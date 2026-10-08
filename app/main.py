"""FastAPI application + routes."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile
from contextlib import asynccontextmanager
from functools import partial
from typing import Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .config import Settings
from .db import make_engine, make_session_factory
from .generator import CertificateGenerator, generate_certificate_pdf
from .models import Base, Certificate, CertificateStatus, Job, JobStatus
from .processor import process_job
from .runner import BackgroundRunner, InlineRunner
from .schemas import CertificateList, CertificateOut, JobCreate, JobOut
from .services import create_job, get_counts

logger = logging.getLogger(__name__)

FINISHED = {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_ERRORS.value, JobStatus.FAILED.value}


def create_app(settings: Settings | None = None, generator: CertificateGenerator | None = None) -> FastAPI:
    """App factory. Everything environment-specific is injected, which is what makes the
    test-suite possible (temp DB, temp storage, inline runner, a deliberately failing generator)."""
    settings = settings or Settings()
    generator = generator or generate_certificate_pdf

    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    process = partial(process_job, session_factory, generator, settings.storage_dir)
    runner = InlineRunner(process) if settings.run_inline else BackgroundRunner(process, settings.worker_threads)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Crash recovery: any job left pending/processing by a previous run is resumed.
        # process_job only touches `pending` certificates, so finished work is not redone.
        with session_factory() as db:
            stuck = db.execute(
                select(Job.id).where(Job.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value]))
            ).scalars().all()
        for job_id in stuck:
            logger.info("Resuming unfinished job %s", job_id)
            runner.submit(job_id)
        yield
        runner.shutdown()  # wait for in-flight work so we don't kill a half-written job
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description="Submit a list of recipients, get PDF certificates back.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory

    def get_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    # ------------------------------------------------------------------ helpers
    def job_to_out(request: Request, db: Session, job: Job) -> JobOut:
        counts = get_counts(db, job.id)
        done = counts.succeeded + counts.failed
        has_downloads = job.status in {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_ERRORS.value}
        return JobOut(
            id=job.id,
            status=job.status,
            course_title=job.course_title,
            issuer_name=job.issuer_name,
            issue_date=job.issue_date,
            counts=counts,
            progress_percent=round(done / counts.total * 100, 1) if counts.total else 100.0,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            certificates_url=str(request.url_for("list_certificates", job_id=job.id)),
            download_all_url=str(request.url_for("download_job_zip", job_id=job.id)) if has_downloads else None,
        )

    def cert_to_out(request: Request, cert: Certificate) -> CertificateOut:
        ok = cert.status == CertificateStatus.SUCCEEDED.value
        return CertificateOut(
            id=cert.id,
            row_index=cert.row_index,
            recipient_name=cert.recipient_name,
            recipient_email=cert.recipient_email,
            status=cert.status,
            error=cert.error,
            download_url=str(request.url_for("download_certificate", certificate_id=cert.id)) if ok else None,
        )

    def get_job_or_404(db: Session, job_id: str) -> Job:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    # ------------------------------------------------------------------- routes
    @app.post("/jobs", response_model=JobOut, status_code=202)
    def submit_job(payload: JobCreate, request: Request, response: Response, db: Session = Depends(get_db)):
        """Create a generation job. Returns 202 immediately; poll GET /jobs/{id}."""
        if len(payload.recipients) > settings.max_recipients_per_job:
            raise HTTPException(
                status_code=422,
                detail=f"Too many recipients: {len(payload.recipients)} (max {settings.max_recipients_per_job} per job)",
            )
        job = create_job(db, payload)
        runner.submit(job.id)
        db.refresh(job)  # inline runner may already have finished it
        response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
        return job_to_out(request, db, job)

    @app.get("/jobs/{job_id}", response_model=JobOut, name="get_job")
    def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
        """Status + progress of a job."""
        return job_to_out(request, db, get_job_or_404(db, job_id))

    @app.get("/jobs/{job_id}/certificates", response_model=CertificateList, name="list_certificates")
    def list_certificates(
        job_id: str,
        request: Request,
        status: Literal["pending", "succeeded", "failed"] | None = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_db),
    ):
        """Per-recipient results (paginated). Filter with ?status=failed to see only errors."""
        get_job_or_404(db, job_id)
        query = select(Certificate).where(Certificate.job_id == job_id)
        if status:
            query = query.where(Certificate.status == status)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(query.order_by(Certificate.row_index).limit(limit).offset(offset)).all()
        return CertificateList(
            total=total, limit=limit, offset=offset, items=[cert_to_out(request, c) for c in rows]
        )

    @app.get("/certificates/{certificate_id}/download", name="download_certificate")
    def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
        """Download one certificate as a PDF."""
        cert = db.get(Certificate, certificate_id)
        if cert is None:
            raise HTTPException(status_code=404, detail="Certificate not found")
        if cert.status != CertificateStatus.SUCCEEDED.value or not cert.file_path:
            raise HTTPException(status_code=409, detail=f"Certificate is not available (status: {cert.status})")
        path = settings.storage_dir / cert.file_path
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Certificate file is missing from storage")
        return FileResponse(path, media_type="application/pdf", filename=f"certificate_{_safe(cert.recipient_name)}.pdf")

    @app.get("/jobs/{job_id}/download", name="download_job_zip")
    def download_job_zip(job_id: str, db: Session = Depends(get_db)):
        """Download every successful certificate of a finished job as one ZIP."""
        job = get_job_or_404(db, job_id)
        if job.status not in {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_ERRORS.value}:
            raise HTTPException(status_code=409, detail=f"Job has nothing to download (status: {job.status})")
        certs = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCEEDED.value)
            .order_by(Certificate.row_index)
        ).all()

        # Build on disk (not in RAM) so a 1000-certificate job doesn't balloon memory,
        # and delete the temp file once the response has been sent.
        fd, tmp_name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
            for c in certs:
                src = settings.storage_dir / c.file_path
                if src.is_file():
                    zf.write(src, arcname=f"{c.row_index + 1:04d}_{_safe(c.recipient_name)}.pdf")
        return FileResponse(
            tmp_name,
            media_type="application/zip",
            filename=f"certificates_{job_id}.zip",
            background=BackgroundTask(os.remove, tmp_name),
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def _safe(name: str | None) -> str:
    """Filesystem/header-safe version of a recipient name (used ONLY for download filenames)."""
    # No dots allowed at all: rules out "..", hidden files and odd extensions in one stroke.
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name or "recipient").strip("_")
    return cleaned[:60] or "recipient"


# Run with:  uvicorn app.main:create_app --factory
# (a factory, not a module-level `app`, so importing this module has no side effects
#  such as creating a database file - important for tests.)
