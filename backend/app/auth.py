"""Password login issuing stateless HMAC-signed bearer tokens, plus a login rate limiter.

Token format: base64url(json{"exp": unix_ts, "n": nonce}) + "." + base64url(hmac_sha256(payload)).
The signing key is SESSION_SECRET when set, otherwise derived from APP_PASSWORD,
so changing the password invalidates every existing session.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request

from . import config


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signing_key() -> bytes:
    if config.SESSION_SECRET:
        return config.SESSION_SECRET.encode("utf-8")
    return hashlib.sha256(b"session-key:" + config.APP_PASSWORD.encode("utf-8")).digest()


def _sign(payload: str) -> str:
    return _b64e(hmac.new(_signing_key(), payload.encode("ascii"), hashlib.sha256).digest())


def issue_token(now: Optional[float] = None) -> dict:
    exp = int((now or time.time()) + config.SESSION_TTL_SECONDS)
    payload = _b64e(json.dumps({"exp": exp, "n": secrets.token_hex(8)}).encode("utf-8"))
    return {"token": f"{payload}.{_sign(payload)}", "expires_at": exp}


def verify_token(token: str, now: Optional[float] = None) -> bool:
    try:
        payload, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        return False
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        exp = int(json.loads(_b64d(payload))["exp"])
    except (ValueError, KeyError, TypeError):
        return False
    return exp > (now or time.time())


def check_password(candidate: str) -> bool:
    if not config.APP_PASSWORD:
        return False  # never authenticate against an empty password
    return hmac.compare_digest(candidate.encode("utf-8"), config.APP_PASSWORD.encode("utf-8"))


async def require_auth(request: Request):
    """Dependency for every protected /api route.

    Accepts `Authorization: Bearer <token>`. Media endpoints are loaded by
    <img>/<video> tags that cannot set headers, so a `?token=` query parameter
    is accepted as well.
    """
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else request.query_params.get("token", "")
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})


# =============================================================
# Login rate limiting (in-memory, per client IP)
# =============================================================

_failures: Dict[str, Deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    """Client IP, trusting X-Forwarded-For only when the direct peer is a local proxy.

    nginx appends the address it saw to X-Forwarded-For, so the last entry is
    the one it vouches for; earlier entries are client-controlled.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        trusted = False
    xff = request.headers.get("x-forwarded-for", "")
    if trusted and xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return peer


def _prune(ip: str, now: float) -> Deque[float]:
    q = _failures.get(ip)
    if q is None:
        return deque()
    while q and now - q[0] > config.LOGIN_WINDOW_SECONDS:
        q.popleft()
    if not q:
        del _failures[ip]  # keep the map from growing with one-off IPs
    return q


def ensure_not_rate_limited(ip: str):
    q = _prune(ip, time.monotonic())
    if len(q) >= config.LOGIN_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again in a few minutes.")


def record_failure(ip: str):
    _failures[ip].append(time.monotonic())


def reset_failures(ip: str):
    _failures.pop(ip, None)


def reset_all():
    _failures.clear()
