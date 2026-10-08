"""Business logic that is independent of HTTP: creating jobs, counting progress."""
from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Certificate, CertificateStatus, Job, JobStatus
from .schemas import JobCounts, JobCreate, RecipientIn

_recipient_adapter = TypeAdapter(RecipientIn)


def validate_recipient(raw: Any) -> tuple[RecipientIn | None, str | None]:
    """Return (recipient, None) if valid, else (None, human-readable error)."""
    if not isinstance(raw, dict):
        return None, "recipient: must be an object with 'name' and 'email'"
    try:
        return _recipient_adapter.validate_python(raw), None
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "recipient"
            parts.append(f"{field}: {err['msg']}")
        return None, "; ".join(parts)


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist a Job + one Certificate row per recipient, in ONE transaction.

    Valid recipients start as `pending`; invalid ones are stored immediately as `failed`
    with the reason, so the client sees every row it submitted in the results.
    """
    job = Job(
        course_title=payload.course_title,
        issuer_name=payload.issuer_name,
        issue_date=payload.issue_date,
        status=JobStatus.PENDING.value,
    )
    db.add(job)

    for index, raw in enumerate(payload.recipients):
        recipient, error = validate_recipient(raw)
        if recipient is not None:
            cert = Certificate(
                job=job,
                row_index=index,
                recipient_name=recipient.name,
                recipient_email=str(recipient.email),
                raw_input=raw,
                status=CertificateStatus.PENDING.value,
            )
        else:
            cert = Certificate(
                job=job,
                row_index=index,
                # Best-effort echo so the client can recognise the row (never trusted).
                recipient_name=str(raw.get("name"))[:200] if isinstance(raw, dict) and raw.get("name") else None,
                recipient_email=str(raw.get("email"))[:320] if isinstance(raw, dict) and raw.get("email") else None,
                raw_input=raw,
                status=CertificateStatus.FAILED.value,
                error=f"Validation failed - {error}",
            )
        db.add(cert)

    db.commit()  # must be committed BEFORE the worker is told about it
    return job


def get_counts(db: Session, job_id: str) -> JobCounts:
    """Progress is computed from the certificates table (single GROUP BY), not stored in
    counter columns. Counters would need read-modify-write updates from several threads
    and could drift; deriving them can never be wrong."""
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    by_status = {status: n for status, n in rows}
    pending = by_status.get(CertificateStatus.PENDING.value, 0)
    succeeded = by_status.get(CertificateStatus.SUCCEEDED.value, 0)
    failed = by_status.get(CertificateStatus.FAILED.value, 0)
    return JobCounts(total=pending + succeeded + failed, pending=pending, succeeded=succeeded, failed=failed)
