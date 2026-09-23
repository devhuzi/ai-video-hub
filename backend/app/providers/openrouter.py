"""OpenRouter: chat completions for Script Studio and the model catalogue.

Docs: https://openrouter.ai/docs — OpenAI-compatible POST /chat/completions,
public GET /models (per-token USD prices as strings).
"""
import logging
import time
from typing import List, Optional

import httpx

from .. import config
from .http_errors import body_detail, describe

logger = logging.getLogger(__name__)

_models_cache: dict = {"at": 0.0, "data": None}


class OpenRouterError(RuntimeError):
    pass


def _headers() -> dict:
    h = {"Content-Type": "application/json", "X-Title": config.APP_TITLE}
    if config.OPENROUTER_API_KEY:
        h["Authorization"] = f"Bearer {config.OPENROUTER_API_KEY}"
    return h


def _message_text(content) -> str:
    """`message.content` is normally a string; some providers return content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _is_structured_output_rejection(resp: httpx.Response) -> bool:
    # With provider.require_parameters, a model without structured-output support
    # yields "no endpoints found" (404) or a 400 naming response_format.
    if resp.status_code == 404:
        return True
    return resp.status_code == 400 and ("response_format" in resp.text or "json_schema" in resp.text
                                        or "structured" in resp.text)


async def complete(model: str, system: str, user: str, json_schema: Optional[dict] = None,
                   schema_name: str = "result", max_tokens: int = 16000) -> str:
    """Return the assistant text. With `json_schema`, structured output is requested
    and, if the model can't do it, the call is retried once as plain text (the
    caller parses JSON from the text either way)."""
    if not config.OPENROUTER_API_KEY:
        raise OpenRouterError("OPENROUTER_API_KEY is not configured")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_completion_tokens": max_tokens,
    }
    attempts = [body]
    if json_schema:
        structured = {**body,
                      "response_format": {"type": "json_schema",
                                          "json_schema": {"name": schema_name, "strict": True, "schema": json_schema}},
                      "provider": {"require_parameters": True}}
        attempts = [structured, body]

    async with httpx.AsyncClient(timeout=180) as c:
        for i, payload in enumerate(attempts):
            resp = await c.post(f"{config.OPENROUTER_BASE}/chat/completions", headers=_headers(), json=payload)
            if resp.status_code >= 400:
                if i < len(attempts) - 1 and _is_structured_output_rejection(resp):
                    logger.info("OpenRouter model %s rejected structured output; retrying as text", model)
                    continue
                raise OpenRouterError(f"OpenRouter {describe(resp)}")
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise OpenRouterError(f"OpenRouter error: {body_detail(data)}")
            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices:
                raise OpenRouterError(f"OpenRouter response had no choices: {str(data)[:400]}")
            text = _message_text((choices[0].get("message") or {}).get("content"))
            if not text.strip():
                raise OpenRouterError(f"OpenRouter returned an empty completion "
                                      f"(finish_reason={choices[0].get('finish_reason')})")
            return text
    raise OpenRouterError("OpenRouter request failed")  # unreachable


def _per_million(value) -> Optional[float]:
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


async def list_models() -> List[dict]:
    """Text models: [{id, name, prompt_price, completion_price, structured_outputs}].

    Prices are USD per million tokens (OpenRouter reports per-token strings).
    Cached for CATALOG_CACHE_SECONDS.
    """
    now = time.monotonic()
    if _models_cache["data"] is not None and now - _models_cache["at"] < config.CATALOG_CACHE_SECONDS:
        return _models_cache["data"]
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{config.OPENROUTER_BASE}/models", headers=_headers())
    if resp.status_code >= 400:
        raise OpenRouterError(f"OpenRouter models {describe(resp)}")
    out = []
    for m in (resp.json().get("data") or []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        pricing = m.get("pricing") or {}
        params = m.get("supported_parameters") or []
        out.append({
            "id": m["id"], "name": m.get("name") or m["id"],
            "prompt_price": _per_million(pricing.get("prompt")),
            "completion_price": _per_million(pricing.get("completion")),
            "structured_outputs": "structured_outputs" in params or "response_format" in params,
        })
    out.sort(key=lambda m: m["name"].lower())
    _models_cache.update(at=now, data=out)
    return out


def reset_cache():
    _models_cache.update(at=0.0, data=None)
