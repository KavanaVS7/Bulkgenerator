"""Pydantic schemas: request validation + response shapes."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, StringConstraints

# Our DB stores naive UTC; this renders it as ISO-8601 with a trailing "Z".
UTCDateTime = Annotated[
    datetime, PlainSerializer(lambda d: d.isoformat() + "Z", return_type=str, when_used="json")
]


# ----------------------------------------------------------------------------- requests
class RecipientIn(BaseModel):
    """One recipient. Validated INDIVIDUALLY (see services.create_job), not as part of the
    request body, so that one bad recipient does not reject the entire batch."""

    model_config = ConfigDict(extra="ignore")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    email: EmailStr


class JobCreate(BaseModel):
    """The whole request. Only *request-level* problems fail here (-> HTTP 422)."""

    course_title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    issuer_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ] = "Certificate Issuer"
    issue_date: date = Field(default_factory=date.today)
    # list[Any]: each item is validated later, one by one, so a malformed item
    # (e.g. a bare string or a missing email) is recorded as a failed recipient.
    recipients: Annotated[list[Any], Field(min_length=1)]


# ---------------------------------------------------------------------------- responses
class JobCounts(BaseModel):
    total: int
    pending: int
    succeeded: int
    failed: int


class JobOut(BaseModel):
    id: str
    status: str
    course_title: str
    issuer_name: str
    issue_date: date
    counts: JobCounts
    progress_percent: float  # (succeeded + failed) / total * 100
    created_at: UTCDateTime
    started_at: UTCDateTime | None
    completed_at: UTCDateTime | None
    certificates_url: str
    download_all_url: str | None  # only set once the job has finished with >=1 success


class CertificateOut(BaseModel):
    id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    download_url: str | None  # only for succeeded certificates


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
