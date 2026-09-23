"""SQLite persistence layer for pipelines, their images/videos/scenes and logs.

Schema changes are additive only: columns are never dropped and rows are never
discarded, so existing production databases keep working across upgrades.
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import aiosqlite

# Older deployments used this file name; keep using it when present so an
# upgrade never silently starts from an empty database.
LEGACY_DB_FILENAME = "epoxygen.db"
DB_FILENAME = "video_hub.db"

_db: Optional[aiosqlite.Connection] = None
_data_dir: Optional[str] = None

JSON_LIST_FIELDS = ("image_prompts", "video_prompts", "image_urls", "video_urls", "logs")

# Columns from the retired "category" feature. They are kept (never dropped) but
# must be nullable so new inserts that don't know about them succeed.
LEGACY_CATEGORY_COLUMNS = {
    "category_id", "video_type_id", "vibe", "lighting", "features",
    "custom_features_text", "custom_text", "custom_image_url",
}

# Every column the current code relies on, added to old databases on startup.
ADDITIVE_PIPELINE_COLUMNS = [
    ("first_image_model", "TEXT DEFAULT 'snapgen'"),
    ("subsequent_images_model", "TEXT DEFAULT 'snapgen'"),
    ("shot_duration", "INTEGER DEFAULT 6"),
    ("video_engine", "TEXT DEFAULT 'grok'"),
    ("video_system", "TEXT"),
    ("pipeline_kind", "TEXT DEFAULT 'paste'"),
    ("script_text", "TEXT"),
    ("narration_audio_url", "TEXT"),
    ("subtitle_ass_path", "TEXT"),
    ("watermark_logo_url", "TEXT"),
    ("animate_scenes", "INTEGER DEFAULT 0"),
    ("tts_voice_id", "TEXT"),
    ("tts_model", "TEXT"),
    ("global_style", "TEXT"),
    ("animation_engine", "TEXT"),
    ("image_gen_model", "TEXT"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_data_dir(path) -> None:
    """Point the module at a data directory. Takes effect on the next connect."""
    global _data_dir
    _data_dir = str(path)


def resolve_db_path() -> str:
    data_dir = _data_dir or os.environ.get("DATA_DIR", "/app/data")
    legacy = os.path.join(data_dir, LEGACY_DB_FILENAME)
    if os.path.exists(legacy):
        return legacy
    return os.path.join(data_dir, DB_FILENAME)


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        path = resolve_db_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await init_schema(conn)
        _db = conn
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


# =============================================================
# Schema + migrations
# =============================================================

async def init_schema(db: aiosqlite.Connection):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS pipelines (
            id TEXT PRIMARY KEY,
            room_type TEXT NOT NULL,
            room_name TEXT NOT NULL,
            pipeline_kind TEXT NOT NULL DEFAULT 'paste',
            status TEXT NOT NULL DEFAULT 'pending',
            num_images INTEGER NOT NULL DEFAULT 4,
            num_videos INTEGER NOT NULL DEFAULT 3,
            aspect_ratio TEXT NOT NULL DEFAULT '16:9',
            ai_model TEXT NOT NULL DEFAULT 'anthropic/claude-sonnet-5',
            first_image_model TEXT DEFAULT 'snapgen',
            subsequent_images_model TEXT DEFAULT 'snapgen',
            current_step TEXT DEFAULT 'queued',
            progress INTEGER DEFAULT 0,
            resume_from_step TEXT DEFAULT 'generating_images',
            image_prompts TEXT DEFAULT '[]',
            video_prompts TEXT DEFAULT '[]',
            image_urls TEXT DEFAULT '[]',
            video_urls TEXT DEFAULT '[]',
            final_video_url TEXT,
            nextcloud_url TEXT,
            nocodb_synced INTEGER DEFAULT 0,
            video_title TEXT,
            logs TEXT DEFAULT '[]',
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            shot_duration INTEGER NOT NULL DEFAULT 6,
            video_engine TEXT DEFAULT 'grok',
            video_system TEXT,
            script_text TEXT,
            narration_audio_url TEXT,
            subtitle_ass_path TEXT,
            watermark_logo_url TEXT,
            animate_scenes INTEGER DEFAULT 0,
            tts_voice_id TEXT,
            tts_model TEXT,
            global_style TEXT,
            animation_engine TEXT,
            image_gen_model TEXT
        );
        CREATE TABLE IF NOT EXISTS pipeline_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            prompt TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            service TEXT,
            url TEXT,
            snapgen_uuid TEXT,
            kie_task_id TEXT,
            snapgen_error TEXT,
            kie_error TEXT,
            extra_json TEXT DEFAULT '{}',
            created_at TEXT,
            completed_at TEXT,
            updated_at TEXT,
            UNIQUE(pipeline_id, idx)
        );
        CREATE TABLE IF NOT EXISTS pipeline_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            prompt TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            url TEXT,
            snapgen_uuid TEXT,
            first_image_url TEXT,
            last_image_url TEXT,
            attempt INTEGER DEFAULT 0,
            error TEXT,
            extra_json TEXT DEFAULT '{}',
            created_at TEXT,
            completed_at TEXT,
            updated_at TEXT,
            UNIQUE(pipeline_id, idx)
        );
        CREATE TABLE IF NOT EXISTS pipeline_scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            text TEXT NOT NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            image_prompt TEXT,
            image_url TEXT,
            animated_clip_url TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(pipeline_id, idx)
        );
        CREATE TABLE IF NOT EXISTS pipeline_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            message TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pipeline_logs_pipeline ON pipeline_logs(pipeline_id, id);
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    existing_cols = await _table_columns(db, "pipelines")
    for name, decl in ADDITIVE_PIPELINE_COLUMNS:
        if name not in existing_cols:
            await db.execute(f"ALTER TABLE pipelines ADD COLUMN {name} {decl}")
    await db.commit()

    await _relax_legacy_not_null(db)
    await _migrate_json_logs(db)
    await _add_snapgen_columns(db)


async def _table_columns(db: aiosqlite.Connection, table: str) -> dict:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    # cid, name, type, notnull, dflt_value, pk
    return {row[1]: row for row in rows}


async def _relax_legacy_not_null(db: aiosqlite.Connection):
    """Rebuild `pipelines` if a retired category column is still NOT NULL.

    Earlier versions dropped these columns with a hard-coded keep-list, which
    also silently dropped newer columns such as `image_gen_model`. Instead we
    copy *every* column, only relaxing NOT NULL on the retired ones, so no
    column or row is ever lost. Idempotent: once relaxed, it never runs again.
    """
    cols = await _table_columns(db, "pipelines")
    blocking = [n for n, row in cols.items() if n in LEGACY_CATEGORY_COLUMNS and row[3]]
    if not blocking:
        return

    ordered = sorted(cols.values(), key=lambda r: r[0])
    defs = []
    for _cid, name, ctype, notnull, default, pk in ordered:
        parts = [f'"{name}"', ctype or ""]
        if pk:
            parts.append("PRIMARY KEY")
        if notnull and name not in LEGACY_CATEGORY_COLUMNS:
            parts.append("NOT NULL")
        if default is not None:
            parts.append(f"DEFAULT {default}")
        defs.append(" ".join(p for p in parts if p))
    col_list = ",".join(f'"{r[1]}"' for r in ordered)
    await db.executescript(f"""
        BEGIN;
        CREATE TABLE pipelines_new ({", ".join(defs)});
        INSERT INTO pipelines_new ({col_list}) SELECT {col_list} FROM pipelines;
        DROP TABLE pipelines;
        ALTER TABLE pipelines_new RENAME TO pipelines;
        COMMIT;
    """)


# SnapGen was called GeminiGen before its rebrand. Databases created before the
# rename store job ids in the old columns; they are copied once into the new
# columns (the old columns are kept, per the additive-only rule).
LEGACY_SNAPGEN_COLUMNS = {
    "pipeline_images": [("snapgen_uuid", "geminigen_uuid"), ("snapgen_error", "geminigen_error")],
    "pipeline_videos": [("snapgen_uuid", "geminigen_uuid")],
}


async def _add_snapgen_columns(db: aiosqlite.Connection):
    """Add the snapgen_* columns to old databases and backfill them. Idempotent."""
    for table, pairs in LEGACY_SNAPGEN_COLUMNS.items():
        cols = await _table_columns(db, table)
        for new, old in pairs:
            if new in cols:
                continue
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {new} TEXT")
            if old in cols:
                await db.execute(f"UPDATE {table} SET {new}={old} WHERE {old} IS NOT NULL")
    await db.commit()


_LOG_LINE_RE = re.compile(r"^\[([^\]]+)\] (.*)$", re.DOTALL)


def _split_log_line(line: str, fallback_ts: str):
    m = _LOG_LINE_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    return fallback_ts, line


async def _migrate_json_logs(db: aiosqlite.Connection):
    """One-time copy of the legacy JSON `pipelines.logs` arrays into pipeline_logs.

    The JSON column is left untouched (additive migration); a schema_meta marker
    guarantees the copy happens exactly once.
    """
    async with db.execute("SELECT value FROM schema_meta WHERE key='logs_migrated'") as cur:
        if await cur.fetchone():
            return
    async with db.execute("SELECT id, logs, created_at FROM pipelines") as cur:
        rows = await cur.fetchall()
    batch = []
    for pid, logs_json, created_at in rows:
        for line in _json_loads(logs_json):
            if isinstance(line, str):
                ts, msg = _split_log_line(line, created_at or "")
                batch.append((pid, ts, msg))
    if batch:
        await db.executemany("INSERT INTO pipeline_logs (pipeline_id, ts, message) VALUES (?,?,?)", batch)
    await db.execute("INSERT INTO schema_meta (key, value) VALUES ('logs_migrated', ?)", (now_iso(),))
    await db.commit()


# =============================================================
# Row helpers
# =============================================================

def _json_loads(val):
    if val is None:
        return []
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for k in JSON_LIST_FIELDS:
        if k in d:
            d[k] = _json_loads(d[k])
    if "extra_json" in d:
        extra = _json_loads(d.pop("extra_json"))
        if isinstance(extra, dict):
            for k, v in extra.items():
                d.setdefault(k, v)
    if "nocodb_synced" in d:
        d["nocodb_synced"] = bool(d["nocodb_synced"])
    if "idx" in d:
        d["index"] = d.pop("idx")
    return d


def _encode_pipeline_fields(data: dict) -> dict:
    out = dict(data)
    for k in JSON_LIST_FIELDS:
        if isinstance(out.get(k), list):
            out[k] = json.dumps(out[k])
    if "nocodb_synced" in out:
        out["nocodb_synced"] = int(bool(out["nocodb_synced"]))
    return out


# =============================================================
# Pipelines
# =============================================================

async def insert_pipeline(data: dict):
    """Insert a pipeline row. The caller's dict is never mutated."""
    db = await get_db()
    row = _encode_pipeline_fields(data)
    cols = list(row.keys())
    await db.execute(
        f"INSERT INTO pipelines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        list(row.values()),
    )
    await db.commit()


