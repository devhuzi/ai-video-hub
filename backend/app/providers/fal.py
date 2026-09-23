"""fal.ai queue API: images (nano-banana family and a curated text-to-image set),
ElevenLabs TTS and the pricing API.

Docs: https://fal.ai/docs — `Authorization: Key $FAL_KEY`; submit with
POST https://queue.fal.run/{endpoint_id}, poll the returned `status_url`, then
GET the returned `response_url`.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from .. import config
from ..pipelines import runner
from .http_errors import describe

logger = logging.getLogger(__name__)

POLL_INTERVAL = 3
TTS_ENDPOINT = "fal-ai/elevenlabs/tts/multilingual-v2"

# Pack images: edit variant when there is a reference image, text-to-image otherwise.
PACK_IMAGE_MODEL = "fal-ai/nano-banana-2"
PACK_IMAGE_EDIT_MODEL = "fal-ai/nano-banana-2/edit"

# Curated text-to-image models for Script Studio (id, display name).
SCENE_IMAGE_MODELS: List[Tuple[str, str]] = [
    ("fal-ai/nano-banana-2", "Nano Banana 2"),
    ("fal-ai/nano-banana", "Nano Banana"),
    ("fal-ai/nano-banana-pro", "Nano Banana Pro"),
    ("fal-ai/flux-pro/kontext/text-to-image", "FLUX.1 Kontext [pro]"),
    ("fal-ai/bytedance/seedream/v4/text-to-image", "Seedream 4.0"),
]

_RESOLUTION_MODELS = {"fal-ai/nano-banana-2", "fal-ai/nano-banana-2/edit",
                      "fal-ai/nano-banana-pro", "fal-ai/nano-banana-pro/edit"}
_SEEDREAM_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (2048, 2048)}

LogFn = Optional[Callable[[str], Awaitable[None]]]

_price_cache: Dict[str, Tuple[float, Optional[dict]]] = {}


class FalError(RuntimeError):
    pass


def _headers() -> dict:
    return {"Authorization": f"Key {config.FAL_KEY}", "Content-Type": "application/json"}


async def submit(endpoint_id: str, payload: dict) -> dict:
    """Queue a request. Returns {request_id, status_url, response_url}."""
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(f"{config.FAL_QUEUE_BASE}/{endpoint_id}", headers=_headers(), json=payload)
    if resp.status_code >= 400:
        raise FalError(f"fal {endpoint_id} submit {describe(resp)}")
    body = resp.json()
    request_id = body.get("request_id") if isinstance(body, dict) else None
    if not request_id:
        raise FalError(f"fal {endpoint_id} submit response had no request_id: {str(body)[:300]}")
    # Prefer the returned URLs: sub-path endpoints (…/edit) may be queued under the base app id.
    base = f"{config.FAL_QUEUE_BASE}/{endpoint_id}/requests/{request_id}"
    return {
        "request_id": request_id,
        "status_url": body.get("status_url") or f"{base}/status",
        "response_url": body.get("response_url") or base,
    }


async def wait_for(handle: dict, step_label: str, max_wait: int, log: LogFn = None) -> dict:
    """Poll the status URL until COMPLETED, then fetch and return the result JSON."""
    elapsed = 0.0
    async with httpx.AsyncClient(timeout=30) as c:
        while elapsed < max_wait:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            runner.check_cancelled()
            try:
                resp = await c.get(handle["status_url"], headers=_headers())
            except httpx.HTTPError as e:
                if log:
                    await log(f"[{step_label}] fal network error: {e}")
                continue
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise FalError(f"fal status {describe(resp)}")
            if resp.status_code >= 400:
                continue
            body = resp.json() if resp.content else {}
            status = str(body.get("status", "")).upper()
            if status == "COMPLETED":
                if body.get("error"):
                    raise FalError(f"fal generation failed: {body.get('error')} "
                                   f"({body.get('error_type') or 'error'})")
                result = await c.get(handle["response_url"], headers=_headers())
                if result.status_code >= 400:
                    raise FalError(f"fal result {describe(result)}")
                return result.json()
            if status in ("FAILED", "ERROR", "CANCELLED"):
                raise FalError(f"fal request {status.lower()}: {body.get('error') or body}")
            if log and int(elapsed) % 15 < POLL_INTERVAL:
                await log(f"[{step_label}] fal polling... status={status or 'unknown'}, elapsed={int(elapsed)}s")
    raise TimeoutError(f"fal timeout after {max_wait}s for {step_label}")


async def run(endpoint_id: str, payload: dict, step_label: str, max_wait: int, log: LogFn = None) -> dict:
    handle = await submit(endpoint_id, payload)
    if log:
        await log(f"[{step_label}] fal {endpoint_id} queued (request {handle['request_id']})")
    return await wait_for(handle, step_label, max_wait, log)


def extract_image_url(result: dict) -> Optional[str]:
    images = result.get("images") if isinstance(result, dict) else None
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return first.get("url")
        if isinstance(first, str):
            return first
    image = result.get("image") if isinstance(result, dict) else None
    if isinstance(image, dict):
        return image.get("url")
    return None


def image_input(model: str, prompt: str, aspect_ratio: str, reference_url: Optional[str] = None) -> dict:
    """Request body for a fal image endpoint (shapes differ between model families)."""
    if "seedream" in model:
        w, h = _SEEDREAM_SIZES.get(aspect_ratio, _SEEDREAM_SIZES["1:1"])
        payload = {"prompt": prompt, "image_size": {"width": w, "height": h}, "num_images": 1}
        if reference_url:
            payload["image_urls"] = [reference_url]
        return payload
    payload = {"prompt": prompt, "num_images": 1, "aspect_ratio": aspect_ratio, "output_format": "jpeg"}
    if model in _RESOLUTION_MODELS:
        payload["resolution"] = "1K"
    if reference_url:
        if "kontext" in model:
            payload["image_url"] = reference_url
        else:
            payload["image_urls"] = [reference_url]
    return payload


async def generate_image(model: str, prompt: str, aspect_ratio: str, reference_url: Optional[str] = None,
                         step_label: str = "Image", log: LogFn = None) -> str:
    result = await run(model, image_input(model, prompt, aspect_ratio, reference_url), step_label,
                       config.IMAGE_POLL_TIMEOUT, log)
    url = extract_image_url(result)
    if not url:
        raise FalError(f"fal {model} returned no image URL: {str(result)[:300]}")
    return url


async def generate_pack_image(prompt: str, reference_url: Optional[str], aspect_ratio: str,
                              step_label: str, log: LogFn = None) -> str:
    model = PACK_IMAGE_EDIT_MODEL if reference_url else PACK_IMAGE_MODEL
    return await generate_image(model, prompt, aspect_ratio, reference_url, step_label, log)


async def text_to_speech(text: str, voice: str, log: LogFn = None) -> Tuple[str, object]:
    """ElevenLabs Multilingual v2. Returns (audio_url, raw word timestamps or None)."""
    result = await run(TTS_ENDPOINT, {
        "text": text, "voice": voice, "stability": 0.5, "similarity_boost": 0.75, "timestamps": True,
    }, "TTS", config.TTS_POLL_TIMEOUT, log)
    audio = result.get("audio") if isinstance(result, dict) else None
    url = audio.get("url") if isinstance(audio, dict) else (audio if isinstance(audio, str) else None)
    if not url:
        raise FalError(f"fal TTS returned no audio URL: {str(result)[:300]}")
    return url, result.get("timestamps")


# =============================================================
# Pricing (USD, from fal's pricing API; never estimated locally)
# =============================================================

async def image_prices(endpoint_ids: List[str]) -> Dict[str, float]:
    """USD per image for each endpoint fal reports a per-image price for.

    Cached per endpoint for CATALOG_CACHE_SECONDS. Endpoints without a known
    per-image price are simply absent from the result.
    """
    if not config.FAL_KEY or not endpoint_ids:
        return {}
    now = time.monotonic()
    fresh = {e for e in endpoint_ids
             if e in _price_cache and now - _price_cache[e][0] < config.CATALOG_CACHE_SECONDS}
    todo = [e for e in dict.fromkeys(endpoint_ids) if e not in fresh]
    if todo:
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.get(f"{config.FAL_API_BASE}/models/pricing",
                                   headers={"Authorization": f"Key {config.FAL_KEY}"},
                                   params={"endpoint_id": ",".join(todo[:50])})
            resp.raise_for_status()
            entries = {p.get("endpoint_id"): p for p in (resp.json().get("prices") or []) if isinstance(p, dict)}
            for e in todo:
                _price_cache[e] = (now, entries.get(e))
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            logger.warning("fal pricing lookup failed: %s", e)
            return _cached_prices(endpoint_ids)
    return _cached_prices(endpoint_ids)


def _cached_prices(endpoint_ids: List[str]) -> Dict[str, float]:
    out = {}
    for e in endpoint_ids:
        entry = (_price_cache.get(e) or (0, None))[1]
        if not isinstance(entry, dict):
            continue
        price, unit = entry.get("unit_price"), str(entry.get("unit") or "").lower()
        currency = str(entry.get("currency") or "USD").upper()
        if isinstance(price, (int, float)) and unit.startswith("image") and currency == "USD":
            out[e] = float(price)
    return out


def reset_cache():
    _price_cache.clear()
