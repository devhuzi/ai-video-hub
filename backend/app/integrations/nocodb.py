"""Optional NocoDB mirror of pipeline state.

Enabled only when NOCODB_URL, NOCODB_API_TOKEN and NOCODB_BASE_ID are all set.
Writes are fire-and-forget background tasks, serialised per pipeline so an
older update can never overwrite a newer one, and never fail a pipeline.
"""
import asyncio
import logging
from typing import Dict, Set

import httpx

from .. import config

logger = logging.getLogger(__name__)

_table_ids: Dict[str, str] = {}
_table_lock = asyncio.Lock()
_pipeline_locks: Dict[str, asyncio.Lock] = {}
_background: Set[asyncio.Task] = set()
_disabled_logged = False

_TABLE_DEFS = {
    "pipelines": ("VideoPipelines", [
        ("PipelineId", "SingleLineText"), ("PipelineKind", "SingleLineText"),
        ("PipelineName", "SingleLineText"), ("Status", "SingleLineText"),
        ("CurrentStep", "SingleLineText"), ("NumImages", "Number"), ("NumVideos", "Number"),
        ("Progress", "Number"), ("AspectRatio", "SingleLineText"), ("AiModel", "SingleLineText"),
        ("VideoTitle", "SingleLineText"), ("FinalVideoUrl", "SingleLineText"),
        ("NextCloudUrl", "SingleLineText"), ("ErrorMessage", "LongText"),
        ("CreatedAt", "SingleLineText"), ("UpdatedAt", "SingleLineText"),
    ]),
    "images": ("VideoImages", [
        ("PipelineId", "SingleLineText"), ("ImageIndex", "Number"), ("Prompt", "LongText"),
        ("Status", "SingleLineText"), ("Service", "SingleLineText"), ("ImageUrl", "SingleLineText"),
        ("SnapGenUUID", "SingleLineText"), ("KieTaskId", "SingleLineText"),
        ("SnapGenError", "LongText"), ("KieError", "LongText"),
        ("CreatedAt", "SingleLineText"), ("CompletedAt", "SingleLineText"),
    ]),
    "videos": ("VideoClips", [
        ("PipelineId", "SingleLineText"), ("VideoIndex", "Number"), ("Prompt", "LongText"),
        ("Status", "SingleLineText"), ("VideoUrl", "SingleLineText"), ("SnapGenUUID", "SingleLineText"),
        ("FirstImageUrl", "SingleLineText"), ("LastImageUrl", "SingleLineText"), ("Attempts", "Number"),
        ("ErrorMessage", "LongText"), ("CreatedAt", "SingleLineText"), ("CompletedAt", "SingleLineText"),
    ]),
    "records": ("VideoRecords", [
        ("PipelineId", "SingleLineText"), ("PipelineKind", "SingleLineText"),
        ("PipelineName", "SingleLineText"), ("FinalVideoUrl", "SingleLineText"),
        ("NextCloudUrl", "SingleLineText"), ("VideoTitle", "SingleLineText"), ("CreatedAt", "SingleLineText"),
    ]),
}


def enabled() -> bool:
    return bool(config.NOCODB_URL and config.NOCODB_API_TOKEN and config.NOCODB_BASE_ID)


def log_status_once():
    global _disabled_logged
    if not enabled() and not _disabled_logged:
        _disabled_logged = True
        logger.info("NocoDB sync disabled (set NOCODB_URL, NOCODB_API_TOKEN and NOCODB_BASE_ID to enable).")


async def _request(method: str, path: str, json_data=None):
    headers = {"xc-token": config.NOCODB_API_TOKEN, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.request(method, f"{config.NOCODB_URL}{path}", headers=headers, json=json_data)
        resp.raise_for_status()
        return resp.json() if resp.content else {}


async def _ensure_tables():
    async with _table_lock:
        if all(k in _table_ids for k in _TABLE_DEFS):
            return
        base = config.NOCODB_BASE_ID
        existing = (await _request("GET", f"/api/v1/db/meta/projects/{base}/tables")).get("list", [])
        by_title = {t["title"]: t["id"] for t in existing}
        for key, (title, cols) in _TABLE_DEFS.items():
            if title in by_title:
                _table_ids[key] = by_title[title]
            else:
                columns = [{"title": t, "uidt": u} for t, u in cols]
                resp = await _request("POST", f"/api/v1/db/meta/projects/{base}/tables",
                                      {"title": title, "columns": columns})
                _table_ids[key] = resp["id"]


async def _upsert(table: str, where: str, data: dict, keys: dict):
    await _ensure_tables()
    base, tid = config.NOCODB_BASE_ID, _table_ids[table]
    existing = await _request("GET", f"/api/v1/db/data/noco/{base}/{tid}?where={where}")
    rows = existing.get("list", [])
    if rows:
        await _request("PATCH", f"/api/v1/db/data/noco/{base}/{tid}/{rows[0]['Id']}", data)
    else:
        await _request("POST", f"/api/v1/db/data/noco/{base}/{tid}", {**data, **keys})


async def _delete_rows(pipeline_id: str):
    await _ensure_tables()
    base = config.NOCODB_BASE_ID
    for key in ("pipelines", "images", "videos"):
        tid = _table_ids.get(key)
        rows = await _request("GET", f"/api/v1/db/data/noco/{base}/{tid}?where=(PipelineId,eq,{pipeline_id})&limit=200")
        for row in rows.get("list", []):
            if row.get("Id"):
                await _request("DELETE", f"/api/v1/db/data/noco/{base}/{tid}/{row['Id']}")


def _spawn(pipeline_id: str, what: str, coro_fn, *args):
    if not enabled():
        log_status_once()
        return None
    lock = _pipeline_locks.setdefault(pipeline_id, asyncio.Lock())

    async def runner():
        async with lock:
            try:
                await coro_fn(*args)
            except Exception as e:
                logger.warning("NocoDB %s sync failed for %s: %s", what, pipeline_id[:8], e)

    task = asyncio.create_task(runner())
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def sync_pipeline(pipeline_id: str, data: dict):
    return _spawn(pipeline_id, "pipeline", _upsert, "pipelines",
                  f"(PipelineId,eq,{pipeline_id})", dict(data), {"PipelineId": pipeline_id})


def sync_image(pipeline_id: str, index: int, data: dict):
    return _spawn(pipeline_id, "image", _upsert, "images",
                  f"(PipelineId,eq,{pipeline_id})~and(ImageIndex,eq,{index})", dict(data),
                  {"PipelineId": pipeline_id, "ImageIndex": index})


def sync_video(pipeline_id: str, index: int, data: dict):
    return _spawn(pipeline_id, "video", _upsert, "videos",
                  f"(PipelineId,eq,{pipeline_id})~and(VideoIndex,eq,{index})", dict(data),
                  {"PipelineId": pipeline_id, "VideoIndex": index})


def sync_record(pipeline_id: str, data: dict):
    return _spawn(pipeline_id, "record", _upsert, "records",
                  f"(PipelineId,eq,{pipeline_id})", dict(data), {"PipelineId": pipeline_id})


def delete_pipeline(pipeline_id: str):
    task = _spawn(pipeline_id, "delete", _delete_rows, pipeline_id)
    if task is not None:
        task.add_done_callback(lambda _t: _pipeline_locks.pop(pipeline_id, None))
    return task
