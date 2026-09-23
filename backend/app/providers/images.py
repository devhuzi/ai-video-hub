"""Image generation.

Prompt packs: the chosen provider first, then the others in the order
SnapGen → Kie.ai → fal.ai, skipping providers without an API key. Every
provider receives the previous image as a reference for visual consistency.

Script Studio: one image per scene from the selected model (a fal endpoint,
SnapGen or Kie.ai), text-to-image.
"""
import asyncio
import re
from typing import Optional, Tuple

import database as db

from .. import config
from ..pipelines import runner
from ..pipelines.common import add_log
from ..pipelines.runner import PipelineInterrupt
from . import fal, kie, registry, snapgen

_NEGATIVE_LABEL = re.compile(r"(?:^[ \t]*|(?<=[.!?])[ \t]+)NEGATIVE[ \t]*:")
_SECTION_LABEL = re.compile(r"^[ \t]*[A-Z][A-Z0-9 _/&-]*[ \t]*:")


def strip_negative_blocks(prompt: str) -> str:
    """Remove `NEGATIVE:` blocks from a prompt, conservatively.

    Image models read every word as something to draw, so "NEGATIVE: no split
    image" tends to *produce* split images. Semantics:

    * Only the uppercase label `NEGATIVE:` counts, and only at the start of a
      line or right after a sentence end (`.`, `!`, `?` + space). Lowercase
      "negative:" or a mid-sentence "a NEGATIVE: film" is left alone.
    * If the label has text after it on the same line, the block runs to the
      end of that line only. Text on the following lines is kept, so
      "Kitchen. NEGATIVE: no blur. Camera locked." keeps just "Kitchen.", while
      "Kitchen.\\nNEGATIVE: no blur.\\nCamera locked." keeps both other lines.
    * If the label ends its line (a list follows), the block also swallows the
      following non-blank lines until a blank line or the next labelled
      section such as `LIGHTING:`.
    """
    out_lines = []
    in_list_block = False
    for line in prompt.split("\n"):
        if in_list_block:
            ends_block = not line.strip() or (
                _SECTION_LABEL.match(line) and not _NEGATIVE_LABEL.match(line))
            if not ends_block:
                continue
            in_list_block = False
        m = _NEGATIVE_LABEL.search(line)
        if m:
            kept = line[:m.start()].rstrip()
            if not line[m.end():].strip():
                in_list_block = True
            if kept:
                out_lines.append(kept)
            continue
        out_lines.append(line)
    cleaned = "\n".join(out_lines)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


IMAGE_POSITIVE_SUFFIX = (
    " This is a single continuous photograph that fills the entire frame edge to edge. "
    "The scene flows as one seamless, unified view captured from a single camera perspective "
    "across the whole canvas."
)


FALLBACK_ORDER = ("snapgen", "kie", "fal")


def _log_fn(pipeline_id: str):
    async def log(msg: str):
        await add_log(pipeline_id, msg)
    return log


async def _mark_done(pipeline_id: str, index: int, url: str, service: str):
    await db.update_image(pipeline_id, index, {"status": "completed", "url": url, "service": service,
                                               "completed_at": db.now_iso(), "updated_at": db.now_iso()})


async def _try_snapgen(pipeline_id: str, index: int, step: str, prompt: str, reference_url: Optional[str],
                       aspect_ratio: str, errors: list) -> Optional[Tuple[str, str]]:
    gen_uuid = None
    runner.checkpoint()
    try:
        await add_log(pipeline_id, f"[{step}] SnapGen (nano-banana-2) - Submitting...")
        gen_uuid = await snapgen.submit_image(prompt, reference_url, aspect_ratio)
        await add_log(pipeline_id, f"[{step}] SnapGen submitted (uuid: {gen_uuid}). Waiting up to 15 min...")
        await db.update_image(pipeline_id, index, {"snapgen_uuid": gen_uuid, "status": "generating",
                                                   "service": "snapgen", "updated_at": db.now_iso()})
        try:
            completed = await snapgen.wait_for(gen_uuid, pipeline_id, step, max_wait=config.SNAPGEN_IMAGE_WAIT)
        except TimeoutError:
            await add_log(pipeline_id, f"[{step}] SnapGen timeout after 15m. Waiting 5m for a final re-check...")
            await asyncio.sleep(300)
            runner.check_cancelled()
            completed = await snapgen.check_status(gen_uuid)
            if completed.get("status") != snapgen.STATUS_DONE:
                raise snapgen.SnapGenError("SnapGen timeout + final re-check not complete")
        img_url = snapgen.extract_image_url(completed)
        if not img_url:
            raise snapgen.SnapGenError("SnapGen returned no image URL")
        await add_log(pipeline_id, f"[{step}] SnapGen SUCCESS: {img_url}")
        await _mark_done(pipeline_id, index, img_url, "snapgen")
        return img_url, gen_uuid
    except PipelineInterrupt:
        raise
    except Exception as e:
        errors.append(f"SnapGen: {e}")
        await add_log(pipeline_id, f"[{step}] SnapGen FAILED: {e}")
        await db.update_image(pipeline_id, index, {"snapgen_error": str(e), "snapgen_uuid": gen_uuid,
                                                   "updated_at": db.now_iso()})
        return None


