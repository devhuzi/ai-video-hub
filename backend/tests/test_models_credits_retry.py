"""SnapGen image models, reference chaining, reported credits and balances, and
retry / regenerate with a new model selection. Every provider call is mocked."""
import httpx
import pytest

import database as db
from app import api, config
from app.pipelines import pack
from app.providers import fal, images, kie, registry, snapgen

from .conftest import mock_http
from .helpers import PACK_4_3, sql


def _field(body: str, name: str, value) -> bool:
    return f'name="{name}"\r\n\r\n{value}\r\n' in body


def _capture(monkeypatch, image_bytes=b"\x89PNG-bytes"):
    sent = []

    def handler(req):
        body = req.content.decode("utf-8", errors="replace")
        sent.append((req.method, req.url.path, body))
        if req.method == "GET":  # reference download
            return httpx.Response(200, content=image_bytes, headers={"content-type": "image/png"})
        return httpx.Response(200, json={"uuid": f"u{len(sent)}", "estimated_credit": 4})

    mock_http(monkeypatch, handler)
    return sent


# =============================================================
# SnapGen image models
# =============================================================

async def test_nano_banana_models_use_generate_image(monkeypatch):
    sent = _capture(monkeypatch)
    await snapgen.submit_image("p", "https://ref.jpg", "9:16", model="nano-banana-pro")
    _, path, body = sent[0]
    assert path.endswith("/generate_image")
    assert _field(body, "model", "nano-banana-pro") and _field(body, "file_urls", "https://ref.jpg")


async def test_grok_image_payload_with_snapgen_reference_uuid(monkeypatch):
    sent = _capture(monkeypatch)
    await snapgen.submit_image("p", "https://ref.jpg", "1:1", model="grok-image", reference_uuid="prev-uuid")
    assert len(sent) == 1  # a SnapGen reference is passed by uuid, nothing is downloaded
    _, path, body = sent[0]
    assert path.endswith("/imagen/grok")
    for name, value in (("orientation", "square"), ("num_result", 1), ("mode", "SPEED"),
                        ("ref_history", "prev-uuid")):
        assert _field(body, name, value), name
    assert 'name="files"' not in body


async def test_gpt_image_2_uploads_a_foreign_reference(monkeypatch):
    sent = _capture(monkeypatch)
    await snapgen.submit_image("p", "https://kie/ref.png", "16:9", model="gpt-image-2")
    assert [(m, p) for m, p, _ in sent][0] == ("GET", "/ref.png")
    _, path, body = sent[1]
    assert path.endswith("/imagen/gpt-image-2")
    for name, value in (("mode", "low"), ("aspect_ratio", "16:9"), ("resolution", "1K"), ("background", "auto")):
        assert _field(body, name, value), name
    assert 'name="files"; filename="reference.png"' in body and "PNG-bytes" in body
    assert 'name="ref_history"' not in body


async def _insert(pid, n_img, n_vid, **extra):
    now = db.now_iso()
    await db.insert_pipeline({
        "id": pid, "room_type": "direct_prompt", "room_name": "T", "pipeline_kind": "paste", "status": "running",
        "num_images": n_img, "num_videos": n_vid, "image_prompts": ["i"] * n_img, "video_prompts": ["v"] * n_vid,
        "video_system": "veo31_frame", "created_at": now, "updated_at": now, **extra,
    })
    for i in range(n_img):
        await db.upsert_image(pid, i, {"prompt": "i", "status": "pending"})
    for i in range(n_vid):
        await db.upsert_video(pid, i, {"prompt": f"v{i}", "status": "pending"})


async def test_pack_passes_snapgen_uuid_of_previous_image(database, monkeypatch):
    await _insert("p-ref", 3, 2, image_model="snapgen/grok-image")
    await db.update_image("p-ref", 0, {"status": "completed", "url": "https://img/0", "service": "snapgen",
                                       "snapgen_uuid": "sg-0"})
    calls = []

    async def fake(i, prompt, ref, pid, aspect_ratio="16:9", image_model=None, reference_uuid=None):
        calls.append((i, ref, reference_uuid, image_model))
        service = "kie" if i == 1 else "snapgen"
        await db.update_image(pid, i, {"status": "completed", "url": f"https://img/{i}", "service": service})
        return f"https://img/{i}", service, None if service == "kie" else f"sg-{i}", None

    monkeypatch.setattr(pack, "generate_image_with_fallback", fake)
    await pack._generate_images("p-ref", await db.find_pipeline("p-ref"))
    assert calls == [(1, "https://img/0", "sg-0", "snapgen/grok-image"),
                     (2, "https://img/1", None, "snapgen/grok-image")]  # image 2 came from Kie: no uuid


