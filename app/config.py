"""Application settings.

Plain dataclass + environment variables. No settings library needed for six values,
and it makes tests trivial: just build a Settings(...) with different values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # Any SQLAlchemy URL works (e.g. postgresql+psycopg://...). SQLite keeps setup at zero.
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'certgen.db'}")
    )
    # Where generated PDFs are written.
    storage_dir: Path = field(
        default_factory=lambda: Path(os.getenv("STORAGE_DIR", str(BASE_DIR / "storage")))
    )
    # Hard cap on recipients per request - protects the server from absurd payloads.
    max_recipients_per_job: int = field(
        default_factory=lambda: int(os.getenv("MAX_RECIPIENTS_PER_JOB", "1000"))
    )
    # Threads used for background generation.
    worker_threads: int = field(default_factory=lambda: int(os.getenv("WORKER_THREADS", "4")))
    # True => run jobs inside the request (used by tests so they are deterministic).
    run_inline: bool = False
