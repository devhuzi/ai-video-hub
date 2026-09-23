import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.main import INTERRUPTED_MESSAGE, app

from .helpers import PACK_4_3, sql

SLIM_KEYS = {"id", "kind", "name", "status", "current_step", "progress", "created_at", "updated_at",
             "num_images", "num_videos", "aspect_ratio", "video_system", "final_video_url", "thumbnail_url",
             "error_message"}


def _create_pack(authed, **overrides):
    resp = authed.post("/api/pipelines", json={**PACK_4_3, **overrides})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _complete_everything(pid):
    sql("UPDATE pipeline_images SET status='completed', url='https://img/' || idx WHERE pipeline_id=?", (pid,))
    sql("UPDATE pipeline_videos SET status='completed', url='https://vid/' || idx, snapgen_uuid='u' || idx "
        "WHERE pipeline_id=?", (pid,))
    sql("UPDATE pipelines SET status='completed', current_step='done', final_video_url='https://share/x', "
        "nextcloud_url='https://dav/x' WHERE id=?", (pid,))


def test_create_pack_returns_arrays_and_starts_run(authed, started):
    body = _create_pack(authed)
    assert isinstance(body["image_prompts"], list) and body["image_prompts"][0] == "img one"
    assert isinstance(body["video_prompts"], list)
    assert body["kind"] == "pack"
    assert body["video_system"] == "veo31_frame"
    assert body["status"] == "queued"
    assert started == [body["id"]]
    assert [v["first_frame_index"] for v in body["videos"]] == [0, 1, 2]
    assert [v["last_frame_index"] for v in body["videos"]] == [1, 2, 3]
    assert body["logs"] and "Pipeline created" in body["logs"][0]
    assert [s["key"] for s in body["steps"]][0] == "generating_images"


def test_extend_shape_detected(authed):
    body = _create_pack(authed, image_prompts=["hero"], video_prompts=["a", "b", "c"])
    assert body["video_system"] == "veo_extend"
    assert body["video_model"] == "veo-3.1-fast"
    assert body["videos"][0]["first_frame_index"] == 0
    assert body["videos"][1]["first_frame_index"] is None


def test_invalid_shape_rejected(authed):
    resp = authed.post("/api/pipelines", json={**PACK_4_3, "video_prompts": ["v"]})
    assert resp.status_code == 400
    assert isinstance(resp.json()["detail"], str)


def test_list_is_slim(authed):
    pid = _create_pack(authed)["id"]
    sql("UPDATE pipeline_images SET status='completed', url='https://img/thumb' WHERE pipeline_id=? AND idx=1", (pid,))
    rows = authed.get("/api/pipelines").json()
    assert isinstance(rows, list) and len(rows) == 1
    assert set(rows[0]) == SLIM_KEYS
    assert rows[0]["thumbnail_url"] == "https://img/thumb"
    assert rows[0]["name"] == "Kitchen Remodel"


def test_detail_404(authed):
    resp = authed.get("/api/pipelines/does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Pipeline not found"}


@pytest.mark.parametrize("payload", [
    {**PACK_4_3, "image_prompts": ["p"] * 61, "video_prompts": ["v"] * 60},
    {**PACK_4_3, "image_prompts": ["p"], "video_prompts": ["v"] * 61},
    {**PACK_4_3, "image_prompts": ["x" * 8001, "b", "c", "d"]},
    {**PACK_4_3, "image_prompts": ["   ", "b", "c", "d"]},
    {**PACK_4_3, "aspect_ratio": "4:3"},
    {**PACK_4_3, "video_system": "sora"},
    {**PACK_4_3, "video_model": "sora"},
    {**PACK_4_3, "image_model": "midjourney"},
    {**PACK_4_3, "first_image_model": "midjourney"},
    {k: v for k, v in PACK_4_3.items() if k != "pipeline_name"},
])
def test_pack_validation(authed, payload):
    resp = authed.post("/api/pipelines", json=payload)
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


@pytest.mark.parametrize("payload", [
    {"script_text": "x" * 50_001, "tts_voice_id": "v"},
    {"script_text": "   ", "tts_voice_id": "v"},
    {"script_text": "hello", "tts_voice_id": ""},
    {"script_text": "hello", "tts_voice_id": "v", "aspect_ratio": "21:9"},
    {"script_text": "hello", "tts_voice_id": "v", "watermark_logo_id": "../../etc/passwd"},
])
def test_script_validation(authed, payload):
    assert authed.post("/api/script-pipelines", json=payload).status_code == 422


