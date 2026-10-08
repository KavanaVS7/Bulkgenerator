"""Retrieving generated certificates (single PDF, ZIP, listing)."""
import io
import zipfile

from app.generator import generate_certificate_pdf
from tests.conftest import good_recipients


def test_download_single_certificate_returns_a_pdf(client, payload):
    job = client.post("/jobs", json=payload).json()
    item = client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]

    r = client.get(item["download_url"])

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "certificate_Person_1.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")


def test_download_unknown_certificate_is_404(client):
    assert client.get("/certificates/nope/download").status_code == 404


def test_download_zip_contains_only_successful_certificates(make_client, payload):
    def flaky(name, title, issuer, issue_date, out_path):
        if name == "Person 2":
            raise RuntimeError("boom")
        generate_certificate_pdf(name, title, issuer, issue_date, out_path)

    client = make_client(generator=flaky)
    job = client.post("/jobs", json=payload).json()

    r = client.get(job["download_all_url"])

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["0001_Person_1.pdf", "0003_Person_3.pdf"]
        assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_zip_filenames_are_sanitised(client, payload):
    payload["recipients"] = [{"name": "../../etc/passwd", "email": "x@example.com"}]
    job = client.post("/jobs", json=payload).json()
    r = client.get(job["download_all_url"])
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        (name,) = zf.namelist()
    assert name == "0001_etc_passwd.pdf"  # no slashes, no dots, no traversal


def test_zip_of_a_failed_job_is_409(client, payload):
    payload["recipients"] = [{"name": "only bad"}]
    job = client.post("/jobs", json=payload).json()
    assert client.get(f"/jobs/{job['id']}/download").status_code == 409


def test_zip_for_unknown_job_is_404(client):
    assert client.get("/jobs/nope/download").status_code == 404


def test_listing_supports_status_filter_and_pagination(client, payload):
    payload["recipients"] = good_recipients(5) + [{"name": "bad"}]
    job = client.post("/jobs", json=payload).json()
    url = f"/jobs/{job['id']}/certificates"

    page1 = client.get(url, params={"limit": 4}).json()
    page2 = client.get(url, params={"limit": 4, "offset": 4}).json()
    assert page1["total"] == page2["total"] == 6
    assert [i["row_index"] for i in page1["items"]] == [0, 1, 2, 3]
    assert [i["row_index"] for i in page2["items"]] == [4, 5]

    ok = client.get(url, params={"status": "succeeded"}).json()
    assert ok["total"] == 5
    assert client.get(url, params={"status": "failed"}).json()["total"] == 1


def test_listing_rejects_bad_query_params(client, payload):
    job = client.post("/jobs", json=payload).json()
    url = f"/jobs/{job['id']}/certificates"
    assert client.get(url, params={"status": "weird"}).status_code == 422
    assert client.get(url, params={"limit": 0}).status_code == 422
    assert client.get(url, params={"limit": 10_000}).status_code == 422
