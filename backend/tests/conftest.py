"""Test setup. Environment is pinned *before* the app is imported so a developer's
real .env (loaded with override=False) can never inject live API keys into tests."""
import os
import shutil
import tempfile

_SESSION_DIR = tempfile.mkdtemp(prefix="videohub-tests-")
for _key in ("SNAPGEN_API_KEY", "GEMINIGEN_API_KEY", "OPENROUTER_API_KEY", "FAL_KEY", "KIE_API_KEY",
             "NEXTCLOUD_URL", "NEXTCLOUD_USERNAME",
             "NEXTCLOUD_PASSWORD", "NOCODB_URL", "NOCODB_API_TOKEN", "NOCODB_BASE_ID", "SESSION_SECRET",
             "CORS_ORIGINS", "SCRIPT_STUDIO_DIR", "DEFAULT_LLM_MODEL"):
    os.environ[_key] = ""
os.environ["APP_PASSWORD"] = "test-password"
os.environ["DATA_DIR"] = _SESSION_DIR

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database as db  # noqa: E402
from app import auth, config  # noqa: E402
from app.main import app  # noqa: E402
from app.pipelines import runner  # noqa: E402
from app.providers import fal, openrouter  # noqa: E402

_RealAsyncClient = httpx.AsyncClient

PASSWORD = "test-password"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Fresh DATA_DIR (database, uploads, work dirs) for every test."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SCRIPT_STUDIO_DIR", tmp_path / "script_studio")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "PACK_WORK_DIR", tmp_path / "pack_work")
    # Dummy keys so "is this provider configured?" checks pass; every provider call is
    # mocked in tests, so these never reach a real API.
    for key in ("SNAPGEN_API_KEY", "OPENROUTER_API_KEY", "FAL_KEY", "KIE_API_KEY"):
        monkeypatch.setattr(config, key, "test-dummy-key")
    fal.reset_cache()
    openrouter.reset_cache()
    db._db = None
    db.set_data_dir(tmp_path)
    auth.reset_all()
    runner._runs.clear()
    yield tmp_path
    runner._runs.clear()


class NetworkBlocked(AssertionError):
    pass


def _refuse(request):
    raise NetworkBlocked(f"Test tried to reach the network: {request.method} {request.url}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every outgoing httpx.AsyncClient request fails unless a test supplies its own transport."""
    def guarded(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(_refuse))
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", guarded)


def mock_http(monkeypatch, handler):
    """Route every httpx.AsyncClient request to `handler(request) -> httpx.Response`."""
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.fixture
async def database(isolated_data_dir):
    conn = await db.get_db()
    yield conn
    await db.close_db()


@pytest.fixture
def started(monkeypatch):
    """Replace the background runner so API tests never execute a pipeline."""
    calls = []

    def fake_start(pipeline_id):
        calls.append(pipeline_id)
        return True

    monkeypatch.setattr(runner, "start_run", fake_start)
    return calls


@pytest.fixture
def client(started):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def token(client):
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture
def authed(client, token):
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
