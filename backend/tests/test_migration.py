import json
import os

import aiosqlite

import database as db

# Schema as it looked while the "category" feature existed: legacy NOT NULL
# columns and no Script Studio / image_gen_model columns yet.
OLD_SCHEMA = """
CREATE TABLE pipelines (
    id TEXT PRIMARY KEY,
    category_id TEXT NOT NULL,
    video_type_id TEXT NOT NULL,
    room_type TEXT NOT NULL,
    room_name TEXT NOT NULL,
    vibe TEXT,
    lighting TEXT,
    features TEXT DEFAULT '[]',
    custom_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    num_images INTEGER NOT NULL DEFAULT 4,
    num_videos INTEGER NOT NULL DEFAULT 3,
    aspect_ratio TEXT NOT NULL DEFAULT '16:9',
    ai_model TEXT NOT NULL DEFAULT 'x',
    current_step TEXT DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    resume_from_step TEXT DEFAULT 'generating_prompts',
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
    updated_at TEXT NOT NULL
);
"""

CURRENT_COLUMNS = {
    "id", "room_type", "room_name", "pipeline_kind", "status", "num_images", "num_videos", "aspect_ratio",
    "ai_model", "first_image_model", "subsequent_images_model", "current_step", "progress", "resume_from_step",
    "image_prompts", "video_prompts", "image_urls", "video_urls", "final_video_url", "nextcloud_url",
    "nocodb_synced", "video_title", "logs", "error_message", "created_at", "updated_at", "shot_duration",
    "video_engine", "video_system", "script_text", "narration_audio_url", "subtitle_ass_path",
    "watermark_logo_url", "animate_scenes", "tts_voice_id", "tts_model", "global_style", "animation_engine",
    "image_gen_model",
}
LEGACY = {"category_id", "video_type_id", "vibe", "lighting", "features", "custom_text"}


async def _columns(conn):
    async with conn.execute("PRAGMA table_info(pipelines)") as cur:
        return {r[1]: r for r in await cur.fetchall()}


async def _make_old_db(path, with_image_gen_model=False):
    conn = await aiosqlite.connect(path)
    await conn.executescript(OLD_SCHEMA)
    if with_image_gen_model:
        await conn.execute("ALTER TABLE pipelines ADD COLUMN image_gen_model TEXT")
    logs = json.dumps(["[2025-01-01T00:00:00+00:00] created", "[2025-01-01T00:01:00+00:00] step two", "raw line"])
    await conn.execute(
        "INSERT INTO pipelines (id, category_id, video_type_id, room_type, room_name, vibe, logs, created_at, updated_at)"
        " VALUES ('p1', 'cat', 'vt', 'kitchen', 'Old Kitchen', 'cozy', ?, '2025-01-01', '2025-01-01')", (logs,))
    if with_image_gen_model:
        await conn.execute("UPDATE pipelines SET image_gen_model='fal-ai/custom' WHERE id='p1'")
    await conn.commit()
    return conn


async def test_migration_preserves_every_column_and_row(tmp_path):
    path = os.path.join(tmp_path, "old.db")
    conn = await _make_old_db(path, with_image_gen_model=True)
    conn.row_factory = aiosqlite.Row
    await db.init_schema(conn)

    cols = await _columns(conn)
    assert CURRENT_COLUMNS <= set(cols), CURRENT_COLUMNS - set(cols)
    assert LEGACY <= set(cols), "legacy columns must not be dropped"
    assert not any(cols[c][3] for c in LEGACY), "legacy columns must become nullable"

    async with conn.execute("SELECT * FROM pipelines WHERE id='p1'") as cur:
        row = dict(await cur.fetchone())
    assert row["room_name"] == "Old Kitchen"
    assert row["vibe"] == "cozy"
    assert row["category_id"] == "cat"
    assert row["image_gen_model"] == "fal-ai/custom"

    # New code can insert without knowing about the legacy columns.
    await conn.execute("INSERT INTO pipelines (id, room_type, room_name, created_at, updated_at) "
                       "VALUES ('p2', 'script', 'New', 'n', 'n')")
    await conn.commit()

    # Idempotent: a second startup changes nothing.
    await db.init_schema(conn)
    assert set(await _columns(conn)) == set(cols)
    await conn.close()


async def test_json_logs_migrated_exactly_once(tmp_path):
    path = os.path.join(tmp_path, "old.db")
    conn = await _make_old_db(path)
    conn.row_factory = aiosqlite.Row
    await db.init_schema(conn)
    await db.init_schema(conn)
    async with conn.execute("SELECT ts, message FROM pipeline_logs WHERE pipeline_id='p1' ORDER BY id") as cur:
        rows = [tuple(r) for r in await cur.fetchall()]
    assert rows == [
        ("2025-01-01T00:00:00+00:00", "created"),
        ("2025-01-01T00:01:00+00:00", "step two"),
        ("2025-01-01", "raw line"),
    ]
    await conn.close()


async def test_existing_legacy_db_filename_is_kept(tmp_path):
    (tmp_path / "epoxygen.db").write_bytes(b"")
    db.set_data_dir(tmp_path)
    assert db.resolve_db_path().endswith("epoxygen.db")
    (tmp_path / "epoxygen.db").unlink()
    assert db.resolve_db_path().endswith("video_hub.db")


async def test_insert_pipeline_does_not_mutate_caller_dict(database):
    data = {"id": "x1", "room_type": "direct_prompt", "room_name": "N", "image_prompts": ["a", "b"],
            "video_prompts": ["v"], "created_at": "t", "updated_at": "t"}
    await db.insert_pipeline(data)
    assert data["image_prompts"] == ["a", "b"]
    stored = await db.find_pipeline("x1")
    assert stored["image_prompts"] == ["a", "b"]


async def test_replace_scenes_removes_stale_rows(database):
    await db.insert_pipeline({"id": "s1", "room_type": "script", "room_name": "S", "pipeline_kind": "script",
                              "created_at": "t", "updated_at": "t"})
    old = [{"text": f"t{i}", "start_sec": i, "end_sec": i + 1, "status": "image_ready"} for i in range(5)]
    await db.replace_scenes("s1", old)
    await db.upsert_image("s1", 0, {"prompt": "legacy mirror", "status": "pending"})
    await db.replace_scenes("s1", old[:2])
    assert [s["index"] for s in await db.find_scenes("s1")] == [0, 1]
    assert await db.find_images("s1") == []
