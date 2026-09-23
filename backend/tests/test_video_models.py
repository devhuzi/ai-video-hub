"""SnapGen video model catalogue, per-model request payloads, Veo extend, legacy
video/image setting normalisation, the image fallback ladder and Script Studio
scene-image fallback. Every provider call is mocked."""
import httpx
import pytest

import database as db
from app import config
from app.pipelines import pack
from app.providers import fal, images, kie, registry, snapgen

from .conftest import mock_http
from .helpers import PACK_4_3, sql


async def _no_sleep(*_a, **_k):
    return None


def _field(body: str, name: str, value) -> bool:
    return f'name="{name}"\r\n\r\n{value}\r\n' in body


def _capture(monkeypatch, status=200):
    sent = []

    def handler(req):
        sent.append((req.url.path, req.content.decode("utf-8", errors="replace")))
        if status >= 400:
            return httpx.Response(status, json={"detail": "model not supported"})
        return httpx.Response(200, json={"uuid": f"u{len(sent)}"})

    mock_http(monkeypatch, handler)
    return sent


# =============================================================
# Catalogue + request validation
# =============================================================

def test_video_models_endpoint(authed):
    body = authed.get("/api/video/models").json()
    assert body["default"] == "veo-3.1-fast"
    by_id = {m["id"]: m for m in body["models"]}
    assert list(by_id) == ["veo-3.1-fast", "veo-3.1-lite", "veo-3.1", "omni-flash", "vela"]
    assert [by_id[m]["credits"] for m in by_id] == [4, 4, 100, 13, 0]
    assert by_id["vela"]["credits_label"] == "Free (daily limit)"
    assert by_id["omni-flash"]["durations"] == [4, 6, 8, 10] and by_id["omni-flash"]["image_mode"] == "ingredient"
    assert not by_id["omni-flash"]["supports_extend"] and by_id["veo-3.1"]["supports_extend"]
    assert by_id["vela"]["resolutions"] == [] and by_id["vela"]["durations"] == [5]
    assert by_id["vela"]["aspects"] == ["16:9", "9:16", "1:1"] and by_id["vela"]["experimental"]
    assert "grok" not in str(body).lower()


@pytest.mark.parametrize("overrides,expected", [
    ({}, ("veo-3.1-fast", "720p", 8)),
    ({"video_model": "veo-3.1-lite", "video_resolution": "1080p", "video_duration": 4}, ("veo-3.1-lite", "1080p", 4)),
    ({"video_model": "veo-3.1", "aspect_ratio": "9:16", "video_duration": 6}, ("veo-3.1", "720p", 6)),
    ({"video_model": "omni-flash", "video_duration": 10}, ("omni-flash", "720p", 10)),
    ({"video_model": "vela", "aspect_ratio": "1:1"}, ("vela", None, 5)),
])
def test_valid_video_combos_are_stored(authed, overrides, expected):
    resp = authed.post("/api/pipelines", json={**PACK_4_3, **overrides})
    assert resp.status_code == 200, resp.text
    row = sql("SELECT video_model, video_resolution, video_duration FROM pipelines WHERE id=?",
              (resp.json()["id"],))[0]
    assert (row["video_model"], row["video_resolution"], row["video_duration"]) == expected
    body = resp.json()
    assert (body["video_model"], body["video_resolution"], body["video_duration"]) == expected


@pytest.mark.parametrize("overrides,fragment", [
    ({"video_model": "veo-3.1-fast", "video_duration": 10}, "durations 4, 6, 8s, not 10s"),
    ({"video_model": "vela", "video_duration": 8}, "durations 5s"),
    ({"video_model": "veo-3.1", "aspect_ratio": "1:1"}, "aspect ratios 16:9, 9:16, not 1:1"),
    ({"video_model": "omni-flash", "video_resolution": "4k"}, "video_resolution"),
    ({"video_model": "vela", "video_resolution": "720p"}, "no resolution setting"),
    ({"video_model": "omni-flash", "image_prompts": ["hero"], "video_prompts": ["a", "b"]},
     "Omni Flash is not available for extend packs"),
    ({"video_model": "vela", "image_prompts": ["hero"], "video_prompts": ["a"]},
     "Vela AI (experimental) is not available for extend packs"),
    ({"video_model": "sora"}, "video_model"),
])
def test_invalid_video_combos_are_422(authed, started, overrides, fragment):
    resp = authed.post("/api/pipelines", json={**PACK_4_3, **overrides})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and fragment in detail, detail
    assert started == []


