import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def make_settings(tmp_path):
    """Isolated DB file + storage dir per test. Inline runner => deterministic by default."""

    def _make(**overrides):
        values = dict(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            storage_dir=tmp_path / "storage",
            max_recipients_per_job=50,
            worker_threads=2,
            run_inline=True,
        )
        values.update(overrides)
        return Settings(**values)

    return _make


@pytest.fixture
def make_client(make_settings):
    """Factory: make_client(generator=..., run_inline=...) -> TestClient."""

    def _make(generator=None, **settings_overrides):
        app = create_app(make_settings(**settings_overrides), generator=generator)
        return TestClient(app)

    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def good_recipients(n=3):
    return [{"name": f"Person {i}", "email": f"person{i}@example.com"} for i in range(1, n + 1)]


@pytest.fixture
def payload():
    return {
        "course_title": "Python Bootcamp",
        "issuer_name": "Acme Academy",
        "issue_date": "2026-10-01",
        "recipients": good_recipients(3),
    }