async def test_fal_pack_images_use_edit_variants(database, monkeypatch):
    endpoints = []

    async def fake_generate(endpoint, prompt, aspect, ref, step, log=None):
        endpoints.append((endpoint, ref))
        return "https://fal/x.jpg"

    monkeypatch.setattr(fal, "generate_image", fake_generate)
    await fal.generate_pack_image("p", None, "16:9", "Image 1", model="fal-ai/bytedance/seedream/v4/text-to-image")
    await fal.generate_pack_image("p", "https://r", "16:9", "Image 2", model="fal-ai/bytedance/seedream/v4/text-to-image")
    await fal.generate_pack_image("p", "https://r", "16:9", "Image 3", model="fal-ai/nano-banana-pro")
    assert endpoints == [("fal-ai/bytedance/seedream/v4/text-to-image", None),
                         ("fal-ai/bytedance/seedream/v4/edit", "https://r"),
                         ("fal-ai/nano-banana-pro/edit", "https://r")]


# =============================================================
# Credits: reported per item, totals, balances, catalogue and estimate
# =============================================================

async def test_reported_credits_are_persisted_per_item(database, monkeypatch):
    await _insert("p-cr", 2, 1)

    async def snap_submit(*a, **k):
        snapgen._submitted_credits["sg-1"] = 4.0  # as _submit records the submit response
        return "sg-1"

    async def snap_wait(*a, **k):
        return {"generated_image": [{"image_url": "https://snap/1.jpg"}], "used_credit": 3}

    monkeypatch.setattr(snapgen, "submit_image", snap_submit)
    monkeypatch.setattr(snapgen, "wait_for", snap_wait)
    await images.generate_image_with_fallback(0, "p", None, "p-cr", image_model="snapgen/gpt-image-2")
    row = (await db.find_images("p-cr"))[0]
    assert (row["model"], row["service"], row["credits"]) == ("snapgen/gpt-image-2", "snapgen", 3.0)
    assert "sg-1" not in snapgen._submitted_credits

    # Kie: creditsConsumed from recordInfo
    def kie_handler(req):
        if req.url.path.endswith("createTask"):
            return httpx.Response(200, json={"code": 200, "data": {"taskId": "t9"}})
        return httpx.Response(200, json={"data": {"state": "success", "creditsConsumed": 2.5,
                                                  "resultJson": '{"resultUrls": ["https://kie/1.jpg"]}'}})

    mock_http(monkeypatch, kie_handler)
    monkeypatch.setattr(kie, "POLL_INTERVAL", 0.001)
    await images.generate_image_with_fallback(1, "p", "https://snap/1.jpg", "p-cr", image_model="kie/nano-banana-2")
    row = (await db.find_images("p-cr"))[1]
    assert (row["model"], row["service"], row["credits"]) == ("kie/nano-banana-2", "kie", 2.5)

    # Video credits fall back to what the submit response reported.
    async def submit_video(*a, **k):
        snapgen._submitted_credits["v-1"] = 13.0
        return "v-1"

    async def wait_video(*a, **k):
        return {"generated_video": [{"video_url": "https://vid/1"}]}

    monkeypatch.setattr(snapgen, "submit_video", submit_video)
    monkeypatch.setattr(snapgen, "wait_for", wait_video)
    video = registry.video_settings({"video_model": "omni-flash"})
    await pack.generate_frame_video(0, "v", "https://a", "https://b", "p-cr", "16:9", video)
    vrow = (await db.find_videos("p-cr"))[0]
    assert (vrow["model"], vrow["credits"]) == ("omni-flash", 13.0)


