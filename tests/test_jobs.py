"""Creating a job, input validation, and job status / progress."""
import threading
import time

from tests.conftest import good_recipients


# ------------------------------------------------------------------ creating a job
def test_create_job_returns_202_with_location_and_counts(client, payload):
    r = client.post("/jobs", json=payload)

    assert r.status_code == 202
    body = r.json()
    assert body["id"]
    assert r.headers["Location"].endswith(f"/jobs/{body['id']}")
    assert body["course_title"] == "Python Bootcamp"
    assert body["issuer_name"] == "Acme Academy"
    assert body["counts"]["total"] == 3  # one row per recipient was persisted


def test_create_job_applies_defaults_for_optional_fields(client):
    r = client.post("/jobs", json={"course_title": "X", "recipients": good_recipients(1)})
    assert r.status_code == 202
    assert r.json()["issuer_name"]  # default issuer
    assert r.json()["issue_date"]  # defaults to today


def test_unknown_job_is_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


# ------------------------------------------------------------- request-level validation
def test_empty_recipient_list_is_rejected(client, payload):
    payload["recipients"] = []
    assert client.post("/jobs", json=payload).status_code == 422


def test_missing_course_title_is_rejected(client, payload):
    del payload["course_title"]
    assert client.post("/jobs", json=payload).status_code == 422


def test_blank_course_title_is_rejected(client, payload):
    payload["course_title"] = "   "
    assert client.post("/jobs", json=payload).status_code == 422


def test_bad_issue_date_is_rejected(client, payload):
    payload["issue_date"] = "not-a-date"
    assert client.post("/jobs", json=payload).status_code == 422


def test_too_many_recipients_is_rejected(client, payload):
    payload["recipients"] = good_recipients(51)  # limit is 50 in the test settings
    r = client.post("/jobs", json=payload)
    assert r.status_code == 422
    assert "Too many recipients" in r.json()["detail"]


# ------------------------------------------------------------ recipient-level validation
def test_invalid_recipients_do_not_reject_the_batch(client, payload):
    payload["recipients"] = [
        {"name": "Good One", "email": "good@example.com"},
        {"name": "", "email": "blank-name@example.com"},  # empty name
        {"name": "No Email"},  # missing email
        {"name": "Bad Email", "email": "not-an-email"},  # malformed email
        "just a string",  # not even an object
        {"name": "Good Two", "email": "good2@example.com"},
    ]
    job = client.post("/jobs", json=payload).json()

    assert job["status"] == "completed_with_errors"
    assert job["counts"] == {"total": 6, "pending": 0, "succeeded": 2, "failed": 4}

    failed = client.get(f"/jobs/{job['id']}/certificates", params={"status": "failed"}).json()
    assert [c["row_index"] for c in failed["items"]] == [1, 2, 3, 4]
    assert all(c["error"].startswith("Validation failed") for c in failed["items"])
    assert "email" in failed["items"][1]["error"]  # tells the client WHAT was wrong


def test_all_recipients_invalid_marks_job_failed(client, payload):
    payload["recipients"] = [{"name": "x"}, {"email": "a@b.com"}]
    job = client.post("/jobs", json=payload).json()
    assert job["status"] == "failed"
    assert job["counts"]["failed"] == 2
    assert job["download_all_url"] is None


def test_name_whitespace_is_trimmed(client, payload):
    payload["recipients"] = [{"name": "  Ada Lovelace  ", "email": "ada@example.com"}]
    job = client.post("/jobs", json=payload).json()
    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    assert items[0]["recipient_name"] == "Ada Lovelace"


# --------------------------------------------------------------- status / progress
def test_finished_job_reports_100_percent(client, payload):
    job = client.post("/jobs", json=payload).json()
    fetched = client.get(f"/jobs/{job['id']}").json()
    assert fetched["status"] == "completed"
    assert fetched["progress_percent"] == 100.0
    assert fetched["counts"] == {"total": 3, "pending": 0, "succeeded": 3, "failed": 0}
    assert fetched["completed_at"] is not None
    assert fetched["download_all_url"]


def test_progress_is_visible_while_job_is_running_in_background(make_client, payload):
    """Real background run: the generator blocks on the 2nd certificate so we can observe
    the job mid-flight, then lets it finish."""
    gate = threading.Event()
    calls = []

    def slow_generator(name, title, issuer, issue_date, out_path):
        calls.append(name)
        if len(calls) == 2:
            assert gate.wait(timeout=10), "test never released the gate"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-fake")

    client = make_client(generator=slow_generator, run_inline=False)
    try:
        r = client.post("/jobs", json=payload)
        assert r.status_code == 202  # returned without waiting for generation
        job_id = r.json()["id"]

        # wait until exactly one certificate has finished and the 2nd is blocked
        deadline = time.time() + 10
        while time.time() < deadline:
            mid = client.get(f"/jobs/{job_id}").json()
            if mid["counts"]["succeeded"] == 1 and len(calls) == 2:
                break
            time.sleep(0.05)

        assert mid["status"] == "processing"
        assert mid["counts"] == {"total": 3, "pending": 2, "succeeded": 1, "failed": 0}
        assert mid["progress_percent"] == 33.3
        assert mid["download_all_url"] is None  # not downloadable until finished
        assert client.get(f"/jobs/{job_id}/download").status_code == 409
    finally:
        gate.set()

    deadline = time.time() + 10
    while time.time() < deadline:
        final = client.get(f"/jobs/{job_id}").json()
        if final["status"] == "completed":
            break
        time.sleep(0.05)
    assert final["status"] == "completed"
    assert final["progress_percent"] == 100.0