def test_legacy_request_fields_are_mapped(authed):
    resp = authed.post("/api/pipelines", json={
        **PACK_4_3, "image_prompts": ["hero"], "video_prompts": ["a", "b"],
        "video_system": "grok_sequential_extend", "video_engine": "grok", "shot_duration": 15,
        "first_image_model": "kie.ai", "subsequent_images_model": "fal"})
    assert resp.status_code == 200, resp.text
    row = sql("SELECT video_system, video_model, video_duration, image_model FROM pipelines WHERE id=?",
              (resp.json()["id"],))[0]
    assert row == {"video_system": "veo_extend", "video_model": "veo-3.1-fast", "video_duration": 8,
                   "image_model": "kie/nano-banana-2"}


def test_default_image_model_is_first_configured_providers_default(authed, monkeypatch):
    monkeypatch.setattr(config, "KIE_API_KEY", "")
    pid = authed.post("/api/pipelines", json=PACK_4_3).json()["id"]
    assert sql("SELECT image_model FROM pipelines WHERE id=?", (pid,))[0]["image_model"] == "snapgen/nano-banana-2"
    monkeypatch.setattr(config, "SNAPGEN_API_KEY", "")
    assert registry.default_image_model() == "fal-ai/nano-banana-2"
    mock_http(monkeypatch, lambda req: httpx.Response(503))  # fal pricing lookup; prices are optional
    assert authed.get("/api/images/models").json()["pack_default"] == "fal-ai/nano-banana-2"


# =============================================================
# Legacy normalisation (registry.video_settings / normalize_video_system)
# =============================================================

@pytest.mark.parametrize("row,expected", [
    # Rows from before the model picker: Veo 3.1 Fast at the old 1080p / 8 s.
    ({"video_system": "veo31_frame"}, ("veo31_frame", "veo-3.1-fast", "1080p", 8)),
    ({"video_system": "grok_sequential_extend", "video_engine": "grok", "shot_duration": 15},
     ("veo_extend", "veo-3.1-fast", "1080p", 8)),
    ({"video_system": "veo31_sequential_extend"}, ("veo_extend", "veo-3.1-fast", "1080p", 8)),
    ({"video_system": None}, (None, "veo-3.1-fast", "1080p", 8)),
    # Current rows keep their settings; invalid stored values snap to the model default.
    ({"video_system": "veo31_frame", "video_model": "omni-flash", "video_resolution": "1080p",
      "video_duration": 10}, ("veo31_frame", "omni-flash", "1080p", 10)),
    ({"video_system": "veo31_frame", "video_model": "vela", "video_resolution": "720p", "video_duration": 8},
     ("veo31_frame", "vela", None, 5)),
    ({"video_system": "veo_extend", "video_model": "omni-flash", "video_duration": 10},
     ("veo_extend", "veo-3.1-fast", "720p", 8)),
])
def test_video_settings_normalisation(row, expected):
    got = registry.video_settings(row)
    assert (got["video_system"], got["video_model"], got["video_resolution"], got["video_duration"]) == expected


def test_detail_and_list_show_legacy_rows_normalised(authed):
    pid = authed.post("/api/pipelines", json={**PACK_4_3, "image_prompts": ["h"], "video_prompts": ["a"]}).json()["id"]
    sql("UPDATE pipelines SET video_system='grok_sequential_extend', video_engine='grok', video_model=NULL, "
        "video_resolution=NULL, video_duration=NULL WHERE id=?", (pid,))
    body = authed.get(f"/api/pipelines/{pid}").json()
    assert (body["video_system"], body["video_model"], body["video_resolution"], body["video_duration"]) == \
        ("veo_extend", "veo-3.1-fast", "1080p", 8)
    assert "video_engine" not in body and "shot_duration" not in body
    assert authed.get("/api/pipelines").json()[0]["video_system"] == "veo_extend"


# =============================================================
# Per-model request payloads
# =============================================================

async def test_veo_frame_payload(monkeypatch):
    sent = _capture(monkeypatch)
    refs = snapgen.clip_images("veo-3.1", "https://a.jpg", "https://b.jpg")
    await snapgen.submit_video("veo-3.1", "p", refs, "9:16", "1080p", 6)
    path, body = sent[0]
    assert path.endswith("/video-gen/veo")
    for name, value in (("model", "veo-3.1"), ("mode_image", "frame"), ("resolution", "1080p"),
                        ("duration", 6), ("aspect_ratio", "9:16")):
        assert _field(body, name, value), name
    assert body.count('name="ref_images"') == 2 and body.index("https://a.jpg") < body.index("https://b.jpg")