def test_detail_shows_item_models_and_credit_totals(authed):
    pid = authed.post("/api/pipelines", json=PACK_4_3).json()["id"]
    sql("UPDATE pipeline_images SET status='completed', service='snapgen', model='snapgen/grok-image', credits=4 "
        "WHERE pipeline_id=? AND idx IN (0, 1)", (pid,))
    sql("UPDATE pipeline_images SET status='completed', service='kie', model='kie/nano-banana-2', credits=1.5 "
        "WHERE pipeline_id=? AND idx=2", (pid,))
    sql("UPDATE pipeline_videos SET status='completed', model='veo-3.1', credits=100 WHERE pipeline_id=? AND idx=0",
        (pid,))
    body = authed.get(f"/api/pipelines/{pid}").json()
    assert body["images"][0]["model"] == "snapgen/grok-image" and body["images"][0]["credits"] == 4
    assert body["videos"][0]["model"] == "veo-3.1"
    assert body["credits_total"] == {"snapgen": 108, "kie": 1.5}


def test_balances_endpoint_caches_and_tolerates_errors(authed, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.host == "api.kie.ai":
            return httpx.Response(500, json={"msg": "down"})
        assert req.headers["x-api-key"] == "test-dummy-key"
        return httpx.Response(200, json={"user_credit": {"available_credit": 1234}})

    mock_http(monkeypatch, handler)
    monkeypatch.setattr(api, "_balance_cache", {"at": 0.0, "data": None})
    assert authed.get("/api/balances").json() == {"snapgen": 1234, "kie": None}
    assert authed.get("/api/balances").json() == {"snapgen": 1234, "kie": None}
    assert sorted(calls) == ["/api/v1/chat/credit", "/uapi/v1/account"]  # second call was cached

    monkeypatch.setattr(api, "_balance_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(config, "SNAPGEN_API_KEY", "")
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={"code": 200, "data": 88}))
    assert authed.get("/api/balances").json() == {"snapgen": None, "kie": 88}


def test_image_models_catalogue_lists_snapgen_models_with_credits(authed, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(503))  # fal prices unavailable
    body = authed.get("/api/images/models").json()
    by_id = {m["id"]: m for m in body["models"]}
    snap = [m["id"] for m in body["models"] if m["provider"] == "snapgen"]
    assert snap == ["snapgen/nano-banana-2", "snapgen/nano-banana-pro", "snapgen/nano-banana-2-lite",
                    "snapgen/grok-image", "snapgen/gpt-image-2"]
    assert by_id["snapgen/nano-banana-pro"]["credits_label"] == "Free (daily limit)"
    assert by_id["snapgen/grok-image"]["credits"] == 4 and by_id["snapgen/gpt-image-2"]["credits"] == 3
    assert body["credits_note"] == "as listed by SnapGen — may change"
    assert [m["provider"] for m in body["models"]].index("kie") < [m["provider"] for m in body["models"]].index("fal")


def test_estimate_adds_snapgen_image_credits(authed):
    body = authed.get("/api/estimate?kind=pack&num_images=4&num_videos=3&image_model=snapgen/grok-image").json()
    assert body["snapgen_credits"] == 3 * 4 + 4 * 4
    assert body["providers"] == {"snapgen": {"images": 4, "videos": 3}}
    script = authed.get("/api/estimate?kind=script&word_count=125&image_model=snapgen/gpt-image-2").json()
    assert script["snapgen_credits"] == 30 and script["estimated_usd"] is None


# =============================================================
# Retry with a new model selection
# =============================================================

def _failed_pack(authed, **overrides):
    pid = authed.post("/api/pipelines", json={**PACK_4_3, **overrides}).json()["id"]
    sql("UPDATE pipelines SET status='failed', resume_from_step='generating_videos' WHERE id=?", (pid,))
    return pid


