"""SnapGen API: image generation, Veo 3.1 / Grok video generation and extension.

Multipart requests with an `x-api-key` header; jobs are polled via
GET /history/{uuid}. Docs:
  https://docs.snapgen.ai/resources/0.image-generation
  https://docs.snapgen.ai/resources/0.video-generation (Veo)
  https://docs.snapgen.ai/resources/1.veo-extend
  https://docs.snapgen.ai/resources/3.video-generation-grok
  https://docs.snapgen.ai/resources/4.grok-extend
  https://docs.snapgen.ai/resources/history-apis
  https://docs.snapgen.ai/getting-started/errors
"""
import asyncio
import logging
from typing import Optional

import httpx

from .. import config
from ..pipelines import runner
from ..pipelines.common import add_log
from .http_errors import describe

logger = logging.getLogger(__name__)

POLL_INTERVAL = 15

# History status codes: 1 processing, 2 completed, 3 failed; negative values are
# terminal too (-2 = content policy violation).
STATUS_DONE = 2
STATUS_FAILED = 3
STATUS_POLICY_VIOLATION = -2

# Veo 3.1 / 3.1 Fast are documented as 8-second, 16:9 clips.
VEO_MODEL = "veo-3.1-fast"
VEO_DURATION = "8"
VEO_PORTRAIT_HINT = ("SnapGen's Veo 3.1 models are documented as 16:9 only — use 16:9 or switch "
                     "the video engine to Grok (supports portrait)")


class SnapGenError(RuntimeError):
    pass


def _headers() -> dict:
    return {"x-api-key": config.SNAPGEN_API_KEY}


async def _submit(path: str, data: dict, files=None, hint: str = "") -> str:
    """POST a generation job and return its uuid. Fails fast if no uuid comes back."""
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(f"{config.SNAPGEN_BASE}{path}", headers=_headers(), data=data, files=files)
    if resp.status_code >= 400:
        msg = f"SnapGen {path} {describe(resp)}"
        if hint and 400 <= resp.status_code < 500:
            msg += f". {hint}"
        raise SnapGenError(msg)
    body = resp.json()
    gen_uuid = body.get("uuid") if isinstance(body, dict) else None
    if not gen_uuid:
        raise SnapGenError(f"SnapGen {path} response had no uuid: {str(body)[:300]}")
    return gen_uuid


def _veo_hint(aspect_ratio: str) -> str:
    return VEO_PORTRAIT_HINT if aspect_ratio != "16:9" else ""


async def submit_image(prompt: str, reference_url: Optional[str], aspect_ratio: str) -> str:
    data = {
        "prompt": prompt, "model": "nano-banana-2", "aspect_ratio": aspect_ratio,
        "style": "Photorealistic", "output_format": "jpeg", "resolution": "1K",
    }
    if reference_url:
        data["file_urls"] = reference_url
    return await _submit("/generate_image", data)


async def submit_veo_frames(prompt: str, first_image_url: str, last_image_url: str, aspect_ratio: str) -> str:
    data = {"prompt": prompt, "model": VEO_MODEL, "resolution": "1080p", "duration": VEO_DURATION,
            "aspect_ratio": aspect_ratio, "mode_image": "frame"}
    # Frame mode takes up to two images, in start → end order.
    files = [("ref_images", (None, first_image_url)), ("ref_images", (None, last_image_url))]
    return await _submit("/video-gen/veo", data, files, hint=_veo_hint(aspect_ratio))


async def submit_veo_first(prompt: str, image_url: str, aspect_ratio: str, duration: int) -> str:
    # "frame" mode with a single reference image uses it as the first frame
    # (the older "first" mode is no longer documented).
    data = {"prompt": prompt, "model": VEO_MODEL, "resolution": "1080p", "duration": VEO_DURATION,
            "aspect_ratio": aspect_ratio, "mode_image": "frame"}
    return await _submit("/video-gen/veo", data, [("ref_images", (None, image_url))],
                         hint=_veo_hint(aspect_ratio))


async def submit_grok_first(prompt: str, image_url: str, aspect_ratio: str, duration: int) -> str:
    grok_aspect = "landscape" if aspect_ratio == "16:9" else "portrait"
    data = {"prompt": prompt, "model": "grok-3", "resolution": "720p",
            "aspect_ratio": grok_aspect, "duration": str(duration), "mode": "custom"}
    return await _submit("/video-gen/grok", data, [("file_urls", (None, image_url))])


async def submit_extend(engine: str, prompt: str, ref_history_uuid: str) -> str:
    """Extend a clip from the last frame of the referenced video (engine: 'veo' | 'grok')."""
    return await _submit(f"/video-extend/{engine}", {"prompt": prompt, "ref_history": ref_history_uuid})


async def check_status(gen_uuid: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{config.SNAPGEN_BASE}/history/{gen_uuid}", headers=_headers())
        resp.raise_for_status()
        return resp.json()


def _status_code(result: dict) -> int:
    try:
        return int(result.get("status", 1))
    except (TypeError, ValueError):
        return 1


def failure_message(result: dict) -> Optional[str]:
    """A human-readable reason if the job reached a terminal failure state, else None."""
    status = _status_code(result)
    detail = result.get("error_message") or result.get("error_code") or result.get("status_desc") or ""
    if status == STATUS_POLICY_VIOLATION:
        return ("SnapGen rejected the request for a content policy violation"
                + (f": {detail}" if detail else "") + ". Rephrase the prompt and retry.")
    if status == STATUS_FAILED or status < 0:
        return f"SnapGen generation failed (status {status}): {detail or 'Unknown error'}"
    return None


async def wait_for(gen_uuid: str, pipeline_id: str, step_label: str, max_wait: int) -> dict:
    """Poll until the job finishes. Client errors (4xx except 429) abort immediately."""
    if not gen_uuid:
        raise SnapGenError("Cannot poll SnapGen without a uuid")
    elapsed = 0
    while elapsed < max_wait:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        runner.check_cancelled()
        try:
            result = await check_status(gen_uuid)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if 400 <= code < 500 and code != 429:
                raise SnapGenError(f"SnapGen status check {describe(e.response)}") from e
            await add_log(pipeline_id, f"[{step_label}] SnapGen status HTTP {code}, will retry")
            continue
        except httpx.HTTPError as e:
            await add_log(pipeline_id, f"[{step_label}] SnapGen network error: {e}")
            continue
        status = _status_code(result)
        await add_log(pipeline_id, f"[{step_label}] SnapGen polling... status={status}, "
                                   f"progress={result.get('status_percentage', 0)}%, elapsed={elapsed}s")
        if status == STATUS_DONE:
            return result
        failed = failure_message(result)
        if failed:
            raise SnapGenError(failed)
    raise TimeoutError(f"SnapGen timeout after {max_wait}s for {step_label}")


def extract_image_url(result: dict) -> Optional[str]:
    if result.get("generated_image"):
        img = result["generated_image"][0]
        return img.get("image_url") or img.get("file_download_url")
    return result.get("generate_result")


def extract_video_url(result: dict) -> Optional[str]:
    if result.get("generated_video"):
        return result["generated_video"][0].get("video_url")
    return result.get("generate_result")