async def test_omni_flash_uses_ingredient_references(monkeypatch):
    sent = _capture(monkeypatch)
    await snapgen.submit_video("omni-flash", "p", snapgen.clip_images("omni-flash", "https://a", "https://b"),
                               "16:9", "720p", 10)
    body = sent[0][1]
    assert _field(body, "mode_image", "ingredient") and _field(body, "duration", 10)
    assert body.count('name="ref_images"') == 2
    # Hero reveal (same image twice) sends one reference.
    assert snapgen.clip_images("omni-flash", "https://a", "https://a") == ["https://a"]
    assert snapgen.clip_images("veo-3.1-fast", "https://a", "https://a") == ["https://a", "https://a"]


async def test_vela_payload_uses_start_image_only(monkeypatch):
    sent = _capture(monkeypatch)
    refs = snapgen.clip_images("vela", "https://a", "https://b")
    assert refs == ["https://a"]
    await snapgen.submit_video("vela", "p", refs, "1:1", None, 5)
    path, body = sent[0]
    assert path.endswith("/video-gen/meta")
    assert _field(body, "model", "meta-video") and _field(body, "duration", 5)
    assert _field(body, "orientation", "square") and _field(body, "file_urls", "https://a")
    assert 'name="resolution"' not in body and 'name="mode_image"' not in body


async def test_vela_4xx_says_it_is_undocumented(monkeypatch):
    _capture(monkeypatch, status=404)
    with pytest.raises(snapgen.SnapGenError) as exc:
        await snapgen.submit_video("vela", "p", ["https://a"], "16:9", None, 5)
    msg = str(exc.value)
    assert "HTTP 404" in msg
    assert "Vela AI isn't in SnapGen's public API docs — the API may not accept it; use a Veo model." in msg


async def test_extend_request_goes_to_veo_extend(monkeypatch):
    sent = _capture(monkeypatch)
    await snapgen.submit_extend("next", "prev-uuid")
    path, body = sent[0]
    assert path.endswith("/video-extend/veo")
    assert _field(body, "ref_history", "prev-uuid") and _field(body, "prompt", "next")
    assert 'name="model"' not in body  # model, aspect and resolution are inherited


# =============================================================
# Executors use the stored video settings
# =============================================================

async def _insert(pid, n_img, n_vid, **extra):
    now = db.now_iso()
    await db.insert_pipeline({
        "id": pid, "room_type": "direct_prompt", "room_name": "T", "pipeline_kind": "paste", "status": "running",
        "num_images": n_img, "num_videos": n_vid, "image_prompts": ["i"] * n_img, "video_prompts": ["v"] * n_vid,
        "created_at": now, "updated_at": now, **extra,
    })
    for i in range(n_img):
        await db.upsert_image(pid, i, {"prompt": "i", "status": "completed", "url": f"https://img/{i}"})
    for i in range(n_vid):
        await db.upsert_video(pid, i, {"prompt": f"v{i}", "status": "pending"})


@pytest.fixture
def video_calls(monkeypatch):
    calls = []

    async def submit_video(model, prompt, image_urls, aspect, resolution, duration):
        calls.append(("video", model, prompt, tuple(image_urls), aspect, resolution, duration))
        return f"uuid-{len(calls)}"

    async def submit_extend(prompt, ref):
        calls.append(("extend", prompt, ref))
        return f"uuid-{len(calls)}"

    async def wait_for(gen_uuid, *_a, **_k):
        return {"generated_video": [{"video_url": f"https://vid/{gen_uuid}"}]}

    monkeypatch.setattr(snapgen, "submit_video", submit_video)
    monkeypatch.setattr(snapgen, "submit_extend", submit_extend)
    monkeypatch.setattr(snapgen, "wait_for", wait_for)
    return calls