def test_retry_with_new_pack_models(authed, started):
    pid = _failed_pack(authed)
    sql("UPDATE pipeline_videos SET model_override='veo-3.1' WHERE pipeline_id=? AND idx=1", (pid,))
    sql("UPDATE pipeline_videos SET status='completed', model_override='veo-3.1' WHERE pipeline_id=? AND idx=0",
        (pid,))
    resp = authed.post(f"/api/pipelines/{pid}/retry", json={
        "image_model": "snapgen/gpt-image-2", "video_model": "omni-flash", "video_duration": 10})
    assert resp.status_code == 200, resp.text
    row = sql("SELECT status, image_model, video_model, video_resolution, video_duration FROM pipelines "
              "WHERE id=?", (pid,))[0]
    assert row == {"status": "queued", "image_model": "snapgen/gpt-image-2", "video_model": "omni-flash",
                   "video_resolution": "720p", "video_duration": 10}
    overrides = {r["idx"]: r["model_override"] for r in
                 sql("SELECT idx, model_override FROM pipeline_videos WHERE pipeline_id=?", (pid,))}
    assert overrides == {0: "veo-3.1", 1: None, 2: None}  # completed item keeps its record
    assert started[-1] == pid
    assert "with image_model=snapgen/gpt-image-2" in authed.get(f"/api/pipelines/{pid}").json()["logs"][-1]


def test_retry_keeps_valid_settings_and_snaps_invalid_ones(authed):
    pid = _failed_pack(authed, video_model="veo-3.1", video_resolution="1080p", video_duration=4)
    assert authed.post(f"/api/pipelines/{pid}/retry", json={"video_model": "veo-3.1-lite"}).status_code == 200
    row = sql("SELECT video_resolution, video_duration FROM pipelines WHERE id=?", (pid,))[0]
    assert row == {"video_resolution": "1080p", "video_duration": 4}
    sql("UPDATE pipelines SET status='failed' WHERE id=?", (pid,))
    assert authed.post(f"/api/pipelines/{pid}/retry", json={"video_model": "vela"}).status_code == 200
    row = sql("SELECT video_resolution, video_duration FROM pipelines WHERE id=?", (pid,))[0]
    assert row == {"video_resolution": None, "video_duration": 5}


@pytest.mark.parametrize("pack_overrides,body,status,fragment", [
    ({"image_prompts": ["hero"], "video_prompts": ["a", "b"]}, {"video_model": "omni-flash"}, 422,
     "not available for extend packs"),
    ({"video_model": "vela", "aspect_ratio": "1:1"}, {"video_model": "veo-3.1"}, 422, "not 1:1"),
    ({}, {"video_model": "veo-3.1", "video_duration": 10}, 422, "not 10s"),
    ({}, {"image_model": "midjourney"}, 422, "unknown image model"),
    ({}, {"ai_model": "openai/gpt-5"}, 400, "Script Studio"),
])
def test_retry_rejects_invalid_selections(authed, started, pack_overrides, body, status, fragment):
    pid = _failed_pack(authed, **pack_overrides)
    resp = authed.post(f"/api/pipelines/{pid}/retry", json=body)
    assert resp.status_code == status and fragment in resp.json()["detail"], resp.text
    assert sql("SELECT status FROM pipelines WHERE id=?", (pid,))[0]["status"] == "failed"


def test_retry_script_with_new_models(authed):
    pid = authed.post("/api/script-pipelines", json={"script_text": "Hi", "tts_voice_id": "Rachel"}).json()["id"]
    sql("UPDATE pipelines SET status='failed' WHERE id=?", (pid,))
    resp = authed.post(f"/api/pipelines/{pid}/retry", json={
        "image_model": "snapgen/nano-banana-pro", "ai_model": "openai/gpt-5", "tts_voice_id": "Adam"})
    assert resp.status_code == 200, resp.text
    row = sql("SELECT image_gen_model, ai_model, tts_voice_id FROM pipelines WHERE id=?", (pid,))[0]
    assert row == {"image_gen_model": "snapgen/nano-banana-pro", "ai_model": "openai/gpt-5", "tts_voice_id": "Adam"}
    sql("UPDATE pipelines SET status='failed' WHERE id=?", (pid,))
    bad = authed.post(f"/api/pipelines/{pid}/retry", json={"video_model": "veo-3.1"})
    assert bad.status_code == 400


def test_retry_without_body_still_works(authed):
    pid = _failed_pack(authed)
    assert authed.post(f"/api/pipelines/{pid}/retry").status_code == 200


