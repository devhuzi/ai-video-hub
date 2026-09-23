"""SnapGen API: images (Nano Banana family, Grok Image, GPT Image 2), video
(Veo 3.1 family, Omni Flash, experimental Vela AI), Veo extension, credits.

Multipart requests with an `x-api-key` header; jobs are polled via
GET /history/{uuid}. Docs:
  https://docs.snapgen.ai/resources/0.image-generation (+ /imagen/grok, /imagen/gpt-image-2)
  https://docs.snapgen.ai/resources/0.video-generation (Veo / Omni Flash)
  https://docs.snapgen.ai/resources/1.veo-extend
  https://docs.snapgen.ai/resources/history-apis
  https://docs.snapgen.ai/getting-started/errors
Vela AI (POST /video-gen/meta) is not in the docs; see `_submit_vela`.
"""
import asyncio
import logging
from typing import List, Optional

import httpx

from .. import config
from ..pipelines import runner
from ..pipelines.common import add_log
from . import registry
from .http_errors import describe

logger = logging.getLogger(__name__)

POLL_INTERVAL = 15

# History status codes: 1 processing, 2 completed, 3 failed; negative values are
# terminal too (-2 = content policy violation).
STATUS_DONE = 2
STATUS_FAILED = 3
STATUS_POLICY_VIOLATION = -2

VELA_MODEL = "meta-video"
# Grok Image orientations (documented). Vela reuses them — UNVERIFIED for Vela
# (SnapGen's web app; not in the API docs).
ORIENTATION = {"16:9": "landscape", "9:16": "portrait", "1:1": "square"}
VELA_ORIENTATION = ORIENTATION
VELA_HINT = ("Vela AI isn't in SnapGen's public API docs — the API may not accept it; "
             "use a Veo model.")


class SnapGenError(RuntimeError):
    pass


def _headers() -> dict:
    return {"x-api-key": config.SNAPGEN_API_KEY}


async def _submit(path: str, data: dict, files=None, hint: str = "") -> str:
    """POST a generation job and return its uuid. Fails fast if no uuid comes back.

    Always multipart/form-data, as SnapGen documents — httpx would otherwise send
    a request without files as form-urlencoded.
    """
    parts = [(k, (None, str(v))) for k, v in data.items() if v is not None] + list(files or [])
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(f"{config.SNAPGEN_BASE}{path}", headers=_headers(), files=parts)
    if resp.status_code >= 400:
        msg = f"SnapGen {path} {describe(resp)}"
        if hint and 400 <= resp.status_code < 500:
            msg += f". {hint}"
        raise SnapGenError(msg)
    body = resp.json()
    gen_uuid = body.get("uuid") if isinstance(body, dict) else None
    if not gen_uuid:
        raise SnapGenError(f"SnapGen {path} response had no uuid: {str(body)[:300]}")
    submitted = _credit_value(body)
    if submitted is not None:
        _submitted_credits[gen_uuid] = submitted
    return gen_uuid


# Credits SnapGen reported when a job was submitted, until the job's result is read.
_submitted_credits: dict = {}


def _credit_value(body) -> Optional[float]:
    if not isinstance(body, dict):
        return None
    for key in ("used_credit", "estimated_credit"):
        val = body.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def credits_for(gen_uuid: str, result: Optional[dict] = None) -> Optional[float]:
    """Credits a finished job cost: `used_credit` / `estimated_credit` from its
    history result, else what the submit response reported. None if unknown."""
    submitted = _submitted_credits.pop(gen_uuid, None)
    found = _credit_value(result)
    return found if found is not None else submitted


async def balance() -> Optional[float]:
    """Available SnapGen credits (GET /account → user_credit.available_credit)."""
    async with httpx.AsyncClient(timeout=20) as c:
        resp = await c.get(f"{config.SNAPGEN_BASE}/account", headers=_headers())
    if resp.status_code >= 400:
        raise SnapGenError(f"SnapGen /account {describe(resp)}")
    credit = (resp.json().get("user_credit") or {}).get("available_credit")
    return float(credit) if isinstance(credit, (int, float)) else None


