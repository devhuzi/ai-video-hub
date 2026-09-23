import time

import pytest
from starlette.requests import Request

from app import auth, config
from app.main import startup

from .conftest import PASSWORD


def test_health_is_public(client):
    resp = client.get("/api/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize("method,path", [
    ("get", "/api/pipelines"),
    ("get", "/api/pipelines/abc"),
    ("post", "/api/pipelines"),
    ("post", "/api/script-pipelines"),
    ("delete", "/api/pipelines/abc"),
    ("get", "/api/setup"),
    ("get", "/api/llm/models"),
    ("get", "/api/images/models"),
    ("get", "/api/tts/voices"),
    ("get", "/api/estimate?kind=pack"),
    ("post", "/api/uploads/logo"),
    ("get", "/api/uploads/0123456789abcdef0123456789abcdef"),
    ("get", "/api/pipelines/abc/final-video"),
    ("post", "/api/pipelines/abc/regenerate"),
])
def test_protected_routes_require_token(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}


def test_login_ok_and_token_works(client):
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_at"] > time.time() + 6 * 24 * 3600
    ok = client.get("/api/pipelines", headers={"Authorization": f"Bearer {body['token']}"})
    assert ok.status_code == 200


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={"password": "nope"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Wrong password"}


def test_verify_alias_returns_token(client):
    resp = client.post("/api/auth/verify", json={"password": PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True
    assert auth.verify_token(resp.json()["token"])


def test_tampered_expired_and_foreign_tokens_rejected(client, monkeypatch):
    good = auth.issue_token()["token"]
    payload, sig = good.split(".")
    assert not auth.verify_token(payload + "." + sig[:-2] + "AA")
    assert not auth.verify_token("garbage")
    assert not auth.verify_token(auth.issue_token(now=time.time() - 8 * 24 * 3600)["token"])
    # Changing the password invalidates existing sessions.
    monkeypatch.setattr(config, "APP_PASSWORD", "another-password")
    assert not auth.verify_token(good)
    assert client.get("/api/pipelines", headers={"Authorization": f"Bearer {good}"}).status_code == 401


def test_session_secret_overrides_password_key(monkeypatch):
    monkeypatch.setattr(config, "SESSION_SECRET", "s3cret")
    tok = auth.issue_token()["token"]
    monkeypatch.setattr(config, "APP_PASSWORD", "changed")
    assert auth.verify_token(tok)


def test_query_token_accepted_for_media(client, token):
    assert client.get(f"/api/pipelines?token={token}").status_code == 200


def test_login_rate_limit(client):
    for _ in range(config.LOGIN_MAX_FAILURES):
        assert client.post("/api/auth/login", json={"password": "bad"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"password": PASSWORD})
    assert blocked.status_code == 429
    assert "detail" in blocked.json()


def _request(peer: str, xff: str = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_client_ip_trusts_forwarded_for_only_from_loopback():
    assert auth.client_ip(_request("127.0.0.1", "6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert auth.client_ip(_request("::1", "203.0.113.9")) == "203.0.113.9"
    assert auth.client_ip(_request("198.51.100.4", "203.0.113.9")) == "198.51.100.4"
    assert auth.client_ip(_request("127.0.0.1")) == "127.0.0.1"


def test_empty_password_never_authenticates(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    assert not auth.check_password("")


async def test_startup_refuses_without_app_password(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    with pytest.raises(RuntimeError, match="APP_PASSWORD"):
        await startup()


def test_no_cors_headers_by_default(client, token):
    resp = client.get("/api/pipelines", headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
