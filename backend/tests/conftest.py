import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

WEBHOOK_SECRET = "test-secret"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        github_token="gh-test-token",
        github_webhook_secret=WEBHOOK_SECRET,
        watched_repos="alice/demo",
        db_path=str(tmp_path / "test.db"),
        workspaces_dir=str(tmp_path / "workspaces"),
        artifacts_dir=str(tmp_path / "artifacts"),
        catchup_on_startup=False,
    )


@pytest.fixture
def client(settings):
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_cancelled_runs():
    """The cancel registry is process-wide (run ids are unique in production); isolate tests from each other."""
    from app.pipeline import proc
    proc._cancelled.clear()
    yield
    proc._cancelled.clear()
