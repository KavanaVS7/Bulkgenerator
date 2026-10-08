# Bulk Certificate Generator

A backend API that accepts **one request containing many recipients**, generates a PDF certificate for each
from a single predefined template, tracks progress, and lets the client download the results.

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite (swappable) · ReportLab · pytest

---

## Setup

```bash
cd certgen
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

No database setup is needed: SQLite is created automatically on first start.

## Run

```bash
uvicorn app.main:create_app --factory --reload
```

* API: http://127.0.0.1:8000  ·  Interactive docs (Swagger): http://127.0.0.1:8000/docs

Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certgen.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `STORAGE_DIR` | `./storage` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Per-request cap |
| `WORKER_THREADS` | `4` | Background worker threads |

## Run the tests

```bash
pytest -v
```

Tests use a temporary database and storage folder per test, so they never touch real data.

---

## API usage

### 1. Submit a generation request
```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "course_title": "Cyber Security Fundamentals",
    "issuer_name": "Dayananda Sagar College of Engineering",
    "issue_date": "2026-10-01",
    "recipients": [
      {"name": "Ada Lovelace", "email": "ada@example.com"},
      {"name": "Alan Turing",  "email": "alan@example.com"}
    ]
  }'
```
`issuer_name` and `issue_date` are optional (defaults: "Certificate Issuer", today).
Response is **`202 Accepted`** with the job (including its `id`) and a `Location` header:
```json
{ "id": "3f1c…", "status": "pending",
  "counts": {"total": 2, "pending": 2, "succeeded": 0, "failed": 0},
  "progress_percent": 0.0, "certificates_url": "…/jobs/3f1c…/certificates", "download_all_url": null, … }
```

### 2. Check progress
```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```
`status` is one of `pending → processing → completed | completed_with_errors | failed`.

### 3. See per-recipient results (and why any failed)
```bash
curl "http://127.0.0.1:8000/jobs/<job_id>/certificates"                  # all rows (paginated: limit/offset)
curl "http://127.0.0.1:8000/jobs/<job_id>/certificates?status=failed"    # only the failures
```
Each item has `row_index` (position in your submitted list), `status`, `error`, and a `download_url` when succeeded.

### 4. Retrieve certificates
```bash
curl -OJ http://127.0.0.1:8000/certificates/<certificate_id>/download   # one PDF
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/download                   # ZIP of all successful PDFs
```

### Endpoint summary
| Method & path | Purpose | Notes |
|---|---|---|
| `POST /jobs` | Create a bulk job | 202; 422 for request-level errors |
| `GET /jobs/{id}` | Status & progress | 404 unknown |
| `GET /jobs/{id}/certificates` | Per-recipient results | `status`, `limit` (≤500), `offset` |
| `GET /certificates/{id}/download` | One PDF | 409 if that certificate failed/pending |
| `GET /jobs/{id}/download` | ZIP of successes | 409 until the job has finished with ≥1 success |
| `GET /health` | Liveness | |

---

## Design decisions

**Asynchronous (background) processing.** A request may contain hundreds of recipients; generating them inside
the HTTP request would risk timeouts and tie up a server worker. `POST /jobs` therefore only validates and
stores the job, returns `202`, and an in-process `ThreadPoolExecutor` generates the PDFs. The client polls
`GET /jobs/{id}`. *Trade-off:* a thread pool needs no extra infrastructure (no Redis/broker), which keeps the
project easy to run and review. The runner sits behind a one-method interface (`submit(job_id)`), so replacing it
with Celery/RQ/SQS for multi-server scale only touches `runner.py`. ReportLab is CPU-bound Python, so threads
share one core under the GIL; scaling out means more processes/workers, not more threads.

**Two levels of validation.**
* *Request-level* (empty list, missing/blank course title, bad date, too many recipients) → `422`, nothing created.
* *Recipient-level* (blank name, malformed email, not an object) → the request is still accepted. The bad row is stored
  as `failed` with a readable reason and the valid rows proceed. This satisfies "one bad record must not block others".
  It is why `recipients` is typed `list[Any]` in the request model and each item is validated individually.

**Failure isolation.** Each certificate is generated inside its own `try/except`; the error is saved on that row and
the loop continues. Final job status: all ok → `completed`; mixed → `completed_with_errors`; none ok → `failed`.

**Progress is derived, not stored.** Counts come from a `GROUP BY status` over the certificates table, so they can
never drift out of sync the way counter columns updated by several threads can. Each certificate is committed
individually, so progress is live.

**Resumable / crash-safe.** Only `pending` certificates are processed, and on startup any job still
`pending`/`processing` is re-queued, so a restart continues where it stopped without regenerating finished work.
PDFs are written to a temp file and atomically renamed, so a half-written file is never served.

**Relational schema.** `jobs` (1) → `certificates` (N), with a composite index on `(job_id, status)` for the hot
progress/listing query. Files are stored on disk (`storage/<job_id>/<certificate_id>.pdf`); the DB stores only the
relative path. Paths are built from server-generated UUIDs, never from user input, so path traversal is impossible;
recipient names are sanitised before being used in download filenames.

**App factory + dependency injection.** `create_app(settings, generator)` lets tests inject a temp DB, an inline
runner, and a deliberately failing generator.

### Known limitations / what I'd do next
* No authentication or rate limiting (a real deployment needs both).
* Local-disk storage; production would use S3/GCS with signed URLs.
* Built-in PDF fonts are Latin-1 only (no Devanagari/CJK names); embed a Unicode TTF font to support them.
* No idempotency key on `POST /jobs`; a client retry creates a second job.
* No emailing of certificates; the model already stores `recipient_email` to support it.
* Schema is created with `create_all`; use Alembic migrations once the schema evolves.

## Project layout
```
app/
  main.py        routes + app factory + startup recovery
  schemas.py     request/response models (pydantic)
  models.py      SQLAlchemy models + status enums
  services.py    create_job, recipient validation, progress counts
  processor.py   worker: per-certificate try/except, finalise job
  runner.py      thread-pool runner (+ inline runner for tests)
  generator.py   the single PDF template (ReportLab)
  config.py / db.py
tests/           28 tests: jobs, generation & failures, retrieval
```