@pytest.mark.parametrize("model,res,dur,refs", [
    ("veo-3.1-lite", "1080p", 4, [("https://img/0", "https://img/1"), ("https://img/1", "https://img/2")]),
    ("omni-flash", "720p", 10, [("https://img/0", "https://img/1"), ("https://img/1", "https://img/2")]),
    ("vela", None, 5, [("https://img/0",), ("https://img/1",)]),
])
async def test_frame_pack_uses_stored_model(database, video_calls, model, res, dur, refs):
    await _insert("p-f", 3, 2, video_system="veo31_frame", aspect_ratio="16:9", video_model=model,
                  video_resolution=res, video_duration=dur)
    await pack._generate_videos("p-f", await db.find_pipeline("p-f"))
    assert sorted(video_calls) == sorted(("video", model, f"v{i}", refs[i], "16:9", res, dur) for i in range(2))


async def test_extend_pack_generates_then_extends(database, video_calls):
    await _insert("p-x", 1, 3, video_system="veo_extend", aspect_ratio="9:16", video_model="veo-3.1",
                  video_resolution="1080p", video_duration=6)
    await pack._generate_videos("p-x", await db.find_pipeline("p-x"))
    assert video_calls == [
        ("video", "veo-3.1", "v0", ("https://img/0",), "9:16", "1080p", 6),
        ("extend", "v1", "uuid-1"),
        ("extend", "v2", "uuid-2"),
    ]


async def test_legacy_grok_extend_row_runs_on_veo(database, video_calls):
    await _insert("p-old", 1, 2, video_system="grok_sequential_extend", video_engine="grok", shot_duration=15)
    await pack._generate_videos("p-old", await db.find_pipeline("p-old"))
    assert video_calls[0] == ("video", "veo-3.1-fast", "v0", ("https://img/0",), "16:9", "1080p", 8)
    assert video_calls[1] == ("extend", "v1", "uuid-1")


async def test_extend_rejects_models_without_extend(database, video_calls):
    await _insert("p-bad", 1, 2)
    video = {"video_model": "omni-flash", "video_resolution": "720p", "video_duration": 8, "video_system": "veo_extend"}
    with pytest.raises(RuntimeError, match="does not support extend"):
        await pack.generate_sequential_extend_videos("p-bad", video, ["a", "b"], "https://img/0", "16:9")
    assert video_calls == []


# =============================================================
# Estimate
# =============================================================

@pytest.mark.parametrize("query,credits,seconds", [
    ("video_model=omni-flash&video_duration=10", 39, 30),
    ("video_model=veo-3.1&video_duration=4", 300, 12),
    ("video_model=vela", 0, 15),
])
def test_estimate_video_credits(authed, query, credits, seconds):
    body = authed.get(f"/api/estimate?kind=pack&num_images=4&num_videos=3&{query}").json()
    assert body["snapgen_credits"] == credits and body["video_seconds"] == seconds
    assert any(f"≈ {credits} SnapGen credits" in n and "as listed by SnapGen" in n for n in body["notes"])
    if credits == 0:
        assert any("Free (daily limit)" in n for n in body["notes"])


# =============================================================
# Image fallback ladder
# =============================================================

@pytest.mark.parametrize("start,order", [
    ("snapgen", ["snapgen", "kie", "fal"]),
    ("kie", ["kie", "fal", "snapgen"]),
    ("fal", ["fal", "snapgen", "kie"]),
    ("geminigen", ["snapgen", "kie", "fal"]),
])
def test_image_ladder_order(start, order):
    assert registry.image_ladder(start) == order


