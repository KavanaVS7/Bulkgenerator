"""ORM models: one Job has many Certificates (one row per recipient)."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp. (SQLite drops tzinfo anyway, so we are consistent everywhere.)"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    PENDING = "pending"                          # accepted, not started
    PROCESSING = "processing"                    # worker is running
    COMPLETED = "completed"                      # every certificate succeeded
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some succeeded, some failed
    FAILED = "failed"                            # nothing succeeded


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # The "certificate information" shared by every recipient in the request.
    course_title: Mapped[str] = mapped_column(String(200))
    issuer_name: Mapped[str] = mapped_column(String(200))
    issue_date: Mapped[date] = mapped_column(Date)

    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    # Position in the submitted list: lets the client map results back to their input.
    row_index: Mapped[int] = mapped_column(Integer)

    # Nullable because an invalid recipient may be missing these fields entirely.
    recipient_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Exactly what the client sent, kept for debugging / reporting failures.
    raw_input: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=CertificateStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Path RELATIVE to storage_dir. Built from our own UUIDs, never from user input.
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")

    __table_args__ = (
        # "all certificates of job X with status Y" is the hot query (progress + listing).
        Index("ix_certificates_job_status", "job_id", "status"),
        Index("ix_certificates_job_row", "job_id", "row_index"),
    )
