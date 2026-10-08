"""How jobs get executed: a small in-process thread pool.

Deliberately behind a tiny interface (`submit(job_id)`) so it can be replaced by
Celery/RQ/SQS later without touching the API code. See README "Design decisions".
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)


class _BaseRunner:
    def __init__(self, process_fn: Callable[[str], None]):
        self._process_fn = process_fn

    def _run(self, job_id: str) -> None:
        # A ThreadPoolExecutor silently swallows exceptions unless someone reads the
        # future - so log here or failures would vanish.
        try:
            self._process_fn(job_id)
        except Exception:  # noqa: BLE001
            logger.exception("Job %s crashed unexpectedly", job_id)

    def submit(self, job_id: str) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        pass


class BackgroundRunner(_BaseRunner):
    """Runs jobs on worker threads; `submit` returns immediately."""

    def __init__(self, process_fn: Callable[[str], None], workers: int = 4):
        super().__init__(process_fn)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="certgen-worker")

    def submit(self, job_id: str) -> None:
        self._pool.submit(self._run, job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class InlineRunner(_BaseRunner):
    """Runs the job synchronously inside submit(). Used by tests for determinism."""

    def submit(self, job_id: str) -> None:
        self._run(job_id)