async def _try_kie(pipeline_id: str, index: int, step: str, prompt: str, reference_url: Optional[str],
                   aspect_ratio: str, errors: list) -> Optional[Tuple[str, str]]:
    attempts = 3
    for attempt in range(1, attempts + 1):
        runner.checkpoint()
        try:
            await add_log(pipeline_id, f"[{step}] Kie.ai attempt {attempt}/{attempts} - Submitting...")
            task_id = await kie.submit_image(prompt, reference_url, aspect_ratio)
            await add_log(pipeline_id, f"[{step}] Kie.ai submitted (taskId: {task_id}). Waiting up to 10 min...")
            await db.update_image(pipeline_id, index, {"kie_task_id": task_id, "status": "generating_backup",
                                                       "service": "kie", "updated_at": db.now_iso()})
            img_url = await kie.wait_for(task_id, pipeline_id, f"{step} kie attempt {attempt}")
            await add_log(pipeline_id, f"[{step}] Kie.ai SUCCESS on attempt {attempt}: {img_url}")
            await _mark_done(pipeline_id, index, img_url, "kie")
            return img_url, task_id
        except PipelineInterrupt:
            raise
        except Exception as e:
            msg = str(e)
            errors.append(f"Kie.ai attempt {attempt}: {msg}")
            await db.update_image(pipeline_id, index, {"kie_error": msg, "updated_at": db.now_iso()})
            if isinstance(e, kie.KieCreditError) or kie.is_credit_error(msg):
                await add_log(pipeline_id, f"[{step}] Kie.ai out of credits — moving on.")
                return None
            await add_log(pipeline_id, f"[{step}] Kie.ai attempt {attempt}/{attempts} FAILED: {msg}")
            if attempt < attempts:
                await asyncio.sleep(5)
    return None


async def _try_fal(pipeline_id: str, index: int, step: str, prompt: str, reference_url: Optional[str],
                   aspect_ratio: str, errors: list) -> Optional[str]:
    attempts = 2
    for attempt in range(1, attempts + 1):
        runner.checkpoint()
        try:
            await add_log(pipeline_id, f"[{step}] fal.ai attempt {attempt}/{attempts} - Submitting...")
            await db.update_image(pipeline_id, index, {"status": "generating_backup", "service": "fal",
                                                       "updated_at": db.now_iso()})
            img_url = await fal.generate_pack_image(prompt, reference_url, aspect_ratio, step, _log_fn(pipeline_id))
            await add_log(pipeline_id, f"[{step}] fal.ai SUCCESS on attempt {attempt}: {img_url}")
            await _mark_done(pipeline_id, index, img_url, "fal")
            return img_url
        except PipelineInterrupt:
            raise
        except Exception as e:
            errors.append(f"fal.ai attempt {attempt}: {e}")
            await add_log(pipeline_id, f"[{step}] fal.ai attempt {attempt}/{attempts} FAILED: {e}")
            if attempt < attempts:
                await asyncio.sleep(5)
    return None


async def generate_image_with_fallback(index: int, prompt: str, reference_url: Optional[str], pipeline_id: str,
                                       aspect_ratio: str = "16:9", preferred_service: str = "snapgen"):
    """Returns (url, service, snapgen_uuid, kie_task_id). Raises if every provider fails.

    PipelineCancelled / PipelinePaused always propagate — they never trigger a fallback.
    """
    step = f"Image {index + 1}"
    prompt = strip_negative_blocks(prompt) + IMAGE_POSITIVE_SUFFIX
    preferred = registry.normalize_service(preferred_service)
    order = [preferred] + [s for s in FALLBACK_ORDER if s != preferred]
    errors: list = []

    for n, service in enumerate(order):
        label = registry.LABELS[service]
        # Unconfigured providers are skipped outright: calling them with an empty key only
        # produces confusing auth errors and burns minutes of retries.
        if not registry.configured(service):
            errors.append(f"{label}: {registry.ENV_VARS[service]} not set")
            await add_log(pipeline_id, f"[{step}] {label} skipped — {registry.ENV_VARS[service]} not set")
            continue
        if n > 0:
            await add_log(pipeline_id, f"[{step}] Falling back to {label}...")
        if service == "snapgen":
            got = await _try_snapgen(pipeline_id, index, step, prompt, reference_url, aspect_ratio, errors)
            if got:
                return got[0], "snapgen", got[1], None
        elif service == "kie":
            got = await _try_kie(pipeline_id, index, step, prompt, reference_url, aspect_ratio, errors)
            if got:
                return got[0], "kie", None, got[1]
        else:
            url = await _try_fal(pipeline_id, index, step, prompt, reference_url, aspect_ratio, errors)
            if url:
                return url, "fal", None, None

    await db.update_image(pipeline_id, index, {"status": "failed", "updated_at": db.now_iso()})
    raise RuntimeError(f"[{step}] All image generation attempts failed: {' | '.join(errors)}")


async def generate_scene_image(model: str, prompt: str, aspect_ratio: str, pipeline_id: str, step: str) -> str:
    """One text-to-image generation for Script Studio. `model` is normalised already."""
    provider = registry.scene_model_provider(model)
    if not registry.configured(provider):
        raise RuntimeError(f"{registry.LABELS[provider]} is not configured — set {registry.ENV_VARS[provider]}")
    if provider == "snapgen":
        gen_uuid = await snapgen.submit_image(prompt, None, aspect_ratio)
        completed = await snapgen.wait_for(gen_uuid, pipeline_id, step, max_wait=config.SNAPGEN_IMAGE_WAIT)
        url = snapgen.extract_image_url(completed)
        if not url:
            raise snapgen.SnapGenError("SnapGen returned no image URL")
        return url
    if provider == "kie":
        task_id = await kie.submit_image(prompt, None, aspect_ratio)
        return await kie.wait_for(task_id, pipeline_id, step)
    return await fal.generate_image(model, prompt, aspect_ratio, None, step, _log_fn(pipeline_id))
