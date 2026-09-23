"""State helpers shared by both pipeline executors."""
import logging
import os
from pathlib import Path

import httpx

import database as db

from .. import config
from ..integrations import nextcloud, nocodb
from .runner import PipelineCancelled, PipelinePaused

logger = logging.getLogger(__name__)

_NOCODB_FIELD_MAP = {
    "status": "Status", "current_step": "CurrentStep", "progress": "Progress",
    "final_video_url": "FinalVideoUrl", "nextcloud_url": "NextCloudUrl", "error_message": "ErrorMessage",
}

# Older rows used these step names; map them onto the current step keys.
LEGACY_STEP_ALIASES = {
    "generating_prompts": "generating_images",
    "uploading_nextcloud": "uploading",
    "syncing_nocodb": "uploading",
}


def normalize_step(step, kind: str):
    step = LEGACY_STEP_ALIASES.get(step, step)
    if kind == "script" and step == "generating_images":
        return "generating_scene_images"
    return step


def pack_work_dir(pipeline_id: str) -> Path:
    return config.PACK_WORK_DIR / pipeline_id


def script_work_dir(pipeline_id: str) -> Path:
    return config.SCRIPT_STUDIO_DIR / pipeline_id


def final_video_path(pipeline_id: str, kind: str) -> Path:
    base = script_work_dir(pipeline_id) if kind == "script" else pack_work_dir(pipeline_id)
    return base / "final.mp4"


def local_final_video_url(pipeline_id: str) -> str:
    return f"/api/pipelines/{pipeline_id}/final-video"


async def add_log(pipeline_id: str, message: str):
    logger.info("Pipeline %s: %s", pipeline_id[:8], message)
    await db.push_log(pipeline_id, message)


async def update_state(pipeline_id: str, updates: dict):
    await db.update_pipeline(pipeline_id, updates)
    synced = {v: ("" if updates[k] is None else str(updates[k]))
              for k, v in _NOCODB_FIELD_MAP.items() if k in updates}
    if synced:
        nocodb.sync_pipeline(pipeline_id, synced)


def sync_status(pipeline_id: str, status: str):
    """Mirror a status change made outside update_state (e.g. an atomic transition)."""
    nocodb.sync_pipeline(pipeline_id, {"Status": status})


async def enter_stage(pipeline_id: str, stage: str, progress: int):
    """Record the stage both as the live step and as the resume point."""
    await update_state(pipeline_id, {"current_step": stage, "resume_from_step": stage, "progress": progress})


async def download_file(url: str, dest: Path):
    """Stream a remote file to `dest` atomically (a partial download never lands at `dest`)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 20):
                    f.write(chunk)
    os.replace(tmp, dest)


async def publish_final_video(pipeline_id: str, final_path: Path, label: str, subfolder: str):
    """Upload to NextCloud when configured; otherwise serve the local file via the API."""
    if nextcloud.enabled():
        await add_log(pipeline_id, f"Uploading final video to NextCloud: {label}...")
        webdav_url, share_url = await nextcloud.upload_file(str(final_path), label, subfolder)
        await update_state(pipeline_id, {"final_video_url": share_url, "nextcloud_url": webdav_url, "progress": 97})
        await add_log(pipeline_id, f"NextCloud upload SUCCESS. Public link: {share_url}")
    else:
        nextcloud.log_status_once()
        url = local_final_video_url(pipeline_id)
        await update_state(pipeline_id, {"final_video_url": url, "nextcloud_url": None, "progress": 97})
        await add_log(pipeline_id, f"NextCloud not configured — final video kept on the server ({url}).")


async def finish(pipeline_id: str, label: str):
    p = await db.find_pipeline(pipeline_id) or {}
    await update_state(pipeline_id, {
        "status": "completed", "current_step": "done", "progress": 100,
        "error_message": None, "nocodb_synced": nocodb.enabled(), "video_title": label,
    })
    nocodb.sync_pipeline(pipeline_id, {"VideoTitle": label})
    nocodb.sync_record(pipeline_id, {
        "PipelineKind": p.get("pipeline_kind") or "paste",
        "PipelineName": p.get("room_name", ""),
        "FinalVideoUrl": p.get("final_video_url") or "",
        "NextCloudUrl": p.get("nextcloud_url") or "",
        "VideoTitle": label,
        "CreatedAt": p.get("created_at") or "",
    })
    await add_log(pipeline_id, "Pipeline completed successfully!")


async def handle_stop(pipeline_id: str, exc: BaseException, stage: str, label: str):
    """Persist the outcome of a run that ended early (pause, cancel or error)."""
    if isinstance(exc, PipelinePaused):
        await add_log(pipeline_id, f"Paused during '{stage}'. Click Resume to continue.")
        await update_state(pipeline_id, {"status": "paused", "current_step": "paused", "resume_from_step": stage})
        return
    current = await db.find_pipeline(pipeline_id)
    if isinstance(exc, PipelineCancelled) or (current and current.get("status") == "cancelled"):
        await add_log(pipeline_id, "Pipeline cancelled by user.")
        await update_state(pipeline_id, {"status": "cancelled", "current_step": "cancelled", "resume_from_step": stage})
        return
    logger.error("%s %s failed at %s: %s", label, pipeline_id, stage, exc)
    await add_log(pipeline_id, f"{label} FAILED at '{stage}': {exc}")
    await update_state(pipeline_id, {
        "status": "failed", "current_step": stage, "resume_from_step": stage, "error_message": str(exc),
    })
