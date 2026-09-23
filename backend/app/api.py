"""HTTP routes. Everything under /api requires a bearer token except health and login."""
import asyncio
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

import database as db

from . import auth, config, uploads, views
from .integrations import nextcloud, nocodb
from .models import (LoginRequest, PipelineCreate, RegenerateRequest, RetryRequest,
                     ScriptPipelineCreate, VideoModel, VideoSystem)
from .pipelines import pack, runner
from .pipelines.common import final_video_path, pack_work_dir, script_work_dir
from .providers import fal, kie, openrouter, registry, snapgen, tts

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_auth)])

RETRYABLE = ("failed", "paused", "cancelled")
CANCELLABLE = ("queued", "pending", "running", "paused")
REGENERATABLE = ("completed", "failed", "cancelled", "paused")
STOP_WAIT_SECONDS = 10


# =============================================================
# Public
# =============================================================

@public_router.get("/")
async def health():
    return {"message": "AI Video Production Hub API", "status": "ok"}


@public_router.post("/auth/login")
async def login(data: LoginRequest, request: Request):
    ip = auth.client_ip(request)
    auth.ensure_not_rate_limited(ip)
    if not auth.check_password(data.password):
        auth.record_failure(ip)
        raise HTTPException(status_code=401, detail="Wrong password")
    auth.reset_failures(ip)
    return auth.issue_token()


@public_router.post("/auth/verify")
async def verify(data: LoginRequest, request: Request):
    """Backwards-compatible alias of /auth/login."""
    result = await login(data, request)
    return {"authenticated": True, **result}


# =============================================================
# Helpers
# =============================================================