@pytest.fixture
def fast(monkeypatch):
    for mod in (snapgen, kie, fal):
        monkeypatch.setattr(mod, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(images.asyncio, "sleep", _no_sleep)


async def _insert_images(pid):
    now = db.now_iso()
    await db.insert_pipeline({"id": pid, "room_type": "direct_prompt", "room_name": "x", "status": "running",
                              "num_images": 1, "num_videos": 1, "created_at": now, "updated_at": now})
    await db.upsert_image(pid, 0, {"prompt": "p", "status": "pending"})


async def test_pack_ladder_wraps_to_earlier_providers(database, monkeypatch, fast):
    await _insert_images("p-wrap")
    order = []

    async def kie_submit(*a):
        order.append("kie")
        raise kie.KieCreditError("Kie.ai insufficient credits")

    async def fal_gen(*a, **k):
        order.append("fal")
        raise fal.FalError("fal down")

    async def snap_submit(*a, **k):
        order.append("snapgen")
        return "u1"

    async def snap_wait(*a, **k):
        return {"generated_image": [{"image_url": "https://snap/1.jpg"}]}

    monkeypatch.setattr(kie, "submit_image", kie_submit)
    monkeypatch.setattr(fal, "generate_pack_image", fal_gen)
    monkeypatch.setattr(snapgen, "submit_image", snap_submit)
    monkeypatch.setattr(snapgen, "wait_for", snap_wait)
    got = await images.generate_image_with_fallback(0, "p", None, "p-wrap", image_model="kie/nano-banana-2")
    assert got[:2] == ("https://snap/1.jpg", "snapgen")
    assert order == ["kie", "fal", "fal", "snapgen"]  # fal retries once before the wrap


async def test_pack_ladder_skips_unconfigured(database, monkeypatch, fast):
    await _insert_images("p-skip")
    monkeypatch.setattr(config, "SNAPGEN_API_KEY", "")
    monkeypatch.setattr(config, "KIE_API_KEY", "")
    fal_calls = []

    async def fal_gen(*a, **k):
        fal_calls.append(a)
        return "https://fal/1.jpg"

    monkeypatch.setattr(fal, "generate_pack_image", fal_gen)
    got = await images.generate_image_with_fallback(0, "p", None, "p-skip", image_model="kie/nano-banana-2")
    assert got[1] == "fal" and len(fal_calls) == 1
    logs = await db.get_logs("p-skip")
    assert any("Kie.ai skipped — KIE_API_KEY not set" in line for line in logs)


# =============================================================
# Script Studio scene-image fallback
# =============================================================

async def test_scene_image_falls_back_with_default_models(database, monkeypatch):
    await _insert_images("s-fb")
    tried = []

    async def fal_generate(model, prompt, aspect, ref, step, log=None):
        tried.append(("fal", model))
        raise fal.FalError("fal down")

    async def snap_submit(prompt, ref, aspect, model=None, reference_uuid=None):
        tried.append(("snapgen", model))
        raise snapgen.SnapGenError("snapgen down")

    async def kie_submit(prompt, ref, aspect):
        tried.append(("kie", ref))
        return "task-1"

    async def kie_wait(task_id, pid, step):
        return "https://kie/1.jpg"

    monkeypatch.setattr(fal, "generate_image", fal_generate)
    monkeypatch.setattr(snapgen, "submit_image", snap_submit)
    monkeypatch.setattr(kie, "submit_image", kie_submit)
    monkeypatch.setattr(kie, "wait_for", kie_wait)
    got = await images.generate_scene_image("fal-ai/flux-pro/kontext/text-to-image", "p", "16:9", "s-fb", "Scene 1")
    assert (got.url, got.model, got.service) == ("https://kie/1.jpg", "kie/nano-banana-2", "kie")
    assert tried == [("fal", "fal-ai/flux-pro/kontext/text-to-image"), ("snapgen", "nano-banana-2"), ("kie", None)]
    logs = await db.get_logs("s-fb")
    assert any("Falling back to SnapGen (snapgen/nano-banana-2)" in line for line in logs)
    assert any("Falling back to Kie.ai (kie/nano-banana-2)" in line for line in logs)


async def test_scene_image_fallback_uses_fal_default_and_skips_unconfigured(database, monkeypatch):
    await _insert_images("s-fb2")
    monkeypatch.setattr(config, "KIE_API_KEY", "")
    used = []

    async def snap_submit(*a, **k):
        raise snapgen.SnapGenError("snapgen down")

    async def fal_generate(model, *a, **k):
        used.append(model)
        return "https://fal/1.jpg"

    monkeypatch.setattr(snapgen, "submit_image", snap_submit)
    monkeypatch.setattr(fal, "generate_image", fal_generate)
    got = await images.generate_scene_image("snapgen/gpt-image-2", "p", "16:9", "s-fb2", "Scene 1")
    assert got.url == "https://fal/1.jpg"
    assert used == [config.DEFAULT_SCENE_IMAGE_MODEL]


async def test_scene_image_all_fail_raises(database, monkeypatch):
    await _insert_images("s-fail")
    for key in ("SNAPGEN_API_KEY", "KIE_API_KEY"):
        monkeypatch.setattr(config, key, "")

    async def fal_generate(*a, **k):
        raise fal.FalError("fal down")

    monkeypatch.setattr(fal, "generate_image", fal_generate)
    with pytest.raises(RuntimeError, match="All image providers failed.*fal down.*SNAPGEN_API_KEY not set"):
        await images.generate_scene_image(config.DEFAULT_SCENE_IMAGE_MODEL, "p", "16:9", "s-fail", "Scene 1")
