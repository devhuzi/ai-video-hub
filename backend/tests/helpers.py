"""Direct SQLite access for tests that drive the API through TestClient (which runs
the app on its own event loop, so tests inspect the database file synchronously)."""
import sqlite3

import database as db


def sql(query: str, params=()):
    conn = sqlite3.connect(db.resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


PACK_4_3 = {
    "pipeline_name": "Kitchen Remodel",
    "image_prompts": ["img one", "img two", "img three", "img four"],
    "video_prompts": ["vid one", "vid two", "vid three"],
    "aspect_ratio": "16:9",
}
