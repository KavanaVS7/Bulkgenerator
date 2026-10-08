"""Certificate generation, per-certificate failure handling, crash recovery."""
from datetime import date

from fastapi.testclient import TestClient

from app.generator import generate_certificate_pdf
from app.main import create_app
from app.models import Certificate, CertificateStatus
from app.schemas import JobCreate
from app.services import create_job
from tests.conftest import good_recipients


# ----------------------------------------------------------------- the generator itself
def test_generator_writes_a_valid_pdf(tmp_path):
    out = tmp_path / "sub" / "cert.pdf"  # parent dir does not exist yet
    generate_certificate_pdf("Ada Lovelace", "Analytical Engines 101", "Acme", date(2026, 10, 1), out)

    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert data.rstrip().endswith(b"%%EOF")
    assert not list(tmp_path.rglob("*.tmp"))  # temp file was renamed away


def test_generator_handles_very_long_names(tmp_path):
    out = tmp_path / "long.pdf"
    generate_certificate_pdf("W" * 100, "C" * 200, "Acme", date(2026, 1, 1), out)
    assert out.stat().st_size > 0


def test_generator_handles_unicode_in_text_fields_without_crashing(tmp_path):
    out = tmp_path / "u.pdf"
    # Built-in PDF fonts are Latin-1; accents must work.
    generate_certificate_pdf("José Müller", "Café Management", "Académie", date(2026, 1, 1), out)
    assert out.read_bytes().startswith(b"%PDF")


# ----------------------------------------------------------- end-to-end through the API
def test_job_generates_one_pdf_per_valid_recipient(client, payload, make_settings):
    job = client.post("/jobs", json=payload).json()
    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]

    assert len(items) == 3
    assert all(i["status"] == "succeeded" and i["error"] is None for i in items)
    # files really exist on disk under <storage>/<job_id>/
    pdfs = list((make_settings().storage_dir / job["id"]).glob("*.pdf"))
    assert len(pdfs) == 3


# ------------------------------------------------- one failure must not stop the others
def test_one_certificate_failing_does_not_stop_the_rest(make_client, payload):
    def flaky_generator(name, title, issuer, issue_date, out_path):
        if name == "Person 2":
            raise RuntimeError("disk exploded")
        generate_certificate_pdf(name, title, issuer, issue_date, out_path)

    client = make_client(generator=flaky_generator)
    job = client.post("/jobs", json=payload).json()

    assert job["status"] == "completed_with_errors"
    assert job["counts"] == {"total": 3, "pending": 0, "succeeded": 2, "failed": 1}

    items = {i["recipient_name"]: i for i in client.get(f"/jobs/{job['id']}/certificates").json()["items"]}
    assert items["Person 1"]["status"] == "succeeded"
    assert items["Person 3"]["status"] == "succeeded"  # processed AFTER the failure
    bad = items["Person 2"]
    assert bad["status"] == "failed"
    assert "RuntimeError" in bad["error"] and "disk exploded" in bad["error"]
    assert bad["download_url"] is None
    assert client.get(f"/certificates/{bad['id']}/download").status_code == 409


def test_every_certificate_failing_marks_job_failed(make_client, payload):
    def always_fails(*_args):
        raise ValueError("nope")

    client = make_client(generator=always_fails)
    job = client.post("/jobs", json=payload).json()
    assert job["status"] == "failed"
    assert job["counts"]["failed"] == 3


# ------------------------------------------------------------------- crash recovery
def test_unfinished_job_is_resumed_on_startup_without_redoing_finished_work(make_settings):
    settings = make_settings()
    calls = []

    def counting_generator(name, title, issuer, issue_date, out_path):
        calls.append(name)
        generate_certificate_pdf(name, title, issuer, issue_date, out_path)

    # Simulate "server died mid-job": job is `processing`, 1 of 3 already done.
    first_boot = create_app(settings, generator=counting_generator)
    with first_boot.state.session_factory() as db:
        job = create_job(db, JobCreate(course_title="C", recipients=good_recipients(3)))
        job.status = "processing"
        done = db.query(Certificate).filter_by(job_id=job.id, row_index=0).one()
        done.status = CertificateStatus.SUCCEEDED.value
        done.file_path = "already/done.pdf"
        db.commit()
        job_id = job.id

    # "Restart": the lifespan hook should pick the job up.
    with TestClient(create_app(settings, generator=counting_generator)) as client:
        job = client.get(f"/jobs/{job_id}").json()

    assert job["status"] == "completed"
    assert job["counts"]["succeeded"] == 3
    assert calls == ["Person 2", "Person 3"]  # Person 1 was NOT regenerated