async def find_pipeline(pipeline_id: str):
    db = await get_db()
    async with db.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_dict(row)


async def update_pipeline(pipeline_id: str, updates: dict):
    db = await get_db()
    row = _encode_pipeline_fields(updates)
    row["updated_at"] = now_iso()
    set_clause = ",".join(f"{k}=?" for k in row)
    await db.execute(f"UPDATE pipelines SET {set_clause} WHERE id=?", list(row.values()) + [pipeline_id])
    await db.commit()


async def transition_status(pipeline_id: str, from_statuses: Iterable[str], updates: dict) -> bool:
    """Atomically apply `updates` only if the current status is in `from_statuses`.

    Returns True when the row changed. This is the single guard against two
    concurrent requests (double retry, retry vs regenerate…) both starting a run.
    """
    db = await get_db()
    froms = list(from_statuses)
    row = _encode_pipeline_fields(updates)
    row["updated_at"] = now_iso()
    set_clause = ",".join(f"{k}=?" for k in row)
    cur = await db.execute(
        f"UPDATE pipelines SET {set_clause} WHERE id=? AND status IN ({','.join('?' * len(froms))})",
        list(row.values()) + [pipeline_id] + froms,
    )
    await db.commit()
    return cur.rowcount == 1


async def list_pipelines_slim(limit: int = 200) -> List[dict]:
    db = await get_db()
    sql = """
        SELECT p.id, p.pipeline_kind, p.room_name, p.status, p.current_step, p.progress,
               p.created_at, p.updated_at, p.num_images, p.num_videos, p.aspect_ratio,
               p.video_system, p.final_video_url, p.error_message,
               COALESCE(
                 (SELECT i.url FROM pipeline_images i
                   WHERE i.pipeline_id = p.id AND i.status = 'completed' AND i.url IS NOT NULL
                   ORDER BY i.idx LIMIT 1),
                 (SELECT s.image_url FROM pipeline_scenes s
                   WHERE s.pipeline_id = p.id AND s.image_url IS NOT NULL
                   ORDER BY s.idx LIMIT 1)
               ) AS thumbnail_url
        FROM pipelines p
        ORDER BY p.created_at DESC
        LIMIT ?
    """
    async with db.execute(sql, (limit,)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def recover_interrupted(message: str) -> List[str]:
    """Mark pipelines left running/queued by a previous process as failed.

    `resume_from_step` is left untouched so Retry continues where it stopped.
    """
    db = await get_db()
    async with db.execute(
        "SELECT id FROM pipelines WHERE status IN ('running','queued','pending')"
    ) as cur:
        ids = [r[0] for r in await cur.fetchall()]
    if ids:
        ts = now_iso()
        await db.execute(
            "UPDATE pipelines SET status='failed', error_message=?, updated_at=? "
            "WHERE status IN ('running','queued','pending')",
            (message, ts),
        )
        await db.executemany(
            "INSERT INTO pipeline_logs (pipeline_id, ts, message) VALUES (?,?,?)",
            [(pid, ts, message) for pid in ids],
        )
    await db.execute(
        "UPDATE pipeline_images SET status='pending' WHERE status IN ('generating','generating_backup')"
    )
    await db.execute("UPDATE pipeline_videos SET status='pending' WHERE status='generating'")
    await db.commit()
    return ids


async def delete_pipeline(pipeline_id: str):
    db = await get_db()
    for table in ("pipeline_images", "pipeline_videos", "pipeline_scenes", "pipeline_logs"):
        await db.execute(f"DELETE FROM {table} WHERE pipeline_id=?", (pipeline_id,))
    await db.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
    await db.commit()


# =============================================================
# Logs (append-only)
# =============================================================

async def push_log(pipeline_id: str, message: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO pipeline_logs (pipeline_id, ts, message) VALUES (?,?,?)",
        (pipeline_id, now_iso(), message),
    )
    await db.commit()


async def get_logs(pipeline_id: str, limit: int = 500) -> List[str]:
    db = await get_db()
    async with db.execute(
        "SELECT ts, message FROM pipeline_logs WHERE pipeline_id=? ORDER BY id DESC LIMIT ?",
        (pipeline_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [f"[{r[0]}] {r[1]}" for r in reversed(rows)]


# =============================================================
# Images / videos
# =============================================================

_IMAGE_COLS = {"prompt", "status", "service", "url", "snapgen_uuid", "kie_task_id",
               "snapgen_error", "kie_error", "created_at", "completed_at", "updated_at"}
_VIDEO_COLS = {"prompt", "status", "url", "snapgen_uuid", "first_image_url", "last_image_url",
               "attempt", "error", "created_at", "completed_at", "updated_at"}


async def _upsert_item(table: str, known: set, pipeline_id: str, index: int, data: dict):
    db = await get_db()
    row = {k: v for k, v in data.items() if k in known}
    extra = {k: v for k, v in data.items() if k not in known}
    if extra:
        row["extra_json"] = json.dumps(extra)
    row["pipeline_id"] = pipeline_id
    row["idx"] = index
    cols = list(row.keys())
    update_parts = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("pipeline_id", "idx"))
    await db.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        f"ON CONFLICT(pipeline_id, idx) DO UPDATE SET {update_parts}",
        list(row.values()),
    )
    await db.commit()


async def _update_item(table: str, known: set, pipeline_id: str, index: int, updates: dict):
    db = await get_db()
    row = {k: v for k, v in updates.items() if k in known}
    extra = {k: v for k, v in updates.items() if k not in known}
    if extra:
        async with db.execute(f"SELECT extra_json FROM {table} WHERE pipeline_id=? AND idx=?",
                              (pipeline_id, index)) as cur:
            existing = await cur.fetchone()
        merged = _json_loads(existing["extra_json"]) if existing and existing["extra_json"] else {}
        if not isinstance(merged, dict):
            merged = {}
        merged.update(extra)
        row["extra_json"] = json.dumps(merged)
    if row:
        set_clause = ",".join(f"{k}=?" for k in row)
        await db.execute(f"UPDATE {table} SET {set_clause} WHERE pipeline_id=? AND idx=?",
                         list(row.values()) + [pipeline_id, index])
        await db.commit()


async def _find_items(table: str, pipeline_id: str, only_completed: bool = False):
    db = await get_db()
    where = " AND status='completed'" if only_completed else ""
    async with db.execute(f"SELECT * FROM {table} WHERE pipeline_id=?{where} ORDER BY idx ASC",
                          (pipeline_id,)) as cur:
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def upsert_image(pipeline_id: str, index: int, data: dict):
    await _upsert_item("pipeline_images", _IMAGE_COLS, pipeline_id, index, data)


async def update_image(pipeline_id: str, index: int, updates: dict):
    await _update_item("pipeline_images", _IMAGE_COLS, pipeline_id, index, updates)


async def find_images(pipeline_id: str):
    return await _find_items("pipeline_images", pipeline_id)


async def find_completed_images(pipeline_id: str):
    return await _find_items("pipeline_images", pipeline_id, only_completed=True)


async def upsert_video(pipeline_id: str, index: int, data: dict):
    await _upsert_item("pipeline_videos", _VIDEO_COLS, pipeline_id, index, data)


async def update_video(pipeline_id: str, index: int, updates: dict):
    await _update_item("pipeline_videos", _VIDEO_COLS, pipeline_id, index, updates)


async def find_videos(pipeline_id: str):
    return await _find_items("pipeline_videos", pipeline_id)


async def find_completed_videos(pipeline_id: str):
    return await _find_items("pipeline_videos", pipeline_id, only_completed=True)


async def reset_items(pipeline_id: str, image_indices: Iterable[int], video_indices: Iterable[int]):
    """Put the given images/videos back to 'pending' so the next run regenerates them."""
    db = await get_db()
    ts = now_iso()
    for idx in image_indices:
        await db.execute(
            "UPDATE pipeline_images SET status='pending', url=NULL, snapgen_uuid=NULL, kie_task_id=NULL, "
            "snapgen_error=NULL, kie_error=NULL, completed_at=NULL, updated_at=? WHERE pipeline_id=? AND idx=?",
            (ts, pipeline_id, idx),
        )
    for idx in video_indices:
        await db.execute(
            "UPDATE pipeline_videos SET status='pending', url=NULL, snapgen_uuid=NULL, error=NULL, "
            "attempt=0, completed_at=NULL, updated_at=? WHERE pipeline_id=? AND idx=?",
            (ts, pipeline_id, idx),
        )
    await db.commit()


async def reset_inflight_items(pipeline_id: str):
    """Items left 'generating' by a stopped run are no longer being generated."""
    db = await get_db()
    await db.execute(
        "UPDATE pipeline_images SET status='pending' WHERE pipeline_id=? AND status IN ('generating','generating_backup')",
        (pipeline_id,),
    )
    await db.execute(
        "UPDATE pipeline_videos SET status='pending' WHERE pipeline_id=? AND status='generating'",
        (pipeline_id,),
    )
    await db.commit()


# =============================================================
# Scenes (Script Studio)
# =============================================================

async def upsert_scene(pipeline_id: str, index: int, data: dict):
    db = await get_db()
    row = dict(data)
    row["pipeline_id"] = pipeline_id
    row["idx"] = index
    cols = list(row.keys())
    update_parts = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("pipeline_id", "idx"))
    await db.execute(
        f"INSERT INTO pipeline_scenes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        f"ON CONFLICT(pipeline_id, idx) DO UPDATE SET {update_parts}",
        list(row.values()),
    )
    await db.commit()


