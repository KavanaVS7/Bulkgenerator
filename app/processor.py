"""The worker: turns a job's pending certificates into PDFs.

Key properties
  * Failure isolation - every certificate has its own try/except.
  * Incremental commits - one commit per certificate, so progress is visible to pollers
    and a crash loses at most the certificate being worked on.
  * Idempotent / resumable - only `pending` rows are processed, so re-running a job after
    a crash continues where it stopped and never regenerates finished work.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .generator import CertificateGenerator
from .models import Certificate, CertificateStatus, Job, JobStatus, utcnow
from .services import get_counts

logger = logging.getLogger(__name__)
MAX_ERROR_LEN = 500


def process_job(
    session_factory: sessionmaker[Session],
    generator: CertificateGenerator,
    storage_dir: Path,
    job_id: str,
) -> None:
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.warning("process_job: job %s not found", job_id)
            return

        job.status = JobStatus.PROCESSING.value
        job.started_at = job.started_at or utcnow()
        db.commit()

        pending_ids = db.execute(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING.value)
            .order_by(Certificate.row_index)
        ).scalars().all()

        for cert_id in pending_ids:
            _process_one(db, generator, storage_dir, job, cert_id)

        _finalize(db, job)


def _process_one(db: Session, generator: CertificateGenerator, storage_dir: Path, job: Job, cert_id: str) -> None:
    cert = db.get(Certificate, cert_id)
    rel_path = f"{job.id}/{cert.id}.pdf"  # built from our UUIDs only -> no path traversal
    try:
        generator(cert.recipient_name, job.course_title, job.issuer_name, job.issue_date, storage_dir / rel_path)
        cert.status = CertificateStatus.SUCCEEDED.value
        cert.file_path = rel_path
        cert.error = None
    except Exception as exc:  # noqa: BLE001 - deliberately broad: ANY failure must stay isolated
        logger.exception("Certificate %s (job %s) failed", cert.id, job.id)
        cert.status = CertificateStatus.FAILED.value
        cert.error = f"Generation failed - {type(exc).__name__}: {exc}"[:MAX_ERROR_LEN]
        cert.file_path = None
    cert.completed_at = utcnow()
    db.commit()


def _finalize(db: Session, job: Job) -> None:
    counts = get_counts(db, job.id)
    if counts.failed == 0:
        job.status = JobStatus.COMPLETED.value
    elif counts.succeeded == 0:
        job.status = JobStatus.FAILED.value
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS.value
    job.completed_at = utcnow()
    db.commit()