async def submit_image(prompt: str, reference_url: Optional[str], aspect_ratio: str,
                       model: str = "nano-banana-2", reference_uuid: Optional[str] = None) -> str:
    """Start one image on a SnapGen image model (a key of registry.SNAPGEN_IMAGE_MODELS).

    Nano Banana models take the reference as a URL. Grok Image and GPT Image 2
    only take references as a SnapGen history uuid (`ref_history`) or an
    uploaded file, so a reference from another provider is downloaded and uploaded.
    """
    endpoint = registry.SNAPGEN_IMAGE_MODELS[model]["endpoint"]
    if endpoint == "generate_image":
        data = {
            "prompt": prompt, "model": model, "aspect_ratio": aspect_ratio,
            "style": "Photorealistic", "output_format": "jpeg", "resolution": "1K",
        }
        if reference_url:
            data["file_urls"] = reference_url
        return await _submit("/generate_image", data)

    if endpoint == "imagen/grok":
        data = {"prompt": prompt, "orientation": ORIENTATION.get(aspect_ratio, "landscape"),
                "num_result": "1", "mode": "SPEED"}
    else:  # imagen/gpt-image-2
        data = {"prompt": prompt, "mode": "low", "aspect_ratio": aspect_ratio, "resolution": "1K",
                "background": "auto"}
    files = None
    if reference_uuid:
        data["ref_history"] = reference_uuid
    elif reference_url:
        files = [("files", await _download_reference(reference_url))]
    return await _submit(f"/{endpoint}", data, files)


async def _download_reference(url: str) -> tuple:
    """(filename, bytes, content type) of a reference image, for a multipart upload."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        resp = await c.get(url)
    if resp.status_code >= 400:
        raise SnapGenError(f"Could not download the reference image for upload: {describe(resp)}")
    ctype = resp.headers.get("content-type", "").split(";")[0].strip() or "image/jpeg"
    ext = {"image/png": "png", "image/webp": "webp"}.get(ctype, "jpg")
    return f"reference.{ext}", resp.content, ctype


def clip_images(model: str, first_url: str, last_url: Optional[str]) -> List[str]:
    """Images sent for one clip: [start, end] for Veo (exact frames) and Omni Flash
    (references); only the start image for Vela or when there is no end image."""
    mode = registry.VIDEO_MODELS[model]["image_mode"]
    if mode == "start_image" or not last_url:
        return [first_url]
    if mode == "ingredient" and last_url == first_url:
        return [first_url]  # references: the same image twice adds nothing
    return [first_url, last_url]


async def submit_video(model: str, prompt: str, image_urls: List[str], aspect_ratio: str,
                       resolution: Optional[str], duration: int) -> str:
    """Start one clip on the chosen SnapGen video model. Returns the job uuid."""
    if model == "vela":
        return await _submit_vela(prompt, image_urls[0], aspect_ratio, duration)
    spec = registry.VIDEO_MODELS[model]
    data = {"prompt": prompt, "model": model, "resolution": resolution, "duration": str(duration),
            "aspect_ratio": aspect_ratio, "mode_image": spec["image_mode"]}
    # Frame mode: images in start → end order. Ingredient mode: reference images.
    files = [("ref_images", (None, url)) for url in image_urls]
    return await _submit("/video-gen/veo", data, files)


async def _submit_vela(prompt: str, image_url: str, aspect_ratio: str, duration: int) -> str:
    # UNVERIFIED: field names/values come from SnapGen's web app, not its API docs.
    data = {"prompt": prompt, "model": VELA_MODEL, "duration": str(duration),
            "orientation": VELA_ORIENTATION.get(aspect_ratio, "landscape")}
    return await _submit("/video-gen/meta", data, [("file_urls", (None, image_url))], hint=VELA_HINT)


async def submit_extend(prompt: str, ref_history_uuid: str) -> str:
    """Extend a Veo clip from its last frame. Model, aspect ratio and resolution
    are inherited from the referenced clip."""
    return await _submit("/video-extend/veo", {"prompt": prompt, "ref_history": ref_history_uuid})


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
