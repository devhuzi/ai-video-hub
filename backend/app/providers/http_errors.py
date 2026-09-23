"""Readable error text from provider HTTP error responses.

Providers put the useful part of an error in different places. The message we
raise must always carry the HTTP status plus whatever code/message/validation
details the body holds — never just "422 Unprocessable Entity".
"""
import httpx

MAX_RAW = 500


def _validation_items(items: list) -> str:
    parts = []
    for item in items:
        if isinstance(item, dict):
            loc = ".".join(str(x) for x in item.get("loc", ()) if x != "body")
            msg = item.get("msg") or item.get("message") or str(item)
            parts.append(f"{loc}: {msg}" if loc else str(msg))
        else:
            parts.append(str(item))
    return "; ".join(parts)


def body_detail(body) -> str:
    """Pull code + message out of a decoded JSON error body."""
    if isinstance(body, list):
        return _validation_items(body)
    if not isinstance(body, dict):
        return str(body)[:MAX_RAW]
    for key in ("detail", "error"):
        val = body.get(key)
        if isinstance(val, dict):
            code = val.get("error_code") or val.get("code") or val.get("type") or ""
            msg = val.get("error_message") or val.get("message") or val.get("msg") or ""
            meta = val.get("metadata")
            if isinstance(meta, dict) and meta.get("raw"):
                msg = f"{msg} ({str(meta['raw'])[:200]})" if msg else str(meta["raw"])[:200]
            text = " ".join(str(x) for x in (code, msg) if x)
            return (text or str(val))[:MAX_RAW]
        if isinstance(val, list):
            return _validation_items(val)[:MAX_RAW]
        if isinstance(val, str) and val:
            return val[:MAX_RAW]
    msg = body.get("msg") or body.get("message")
    if msg:
        code = body.get("code")
        return (f"{code} {msg}" if code not in (None, "") else str(msg))[:MAX_RAW]
    return str(body)[:MAX_RAW]


def describe(resp: httpx.Response) -> str:
    """'HTTP <status>: <details>' for an error response."""
    try:
        detail = body_detail(resp.json())
    except ValueError:
        detail = (resp.text or "").strip()[:MAX_RAW]
    return f"HTTP {resp.status_code}: {detail or resp.reason_phrase}"