def test_script_create_ignores_legacy_fields_and_rejects_unknown_logo(authed, started):
    ok = authed.post("/api/script-pipelines", json={
        "script_text": "Hello world.", "tts_voice_id": "voice", "aspect_ratio": "1:1",
        "animate_scenes": True, "animation_engine": "veo", "watermark_logo_path": "/etc/passwd"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["kind"] == "script" and body["scenes"] == []
    assert body["watermark_logo_url"] is None
    assert body["ai_model"] == config.DEFAULT_LLM_MODEL
    bad = authed.post("/api/script-pipelines", json={"script_text": "Hi", "tts_voice_id": "v",
                                                     "watermark_logo_id": "0" * 32})
    assert bad.status_code == 400


def test_regenerate_image_resets_dependent_videos(authed, started):
    pid = _create_pack(authed)["id"]
    _complete_everything(pid)
    resp = authed.post(f"/api/pipelines/{pid}/regenerate", json={"kind": "image", "index": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reset_images"] == [1]
    assert resp.json()["reset_videos"] == [0, 1]
    imgs = {r["idx"]: r for r in sql("SELECT * FROM pipeline_images WHERE pipeline_id=?", (pid,))}
    vids = {r["idx"]: r for r in sql("SELECT * FROM pipeline_videos WHERE pipeline_id=?", (pid,))}
    assert imgs[1]["status"] == "pending" and imgs[1]["url"] is None
    assert all(imgs[i]["status"] == "completed" for i in (0, 2, 3))
    assert vids[0]["status"] == "pending" and vids[1]["status"] == "pending"
    assert vids[2]["status"] == "completed"
    p = sql("SELECT * FROM pipelines WHERE id=?", (pid,))[0]
    assert p["status"] == "queued" and p["final_video_url"] is None and p["nextcloud_url"] is None
    assert p["resume_from_step"] == "generating_images"
    assert started[-1] == pid


def test_regenerate_video_in_extend_mode_resets_later_clips(authed):
    pid = _create_pack(authed, image_prompts=["hero"], video_prompts=["a", "b", "c", "d"])["id"]
    _complete_everything(pid)
    resp = authed.post(f"/api/pipelines/{pid}/regenerate", json={"kind": "video", "index": 1})
    assert resp.json()["reset_videos"] == [1, 2, 3]
    vids = {r["idx"]: r["status"] for r in sql("SELECT idx, status FROM pipeline_videos WHERE pipeline_id=?", (pid,))}
    assert vids == {0: "completed", 1: "pending", 2: "pending", 3: "pending"}


def test_regenerate_rejections(authed):
    pid = _create_pack(authed)["id"]
    # queued/running → 409
    assert authed.post(f"/api/pipelines/{pid}/regenerate", json={"kind": "image", "index": 0}).status_code == 409
    sql("UPDATE pipelines SET status='running' WHERE id=?", (pid,))
    assert authed.post(f"/api/pipelines/{pid}/regenerate", json={"kind": "image", "index": 0}).status_code == 409
    sql("UPDATE pipelines SET status='failed' WHERE id=?", (pid,))
    assert authed.post(f"/api/pipelines/{pid}/regenerate", json={"kind": "video", "index": 3}).status_code == 400
    assert authed.post(f"/api/pipelines/{pid}/regenerate", json={"kind": "audio", "index": 0}).status_code == 422
    script = authed.post("/api/script-pipelines", json={"script_text": "Hi", "tts_voice_id": "v"}).json()
    sql("UPDATE pipelines SET status='failed' WHERE id=?", (script["id"],))
    assert authed.post(f"/api/pipelines/{script['id']}/regenerate",
                       json={"kind": "image", "index": 0}).status_code == 400


def test_retry_is_atomic(authed, started):
    pid = _create_pack(authed)["id"]
    sql("UPDATE pipelines SET status='failed', resume_from_step='generating_videos' WHERE id=?", (pid,))
    assert authed.post(f"/api/pipelines/{pid}/retry").status_code == 200
    assert authed.post(f"/api/pipelines/{pid}/retry").status_code == 409
    assert started.count(pid) == 2  # create + first retry only
    p = sql("SELECT status, resume_from_step FROM pipelines WHERE id=?", (pid,))[0]
    assert p == {"status": "queued", "resume_from_step": "generating_videos"}


def test_cancel_and_retry_cancelled(authed):
    pid = _create_pack(authed)["id"]
    assert authed.post(f"/api/pipelines/{pid}/cancel").status_code == 200
    assert sql("SELECT status FROM pipelines WHERE id=?", (pid,))[0]["status"] == "cancelled"
    assert authed.post(f"/api/pipelines/{pid}/cancel").status_code == 409
    assert authed.post(f"/api/pipelines/{pid}/retry").status_code == 200


def test_pause_requires_running(authed):
    pid = _create_pack(authed)["id"]
    assert authed.post(f"/api/pipelines/{pid}/pause").status_code == 409


def test_delete_removes_rows_and_files(authed):
    body = authed.post("/api/script-pipelines", json={"script_text": "Hello there.", "tts_voice_id": "v"}).json()
    pid = body["id"]
    sql("UPDATE pipelines SET status='completed' WHERE id=?", (pid,))
    sql("INSERT INTO pipeline_scenes (pipeline_id, idx, text, start_sec, end_sec) VALUES (?, 0, 'a', 0, 1)", (pid,))
    work = config.SCRIPT_STUDIO_DIR / pid
    work.mkdir(parents=True)
    (work / "final.mp4").write_bytes(b"x")
    pack_dir = config.PACK_WORK_DIR / pid
    pack_dir.mkdir(parents=True)

    assert authed.delete(f"/api/pipelines/{pid}").status_code == 200
    for table in ("pipeline_scenes", "pipeline_logs", "pipeline_images", "pipeline_videos"):
        assert sql(f"SELECT * FROM {table} WHERE pipeline_id=?", (pid,)) == []
    assert sql("SELECT * FROM pipelines WHERE id=?", (pid,)) == []
    assert not work.exists() and not pack_dir.exists()


def test_delete_running_rejected(authed):
    pid = _create_pack(authed)["id"]
    sql("UPDATE pipelines SET status='running' WHERE id=?", (pid,))
    assert authed.delete(f"/api/pipelines/{pid}").status_code == 409


def test_final_video_served_locally(authed, token):
    pid = _create_pack(authed)["id"]
    assert authed.get(f"/api/pipelines/{pid}/final-video").status_code == 404
    path = config.PACK_WORK_DIR / pid / "final.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake-mp4")
    resp = authed.get(f"/api/pipelines/{pid}/final-video")
    assert resp.status_code == 200 and resp.content == b"fake-mp4"


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return buf.getvalue()


def test_logo_upload_roundtrip_and_validation(authed, token):
    resp = authed.post("/api/uploads/logo", files={"file": ("logo.png", _png_bytes(), "image/png")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["id"]) == 32 and body["url"] == f"/api/uploads/{body['id']}"
    assert authed.get(body["url"]).content == _png_bytes()
    assert (config.UPLOADS_DIR / f"{body['id']}.png").is_file()

    fake = authed.post("/api/uploads/logo", files={"file": ("logo.png", b"not an image", "image/png")})
    assert fake.status_code == 400
    gif = io.BytesIO()
    Image.new("RGB", (4, 4)).save(gif, format="GIF")
    assert authed.post("/api/uploads/logo", files={"file": ("x.png", gif.getvalue(), "image/png")}).status_code == 400
    big = b"\x89PNG" + b"0" * (config.MAX_UPLOAD_BYTES + 10)
    assert authed.post("/api/uploads/logo", files={"file": ("big.png", big, "image/png")}).status_code == 413

    created = authed.post("/api/script-pipelines", json={"script_text": "Hi", "tts_voice_id": "v",
                                                         "watermark_logo_id": body["id"]})
    assert created.status_code == 200
    assert created.json()["watermark_logo_url"] == body["id"]


def test_startup_recovers_interrupted_pipelines(authed, started):
    pid = _create_pack(authed)["id"]
    sql("UPDATE pipelines SET status='running', resume_from_step='generating_videos' WHERE id=?", (pid,))
    sql("UPDATE pipeline_videos SET status='generating' WHERE pipeline_id=?", (pid,))
    with TestClient(app):  # a second startup
        pass
    p = sql("SELECT status, error_message, resume_from_step FROM pipelines WHERE id=?", (pid,))[0]
    assert p == {"status": "failed", "error_message": INTERRUPTED_MESSAGE, "resume_from_step": "generating_videos"}
    assert {r["status"] for r in sql("SELECT status FROM pipeline_videos WHERE pipeline_id=?", (pid,))} == {"pending"}
