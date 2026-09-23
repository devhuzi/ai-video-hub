"""Server-side shaping of pipeline data for the API (list rows, detail, steps)."""
from typing import List

import database as db

from . import config
from .pipelines import pack, script
from .pipelines.common import normalize_step
from .providers import registry


def pipeline_kind(p: dict) -> str:
    return "script" if (p.get("pipeline_kind") or "paste") == "script" else "pack"


def compute_steps(p: dict) -> List[dict]:
    kind = pipeline_kind(p)
    defs = script.STEPS if kind == "script" else pack.STEPS
    keys = [k for k, _ in defs]
    status = p.get("status") or "pending"

    def pos_of(step):
        step = normalize_step(step, kind)
        return keys.index(step) if step in keys else None

    if status == "completed":
        states = ["done"] * len(keys)
        if not p.get("nextcloud_url"):
            states[keys.index("uploading")] = "skipped"
    else:
        resume = pos_of(p.get("resume_from_step"))
        current = pos_of(p.get("current_step"))
        if status == "running":
            pos = current if current is not None else (resume or 0)
            marker = "active"
        elif status in ("failed", "paused", "cancelled"):
            known = [x for x in (resume, current) if x is not None]
            pos = max(known) if known else 0
            marker = "paused" if status == "paused" else "failed"
        else:  # queued / pending: waiting to (re)start at the resume point
            pos = resume or 0
            marker = "pending"
        states = ["done"] * pos + [marker] + ["pending"] * (len(keys) - pos - 1)
    return [{"key": k, "label": label, "status": s} for (k, label), s in zip(defs, states)]


def _item_status(raw) -> str:
    return "generating" if raw in ("generating", "generating_backup") else (raw or "pending")


def image_items(p: dict, rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        status = _item_status(r.get("status"))
        out.append({
            "index": r["index"], "status": status, "url": r.get("url"), "prompt": r.get("prompt"),
            "service": registry.normalize_service(r.get("service"), default=None),
            "model": r.get("model"), "credits": r.get("credits"),
            "error": None if status == "completed" else (r.get("kie_error") or r.get("snapgen_error")),
            "first_frame_index": None, "last_frame_index": None,
        })
    return out


def video_items(p: dict, rows: List[dict]) -> List[dict]:
    frames = pack.video_frame_indices(p.get("num_images") or 0, p.get("num_videos") or 0, p.get("video_system"))
    out = []
    for r in rows:
        i = r["index"]
        first, last = frames[i] if i < len(frames) else (None, None)
        status = _item_status(r.get("status"))
        out.append({
            "index": i, "status": status, "url": r.get("url"), "prompt": r.get("prompt"),
            "service": "snapgen", "model": r.get("model"), "credits": r.get("credits"),
            "error": None if status == "completed" else r.get("error"),
            "first_frame_index": first, "last_frame_index": last,
        })
    return out


def scene_items(rows: List[dict]) -> List[dict]:
    return [{
        "index": r["index"], "text": r.get("text"), "start_sec": r.get("start_sec"), "end_sec": r.get("end_sec"),
        "image_prompt": r.get("image_prompt"), "image_url": r.get("image_url"),
        "status": r.get("status") or "pending", "model": r.get("image_model"),
        "service": r.get("image_service"), "credits": r.get("credits"),
    } for r in rows]


# Columns from before the image model / video model settings; their meaning is
# folded into image_model and the video_* fields below.
_LEGACY_SETTING_FIELDS = ("first_image_model", "subsequent_images_model", "video_engine", "shot_duration")


def normalize_providers(p: dict) -> dict:
    """Show settings stored by older versions under their current names and values."""
    if pipeline_kind(p) == "script":
        p["image_gen_model"] = registry.normalize_scene_image_model(p.get("image_gen_model"))
        p["image_model"] = p["image_gen_model"]
    else:
        p["image_model"] = registry.pack_image_model(p)
        p.update(registry.video_settings(p))
    for key in _LEGACY_SETTING_FIELDS:
        p.pop(key, None)
    return p


def list_item(row: dict) -> dict:
    return {
        "id": row["id"], "kind": pipeline_kind(row), "name": row.get("room_name"),
        "status": row.get("status"), "current_step": row.get("current_step"), "progress": row.get("progress") or 0,
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "num_images": row.get("num_images"), "num_videos": row.get("num_videos"),
        "aspect_ratio": row.get("aspect_ratio"),
        "video_system": registry.normalize_video_system(row.get("video_system")) or row.get("video_system"),
        "final_video_url": row.get("final_video_url"), "thumbnail_url": row.get("thumbnail_url"),
        "error_message": row.get("error_message"),
    }


async def pipeline_detail(pipeline_id: str):
    p = await db.find_pipeline(pipeline_id)
    if not p:
        return None
    kind = pipeline_kind(p)
    p.pop("logs", None)  # legacy JSON column; logs now live in pipeline_logs
    p["kind"] = kind
    p["name"] = p.get("room_name")
    normalize_providers(p)
    p["logs"] = await db.get_logs(pipeline_id, config.LOG_TAIL)
    p["steps"] = compute_steps(p)
    if kind == "script":
        p["images"], p["videos"] = [], []
        p["scenes"] = scene_items(await db.find_scenes(pipeline_id))
    else:
        p["images"] = image_items(p, await db.find_images(pipeline_id))
        p["videos"] = video_items(p, await db.find_videos(pipeline_id))
        p["scenes"] = []
    p["credits_total"] = credits_total(p["images"] + p["videos"] + p["scenes"])
    return p


def credits_total(items: List[dict]) -> dict:
    """Credits providers reported for this run's items, per provider ({} when none did)."""
    totals: dict = {}
    for item in items:
        credits, service = item.get("credits"), item.get("service")
        if isinstance(credits, (int, float)) and service:
            totals[service] = round(totals.get(service, 0) + credits, 4)
    return totals