async def update_scene(pipeline_id: str, index: int, updates: dict):
    if not updates:
        return
    db = await get_db()
    set_clause = ",".join(f"{k}=?" for k in updates)
    await db.execute(
        f"UPDATE pipeline_scenes SET {set_clause} WHERE pipeline_id=? AND idx=?",
        list(updates.values()) + [pipeline_id, index],
    )
    await db.commit()


async def find_scenes(pipeline_id: str):
    db = await get_db()
    async with db.execute(
        "SELECT * FROM pipeline_scenes WHERE pipeline_id=? ORDER BY idx ASC", (pipeline_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def replace_scenes(pipeline_id: str, scenes: List[dict]):
    """Atomically replace all scenes of a pipeline (used after a scene split).

    Old scene rows and the legacy per-scene image mirror rows are removed in the
    same transaction, so stale scenes never survive a re-split and a crash can't
    leave a half-written scene list behind.
    """
    db = await get_db()
    await db.execute("DELETE FROM pipeline_scenes WHERE pipeline_id=?", (pipeline_id,))
    await db.execute("DELETE FROM pipeline_images WHERE pipeline_id=?", (pipeline_id,))
    for idx, data in enumerate(scenes):
        row = {**data, "pipeline_id": pipeline_id, "idx": idx}
        cols = list(row.keys())
        await db.execute(
            f"INSERT INTO pipeline_scenes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            list(row.values()),
        )
    await db.commit()