async def _get_or_404(pipeline_id: str) -> dict:
    p = await db.find_pipeline(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return p


async def _log(pipeline_id: str, message: str):
    await db.push_log(pipeline_id, message)


async def _wait_for_stop(pipeline_id: str):
    ctl = runner.get_control(pipeline_id)
    if ctl and ctl.task and not ctl.task.done():
        await asyncio.wait({ctl.task}, timeout=STOP_WAIT_SECONDS)


def _start(pipeline_id: str):
    if not runner.start_run(pipeline_id):
        raise HTTPException(status_code=409, detail="Pipeline is already running")


# =============================================================
# Pipelines
# =============================================================

@router.get("/pipelines")
async def list_pipelines():
    return [views.list_item(r) for r in await db.list_pipelines_slim()]


@router.post("/pipelines")
async def create_pipeline(data: PipelineCreate):
    # Every video system renders clips through SnapGen, so fail before queueing anything.
    missing = registry.pack_missing_env()
    if missing:
        raise HTTPException(status_code=400, detail=registry.missing_env_message(missing))
    n_img, n_vid = len(data.image_prompts), len(data.video_prompts)
    if n_img >= 2:
        # Frame mode shapes: chain (N-1 videos), chain + hero reveal (N videos),
        # or paired bookends (2N images, N videos).
        if not (n_vid == n_img - 1 or n_vid == n_img or n_img == 2 * n_vid):
            raise HTTPException(status_code=400, detail=(
                "Frame mode needs N images + (N-1 or N) videos, or 2N images + N videos "
                f"(paired first/last frame per scene) — got {n_img} images and {n_vid} videos"))
        detected = "veo31_frame"
    else:
        detected = "veo_extend"
    if data.video_system and data.video_system != detected:
        raise HTTPException(status_code=400, detail=(
            f"video_system mismatch — detected '{detected}' from prompt counts, got '{data.video_system}'"))

    pipeline_id = str(uuid.uuid4())
    now = db.now_iso()
    row = {
        "id": pipeline_id, "room_type": "direct_prompt", "room_name": data.pipeline_name,
        "pipeline_kind": "paste", "status": "queued", "num_images": n_img, "num_videos": n_vid,
        "aspect_ratio": data.aspect_ratio,
        "image_model": data.image_model or registry.default_image_model(),
        "video_model": data.video_model, "video_resolution": data.video_resolution,
        "video_duration": data.video_duration, "video_system": detected, "current_step": "queued", "progress": 0,
        "resume_from_step": "generating_images", "image_prompts": data.image_prompts,
        "video_prompts": data.video_prompts, "image_urls": [], "video_urls": [],
        "nocodb_synced": False, "created_at": now, "updated_at": now,
    }
    if data.ai_model:
        row["ai_model"] = data.ai_model
    await db.insert_pipeline(row)
    for i, prompt in enumerate(data.image_prompts):
        await db.upsert_image(pipeline_id, i, {"prompt": prompt, "status": "pending", "created_at": now, "updated_at": now})
    for i, prompt in enumerate(data.video_prompts):
        await db.upsert_video(pipeline_id, i, {"prompt": prompt, "status": "pending", "created_at": now, "updated_at": now})
    await _log(pipeline_id, f"Pipeline created: {data.pipeline_name} ({n_img} images, {n_vid} videos, "
                            f"{data.aspect_ratio}, {detected}, {data.video_model})")
    nocodb.sync_pipeline(pipeline_id, {
        "PipelineKind": "paste", "PipelineName": data.pipeline_name, "Status": "queued",
        "NumImages": n_img, "NumVideos": n_vid, "AspectRatio": data.aspect_ratio, "CreatedAt": now,
    })
    _start(pipeline_id)
    return await views.pipeline_detail(pipeline_id)


@router.post("/script-pipelines")
async def create_script_pipeline(data: ScriptPipelineCreate):
    # Script Studio needs OpenRouter (LLM), fal or Kie.ai (TTS) and the chosen image model's provider.
    missing = registry.script_missing_env(data.image_gen_model)
    if missing:
        raise HTTPException(status_code=400, detail=registry.missing_env_message(missing))
    if data.watermark_logo_id and uploads.resolve_id(data.watermark_logo_id) is None:
        raise HTTPException(status_code=400, detail="watermark_logo_id does not match an uploaded logo")
    name = data.pipeline_name or f"Script {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    ai_model = data.ai_model or config.DEFAULT_LLM_MODEL
    pipeline_id = str(uuid.uuid4())
    now = db.now_iso()
    await db.insert_pipeline({
        "id": pipeline_id, "room_type": "script", "room_name": name, "pipeline_kind": "script",
        "status": "queued", "num_images": 0, "num_videos": 0, "aspect_ratio": data.aspect_ratio,
        "ai_model": ai_model,
        "image_gen_model": data.image_gen_model, "current_step": "queued", "progress": 0,
        "resume_from_step": "generating_tts", "image_prompts": [], "video_prompts": [],
        "image_urls": [], "video_urls": [], "nocodb_synced": False, "created_at": now, "updated_at": now,
        "script_text": data.script_text, "tts_voice_id": data.tts_voice_id, "tts_model": data.tts_model,
        "watermark_logo_url": data.watermark_logo_id, "global_style": data.global_style,
    })
    await _log(pipeline_id, f"Script pipeline created: {name}")
    nocodb.sync_pipeline(pipeline_id, {
        "PipelineKind": "script", "PipelineName": name, "Status": "queued",
        "AspectRatio": data.aspect_ratio, "AiModel": ai_model, "CreatedAt": now,
    })
    _start(pipeline_id)
    return await views.pipeline_detail(pipeline_id)


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str):
    detail = await views.pipeline_detail(pipeline_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return detail


@router.get("/pipelines/{pipeline_id}/images")
async def get_pipeline_images(pipeline_id: str):
    p = await _get_or_404(pipeline_id)
    return {"images": views.image_items(p, await db.find_images(pipeline_id))}


@router.get("/pipelines/{pipeline_id}/videos")
async def get_pipeline_videos(pipeline_id: str):
    p = await _get_or_404(pipeline_id)
    return {"videos": views.video_items(p, await db.find_videos(pipeline_id))}


@router.post("/pipelines/{pipeline_id}/pause")
async def pause_pipeline(pipeline_id: str):
    p = await _get_or_404(pipeline_id)
    if p["status"] != "running" or not runner.request_pause(pipeline_id):
        raise HTTPException(status_code=409, detail="Can only pause a running pipeline")
    await _log(pipeline_id, "Pause requested by user. Will stop at the next checkpoint.")
    return {"message": "Pause requested — pipeline will stop at the next checkpoint", "pipeline_id": pipeline_id}


def _is_extend(p: dict) -> bool:
    return pack.detect_shape(p.get("num_images") or 0, p.get("num_videos") or 0, p.get("video_system")) == "extend"


def _video_selection(p: dict, video_model: str, resolution: Optional[str] = None,
                     duration: Optional[int] = None) -> dict:
    """Video settings for a new model on an existing pack: the given values, else the
    pack's current ones when the model accepts them, else the model's defaults.
    Raises a 422 when the model can't render this pack (aspect ratio, extend)."""
    current = registry.video_settings({**p, "video_model": video_model})
    chosen = {"video_model": video_model,
              "video_resolution": resolution if resolution is not None else current["video_resolution"],
              "video_duration": duration if duration is not None else current["video_duration"]}
    problem = registry.video_model_problem(video_model, p.get("aspect_ratio") or "16:9",
                                           chosen["video_resolution"], chosen["video_duration"],
                                           extend=_is_extend(p))
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    return chosen


def _retry_updates(p: dict, data: RetryRequest) -> dict:
    """Validated pipeline column updates for the model selections in a retry."""
    updates: dict = {}
    if views.pipeline_kind(p) == "script":
        if data.pack_fields():
            raise HTTPException(status_code=400, detail="Script Studio runs have no video model settings")
        if data.image_model:
            updates["image_gen_model"] = data.image_model
        updates.update(data.script_fields())
        return updates
    if data.script_fields():
        raise HTTPException(status_code=400, detail="ai_model and tts_voice_id only apply to Script Studio runs")
    if data.image_model:
        updates["image_model"] = data.image_model
    if data.pack_fields():
        model = data.video_model or registry.video_settings(p)["video_model"]
        updates.update(_video_selection(p, model, data.video_resolution, data.video_duration))
    return updates


@router.post("/pipelines/{pipeline_id}/retry")
async def retry_pipeline(pipeline_id: str, data: Optional[RetryRequest] = Body(None)):
    """Retry / resume. Optional new model selections apply to every item that
    isn't completed yet; completed items are kept."""
    p = await _get_or_404(pipeline_id)
    if runner.is_active(pipeline_id):
        raise HTTPException(status_code=409, detail="Pipeline is still running or stopping")
    updates = _retry_updates(p, data) if data else {}
    ok = await db.transition_status(pipeline_id, RETRYABLE, {
        "status": "queued", "current_step": "queued", "error_message": None, **updates})
    if not ok:
        raise HTTPException(status_code=409, detail="Can only retry failed, paused or cancelled pipelines")
    # A new pack-level model also replaces one-off per-item choices on unfinished items.
    if "image_model" in updates:
        await db.clear_item_overrides(pipeline_id, "pipeline_images")
    if "video_model" in updates:
        await db.clear_item_overrides(pipeline_id, "pipeline_videos")
    action = "RESUME" if p["status"] == "paused" else "RETRY"
    changed = ", ".join(f"{k}={v}" for k, v in updates.items())
    await _log(pipeline_id, f"{action}: will resume from step '{p.get('resume_from_step')}'"
                            f"{f' with {changed}' if changed else ''}. Queued.")
    _start(pipeline_id)
    return {"message": f"Pipeline queued to resume from '{p.get('resume_from_step')}'", "pipeline_id": pipeline_id}


@router.post("/pipelines/{pipeline_id}/cancel")
async def cancel_pipeline(pipeline_id: str):
    await _get_or_404(pipeline_id)
    ok = await db.transition_status(pipeline_id, CANCELLABLE, {"status": "cancelled", "current_step": "cancelled"})
    if not ok:
        raise HTTPException(status_code=409, detail="Can only cancel queued, pending, running or paused pipelines")
    await _log(pipeline_id, "Pipeline cancelled by user.")
    runner.request_cancel(pipeline_id)
    await _wait_for_stop(pipeline_id)
    return {"message": "Pipeline cancelled", "pipeline_id": pipeline_id}


@router.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(pipeline_id: str):
    p = await _get_or_404(pipeline_id)
    if p["status"] == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running pipeline. Cancel it first.")
    if runner.request_cancel(pipeline_id):  # e.g. still queued behind another pipeline
        await _wait_for_stop(pipeline_id)
    await db.delete_pipeline(pipeline_id)
    for d in (script_work_dir(pipeline_id), pack_work_dir(pipeline_id)):
        await asyncio.to_thread(shutil.rmtree, d, True)
    nocodb.delete_pipeline(pipeline_id)
    return {"message": "Pipeline deleted"}


@router.post("/pipelines/{pipeline_id}/regenerate")
async def regenerate_item(pipeline_id: str, data: RegenerateRequest):
    p = await _get_or_404(pipeline_id)
    if views.pipeline_kind(p) != "pack":
        raise HTTPException(status_code=400, detail="Regenerate is only available for prompt-pack pipelines")
    if p["status"] in ("running", "queued", "pending") or runner.is_active(pipeline_id):
        raise HTTPException(status_code=409, detail="Pipeline is running — pause or cancel it first")
    n_img, n_vid, system = p["num_images"], p["num_videos"], p.get("video_system")
    limit = n_img if data.kind == "image" else n_vid
    if data.index >= limit:
        raise HTTPException(status_code=400, detail=f"{data.kind} index out of range (0..{limit - 1})")
    if data.kind == "image" and data.video_model or data.kind == "video" and data.image_model:
        raise HTTPException(status_code=400, detail="Pick image_model for an image, video_model for a clip")
    if data.video_model:
        if data.index > 0 and _is_extend(p):
            raise HTTPException(status_code=400, detail=(
                "In an extend pack later clips inherit clip 1's model — pick a model when regenerating clip 1"))
        _video_selection(p, data.video_model)  # 422 if it can't render this pack
    if data.kind == "image":
        image_idx = [data.index]
        video_idx = pack.videos_affected_by_image(n_img, n_vid, system, data.index)
    else:
        image_idx = []
        video_idx = pack.videos_affected_by_video(n_vid, system, data.index)

    ok = await db.transition_status(pipeline_id, REGENERATABLE, {
        "status": "queued", "current_step": "queued", "resume_from_step": "generating_images",
        "final_video_url": None, "nextcloud_url": None, "error_message": None,
    })
    if not ok:
        raise HTTPException(status_code=409, detail="Pipeline status changed — try again")
    await db.reset_items(pipeline_id, image_idx, video_idx)
    override = data.image_model or data.video_model
    if override:
        update = db.update_image if data.kind == "image" else db.update_video
        await update(pipeline_id, data.index, {"model_override": override})
    final_video_path(pipeline_id, "pack").unlink(missing_ok=True)
    await _log(pipeline_id, f"REGENERATE {data.kind} {data.index + 1}"
                            f"{f' with {override}' if override else ''}: reset images "
                            f"{[i + 1 for i in image_idx]} and videos {[i + 1 for i in video_idx]}.")
    _start(pipeline_id)
    return {"message": "Regeneration queued", "pipeline_id": pipeline_id,
            "reset_images": image_idx, "reset_videos": video_idx}


@router.get("/pipelines/{pipeline_id}/final-video")
async def get_final_video(pipeline_id: str):
    p = await _get_or_404(pipeline_id)
    path = final_video_path(pipeline_id, views.pipeline_kind(p))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Final video not available on this server")
    filename = f"{nextcloud.slugify(p.get('room_name') or '') or 'video'}.mp4"
    return FileResponse(path, media_type="video/mp4", filename=filename)


# =============================================================
# Setup status, catalogues, voices
# =============================================================

@router.get("/setup")
async def setup_status():
    """Which providers are configured and whether each feature can run."""
    pack_missing = registry.pack_missing_env()
    script_missing = registry.script_missing_env()
    return {
        "providers": {
            "snapgen": registry.configured("snapgen"),
            "openrouter": registry.configured("openrouter"),
            "fal": registry.configured("fal"),
            "kie": registry.configured("kie"),
            "nextcloud": nextcloud.enabled(),
            "nocodb": nocodb.enabled(),
        },
        "env_vars": registry.ENV_VARS,
        "features": {
            "prompt_packs": {"ready": not pack_missing, "missing": pack_missing},
            "script_studio": {"ready": not script_missing, "missing": script_missing},
        },
    }


@router.get("/llm/models")
async def get_llm_models():
    try:
        models = await openrouter.list_models()
    except Exception as e:
        logger.exception("OpenRouter models fetch failed")
        raise HTTPException(status_code=502, detail=f"OpenRouter models fetch failed: {str(e)[:500]}")
    return {"models": models, "count": len(models), "default": config.DEFAULT_LLM_MODEL}


@router.get("/images/models")
async def get_image_models():
    """Image models for prompt packs and Script Studio, in ladder order: SnapGen,
    Kie.ai, then the curated fal.ai models.

    `credits` / `credits_label`: SnapGen's listed credits per image (as listed by
    SnapGen — may change). `price_usd`: fal's per-image price from its pricing
    API, or null. `default` is Script Studio's default, `pack_default` the
    prompt-pack default (first configured provider's default model).
    """
    models = [{"id": f"snapgen/{name}", "name": spec["label"], "provider": "snapgen", "price_usd": None,
               "credits": spec["credits"], "credits_label": spec["credits_label"],
               "configured": registry.configured("snapgen")}
              for name, spec in registry.SNAPGEN_IMAGE_MODELS.items()]
    models += [{"id": f"kie/{name}", "name": spec["label"], "provider": "kie", "price_usd": None,
                "credits": None, "credits_label": None, "configured": registry.configured("kie")}
               for name, spec in registry.KIE_IMAGE_MODELS.items()]
    fal_ids = [m for m, _ in fal.SCENE_IMAGE_MODELS]
    prices = await fal.image_prices(fal_ids)
    models += [{"id": m, "name": name, "provider": "fal", "price_usd": prices.get(m), "credits": None,
                "credits_label": None, "configured": registry.configured("fal")}
               for m, name in fal.SCENE_IMAGE_MODELS]
    return {"models": models, "count": len(models), "default": config.DEFAULT_SCENE_IMAGE_MODEL,
            "pack_default": registry.default_image_model(), "credits_note": registry.CREDITS_NOTE}


@router.get("/video/models")
async def get_video_models():
    """SnapGen video models with the options each one accepts (the UI renders from this).

    `credits` is SnapGen's published credit cost per clip, or null when unpublished.
    """
    models = [{"id": model_id, **spec} for model_id, spec in registry.VIDEO_MODELS.items()]
    return {"models": models, "count": len(models), "default": registry.DEFAULT_VIDEO_MODEL}


_BALANCE_TTL = 60
_balance_cache: dict = {"at": 0.0, "data": None}


async def _balance(provider: str, fetch) -> Optional[float]:
    if not registry.configured(provider):
        return None
    try:
        return await fetch()
    except Exception as e:  # a balance is informational; never fail the request over it
        logger.warning("%s balance lookup failed: %s", registry.LABELS[provider], e)
        return None


@router.get("/balances")
async def get_balances():
    """Remaining credits per provider (null when unconfigured or the lookup failed). Cached 60 s."""
    now = time.monotonic()
    if _balance_cache["data"] is None or now - _balance_cache["at"] > _BALANCE_TTL:
        snap, kie_credits = await asyncio.gather(_balance("snapgen", snapgen.balance),
                                                 _balance("kie", kie.balance))
        _balance_cache.update(at=now, data={"snapgen": snap, "kie": kie_credits})
    return _balance_cache["data"]


@router.get("/tts/voices")
async def get_tts_voices():
    return {"voices": tts.VOICES, "count": len(tts.VOICES), "provider": tts.primary_provider()}


# =============================================================
# Uploads
# =============================================================

@router.post("/uploads/logo")
async def upload_logo(file: UploadFile = File(...)):
    upload_id = await uploads.save_logo(file)
    return {"id": upload_id, "url": f"/api/uploads/{upload_id}"}


@router.get("/uploads/{upload_id}")
async def get_upload(upload_id: str):
    path = uploads.resolve_id(upload_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return FileResponse(path)


# =============================================================
# Cost estimate
# =============================================================

WORDS_PER_SCENE = 12.5  # ~5 s of narration per scene at ~150 words per minute


def _add(counts: dict, provider: str, kind: str, n: int = 1):
    if n:
        counts.setdefault(provider, {}).setdefault(kind, 0)
        counts[provider][kind] += n


@router.get("/estimate")
async def estimate(
    kind: Literal["pack", "script"],
    num_images: int = Query(0, ge=0, le=config.MAX_PROMPTS),
    num_videos: int = Query(0, ge=0, le=config.MAX_PROMPTS),
    video_system: Optional[VideoSystem] = None,
    video_model: VideoModel = registry.DEFAULT_VIDEO_MODEL,
    video_duration: Optional[int] = Query(None, ge=1, le=60),
    image_model: Optional[str] = Query(None, max_length=200),
    first_image_model: Optional[str] = Query(None, max_length=200),  # legacy: a provider name
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9",
    word_count: int = Query(0, ge=0, le=config.MAX_SCRIPT_CHARS),
):
    """Generation counts per provider, SnapGen credits as listed by SnapGen, and a USD
    figure only for what fal's pricing API prices. Kie.ai and OpenRouter costs are
    never guessed."""
    notes: list = []
    counts: dict = {}

    if kind == "pack":
        model = (registry.normalize_image_model(image_model or first_image_model)
                 or registry.default_image_model())
        provider = registry.image_model_provider(model)
        _add(counts, provider, "images", num_images)
        _add(counts, "snapgen", "videos", num_videos)
        spec = registry.VIDEO_MODELS[video_model]
        duration = video_duration if video_duration in spec["durations"] else spec["default_duration"]
        system = video_system or ("veo_extend" if num_images == 1 else "veo31_frame")
        how = "first → last frame" if system == "veo31_frame" else "sequential extend"
        credits = None
        if num_videos:
            credits = num_videos * spec["credits"]
            notes.append(f"{num_videos} clip(s) via {spec['label']} ({how}): {spec['credits_label']} each "
                         f"= ≈ {credits} SnapGen credits ({registry.CREDITS_NOTE}).")
        credits = _add_image_credits(model, num_images, credits, notes)
        # Image 1 has no reference (text-to-image); every later image edits from the previous one.
        fal_models = ([fal.pack_endpoint(model, i > 0) for i in range(num_images)]
                      if provider == "fal" else [])
        usd = await _fal_usd(fal_models, notes)
        notes.append("Retries and provider fallbacks are not included.")
        return {"generations": {"images": num_images, "videos": num_videos, "tts": 0},
                "providers": counts, "snapgen_credits": credits, "video_seconds": num_videos * duration,
                "estimated_usd": usd, "notes": notes}

    scenes = max(1, round(word_count / WORDS_PER_SCENE)) if word_count else 0
    model = registry.normalize_scene_image_model(image_model)
    provider = registry.image_model_provider(model)
    _add(counts, provider, "images", scenes)
    if word_count:
        _add(counts, "openrouter", "llm_calls", 2)
        _add(counts, tts.primary_provider() or "fal", "tts", 1)
    notes.append(f"Scene count is an estimate (~{WORDS_PER_SCENE:g} words per scene); the LLM decides the real split.")
    notes.append("Narration (TTS) and the two OpenRouter LLM calls are not included in the USD figure.")
    credits = _add_image_credits(model, scenes, None, notes)
    usd = await _fal_usd([model] * scenes if provider == "fal" else [], notes)
    return {"generations": {"images": scenes, "videos": 0, "tts": 1 if word_count else 0},
            "providers": counts, "snapgen_credits": credits, "estimated_usd": usd, "notes": notes}


def _add_image_credits(model: str, n: int, credits: Optional[float], notes: list) -> Optional[float]:
    """Add SnapGen's listed image credits to `credits`; note what isn't estimated."""
    provider = registry.image_model_provider(model)
    if not n or provider == "fal":
        return credits
    if provider == "kie":
        notes.append(f"{n} image(s) via Kie.ai — billed by Kie.ai, not estimated.")
        return credits
    spec = registry.SNAPGEN_IMAGE_MODELS[registry.image_model_name(model)]
    notes.append(f"{n} image(s) via SnapGen {spec['label']}: {spec['credits_label']} each "
                 f"({registry.CREDITS_NOTE}).")
    return (credits or 0) + n * spec["credits"]


async def _fal_usd(models: list, notes: list) -> Optional[float]:
    """Sum of fal per-image prices, or None if there are no fal images or any price is unknown."""
    if not models:
        return None
    if not registry.configured("fal"):
        notes.append("FAL_KEY is not set, so fal.ai prices are unavailable.")
        return None
    prices = await fal.image_prices(sorted(set(models)))
    unknown = sorted(set(m for m in models if m not in prices))
    if unknown:
        notes.append(f"fal.ai did not report a per-image price for {', '.join(unknown)}.")
        return None
    notes.append(f"USD covers the {len(models)} fal.ai image(s) only, at fal's listed base (1K) price.")
    return round(sum(prices[m] for m in models), 4)