# =============================================================
# Regenerate one item with its own model
# =============================================================

def _complete(pid):
    sql("UPDATE pipeline_images SET status='completed', url='https://img/' || idx WHERE pipeline_id=?", (pid,))
    sql("UPDATE pipeline_videos SET status='completed', url='https://vid/' || idx WHERE pipeline_id=?", (pid,))
    sql("UPDATE pipelines SET status='completed' WHERE id=?", (pid,))


def test_regenerate_with_item_model(authed):
    pid = authed.post("/api/pipelines", json=PACK_4_3).json()["id"]
    _complete(pid)
    resp = authed.post(f"/api/pipelines/{pid}/regenerate",
                       json={"kind": "image", "index": 1, "image_model": "snapgen/grok-image"})
    assert resp.status_code == 200, resp.text
    rows = {r["idx"]: r["model_override"] for r in
            sql("SELECT idx, model_override FROM pipeline_images WHERE pipeline_id=?", (pid,))}
    assert rows == {0: None, 1: "snapgen/grok-image", 2: None, 3: None}

    _complete(pid)
    resp = authed.post(f"/api/pipelines/{pid}/regenerate", json={"kind": "video", "index": 2, "video_model": "vela"})
    assert resp.status_code == 200
    assert sql("SELECT model_override FROM pipeline_videos WHERE pipeline_id=? AND idx=2", (pid,))[0] == \
        {"model_override": "vela"}


@pytest.mark.parametrize("pack_overrides,body,status", [
    ({}, {"kind": "image", "index": 0, "video_model": "veo-3.1"}, 400),
    ({}, {"kind": "video", "index": 0, "image_model": "snapgen/grok-image"}, 400),
    ({"image_prompts": ["hero"], "video_prompts": ["a", "b"]}, {"kind": "video", "index": 1, "video_model": "veo-3.1"}, 400),
    ({"image_prompts": ["hero"], "video_prompts": ["a", "b"]}, {"kind": "video", "index": 0, "video_model": "vela"}, 422),
])
def test_regenerate_rejects_invalid_item_models(authed, pack_overrides, body, status):
    pid = authed.post("/api/pipelines", json={**PACK_4_3, **pack_overrides}).json()["id"]
    _complete(pid)
    assert authed.post(f"/api/pipelines/{pid}/regenerate", json=body).status_code == status


async def test_executor_uses_item_overrides(database, monkeypatch):
    await _insert("p-ov", 2, 1, image_model="snapgen/nano-banana-2", video_model="veo-3.1-fast",
                  video_resolution="1080p", video_duration=6)
    await db.update_image("p-ov", 0, {"status": "completed", "url": "https://img/0"})
    await db.update_image("p-ov", 1, {"model_override": "fal-ai/nano-banana-pro"})
    await db.update_video("p-ov", 0, {"model_override": "omni-flash"})
    seen = []

    async def fake_image(i, prompt, ref, pid, aspect_ratio="16:9", image_model=None, reference_uuid=None):
        seen.append(image_model)
        await db.update_image(pid, i, {"status": "completed", "url": f"https://img/{i}"})
        return f"https://img/{i}", "fal", None, None

    async def submit_video(model, prompt, urls, aspect, resolution, duration):
        seen.append((model, resolution, duration))
        return "v1"

    async def wait_for(*a, **k):
        return {"generated_video": [{"video_url": "https://vid/1"}]}

    monkeypatch.setattr(pack, "generate_image_with_fallback", fake_image)
    monkeypatch.setattr(snapgen, "submit_video", submit_video)
    monkeypatch.setattr(snapgen, "wait_for", wait_for)
    p = await db.find_pipeline("p-ov")
    await pack._generate_images("p-ov", p)
    await pack._generate_videos("p-ov", p)
    assert seen == ["fal-ai/nano-banana-pro", ("omni-flash", "1080p", 6)]


async def test_item_columns_added_to_old_databases(database):
    conn = await db.get_db()
    for table, cols in db.ADDITIVE_ITEM_COLUMNS.items():
        existing = await db._table_columns(conn, table)
        assert {c for c, _ in cols} <= set(existing)
