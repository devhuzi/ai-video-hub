"""Provider clients, legacy-value compatibility, setup status and cost estimates.

Every HTTP call is served by an httpx.MockTransport; the autouse `no_network`
fixture makes any unmocked request fail the test.
"""
import json
import os

import aiosqlite
import httpx
import pytest

import database as db
import script_studio as ss
from app import config
from app.pipelines import pack
from app.providers import fal, images, kie, openrouter, registry, snapgen, tts

from .conftest import NetworkBlocked, mock_http
from .helpers import PACK_4_3, sql


async def _no_sleep(*_a, **_k):
    return None


@pytest.fixture
def fast(monkeypatch):
    for mod in (snapgen, kie, fal):
        monkeypatch.setattr(mod, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(images.asyncio, "sleep", _no_sleep)


def _clear_keys(monkeypatch, *names):
    for name in names:
        monkeypatch.setattr(config, name, "")


# =============================================================
# Network guard
# =============================================================

async def test_unmocked_http_is_blocked():
    with pytest.raises(NetworkBlocked):
        async with httpx.AsyncClient() as c:
            await c.get("https://example.invalid/")


# =============================================================
# SnapGen key alias + legacy value normalisation
# =============================================================

@pytest.mark.parametrize("environ,expected", [
    ({"SNAPGEN_API_KEY": "new"}, ("new", False)),
    ({"GEMINIGEN_API_KEY": " old "}, ("old", True)),
    ({"SNAPGEN_API_KEY": "new", "GEMINIGEN_API_KEY": "old"}, ("new", False)),
    ({"SNAPGEN_API_KEY": "  ", "GEMINIGEN_API_KEY": "old"}, ("old", True)),
    ({}, ("", False)),
])
def test_snapgen_key_falls_back_to_legacy_name(environ, expected):
    assert config.resolve_snapgen_key(environ) == expected


@pytest.mark.parametrize("value,expected", [
    ("geminigen", "snapgen"), ("GeminiGen", "snapgen"), ("straico", "fal"), ("kie.ai", "kie"),
    ("snapgen", "snapgen"), ("kie", "kie"), ("fal", "fal"), (None, "snapgen"), ("", "snapgen"),
    ("midjourney", "snapgen"),
])
def test_normalize_service(value, expected):
    assert registry.normalize_service(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("fal-ai/nano-banana", "fal-ai/nano-banana"),
    ("fal-ai/some-old-model", "fal-ai/some-old-model"),
    ("openai/dall-e-3", config.DEFAULT_SCENE_IMAGE_MODEL),
    (None, config.DEFAULT_SCENE_IMAGE_MODEL),
    ("geminigen", "snapgen"),
    ("kie", "kie"),
    ("straico", config.DEFAULT_SCENE_IMAGE_MODEL),
])
def test_normalize_scene_image_model(value, expected):
    assert registry.normalize_scene_image_model(value) == expected


def test_detail_view_shows_legacy_rows_under_new_names(authed):
    pid = authed.post("/api/pipelines", json=PACK_4_3).json()["id"]
    sql("UPDATE pipelines SET first_image_model='geminigen', subsequent_images_model='straico' WHERE id=?", (pid,))
    sql("UPDATE pipeline_images SET service='straico' WHERE pipeline_id=? AND idx=0", (pid,))
    sql("UPDATE pipeline_images SET service='kie.ai' WHERE pipeline_id=? AND idx=1", (pid,))
    sql("UPDATE pipeline_images SET service='geminigen' WHERE pipeline_id=? AND idx=2", (pid,))
    body = authed.get(f"/api/pipelines/{pid}").json()
    assert body["first_image_model"] == "snapgen"
    assert body["subsequent_images_model"] == "fal"
    assert [i["service"] for i in body["images"]] == ["fal", "kie", "snapgen", None]
    assert {v["service"] for v in body["videos"]} == {"snapgen"}

    script = authed.post("/api/script-pipelines", json={"script_text": "Hi", "tts_voice_id": "Rachel"}).json()
    sql("UPDATE pipelines SET image_gen_model='openai/dall-e-3', first_image_model='straico' WHERE id=?",
        (script["id"],))
    body = authed.get(f"/api/pipelines/{script['id']}").json()
    assert body["image_gen_model"] == config.DEFAULT_SCENE_IMAGE_MODEL
    assert body["first_image_model"] == "fal"


def test_create_accepts_legacy_provider_names(authed):
    resp = authed.post("/api/pipelines", json={**PACK_4_3, "first_image_model": "geminigen",
                                               "subsequent_images_model": "straico"})
    assert resp.status_code == 200, resp.text
    row = sql("SELECT first_image_model, subsequent_images_model FROM pipelines WHERE id=?", (resp.json()["id"],))[0]
    assert row == {"first_image_model": "snapgen", "subsequent_images_model": "fal"}
    script = authed.post("/api/script-pipelines", json={"script_text": "Hi", "tts_voice_id": "Rachel",
                                                        "image_gen_model": "some-retired-model"})
    assert script.status_code == 200
    assert script.json()["image_gen_model"] == config.DEFAULT_SCENE_IMAGE_MODEL


async def test_pack_executor_normalises_stored_slot_values(database, monkeypatch):
    now = db.now_iso()
    await db.insert_pipeline({
        "id": "p-legacy", "room_type": "direct_prompt", "room_name": "Old", "pipeline_kind": "paste",
        "status": "running", "num_images": 2, "num_videos": 1, "video_system": "veo31_frame",
        "first_image_model": "geminigen", "subsequent_images_model": "straico",
        "image_prompts": ["a", "b"], "video_prompts": ["v"], "created_at": now, "updated_at": now,
    })
    for i in range(2):
        await db.upsert_image("p-legacy", i, {"prompt": f"p{i}", "status": "pending"})
    calls = []

    async def fake(i, prompt, ref, pid, aspect_ratio="16:9", preferred_service="snapgen"):
        calls.append(preferred_service)
        await db.update_image(pid, i, {"status": "completed", "url": f"https://img/{i}"})
        return f"https://img/{i}", preferred_service, None, None

    monkeypatch.setattr(pack, "generate_image_with_fallback", fake)
    await pack._generate_images("p-legacy", await db.find_pipeline("p-legacy"))
    assert calls == ["snapgen", "fal"]


async def test_old_database_gets_snapgen_columns_backfilled(tmp_path):
    path = os.path.join(tmp_path, "old.db")
    conn = await aiosqlite.connect(path)
    await conn.executescript("""
        CREATE TABLE pipeline_images (id INTEGER PRIMARY KEY AUTOINCREMENT, pipeline_id TEXT NOT NULL,
            idx INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', geminigen_uuid TEXT,
            geminigen_error TEXT, UNIQUE(pipeline_id, idx));
        CREATE TABLE pipeline_videos (id INTEGER PRIMARY KEY AUTOINCREMENT, pipeline_id TEXT NOT NULL,
            idx INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', geminigen_uuid TEXT,
            UNIQUE(pipeline_id, idx));
        INSERT INTO pipeline_images (pipeline_id, idx, geminigen_uuid, geminigen_error) VALUES ('p', 0, 'img-u', 'boom');
        INSERT INTO pipeline_videos (pipeline_id, idx, geminigen_uuid) VALUES ('p', 0, 'vid-u');
    """)
    await conn.commit()
    await db.init_schema(conn)
    await db.init_schema(conn)  # idempotent
    async with conn.execute("SELECT snapgen_uuid, snapgen_error, geminigen_uuid FROM pipeline_images") as cur:
        assert tuple(await cur.fetchone()) == ("img-u", "boom", "img-u")
    async with conn.execute("SELECT snapgen_uuid FROM pipeline_videos") as cur:
        assert tuple(await cur.fetchone()) == ("vid-u",)
    await conn.close()


# =============================================================
# /api/setup and create-endpoint key checks
# =============================================================

@pytest.mark.parametrize("cleared,packs,script_missing", [
    ((), True, []),
    (("SNAPGEN_API_KEY",), False, []),
    (("OPENROUTER_API_KEY",), True, ["OPENROUTER_API_KEY"]),
    (("FAL_KEY",), True, []),  # Kie.ai covers TTS, SnapGen/Kie cover images
    (("FAL_KEY", "KIE_API_KEY"), True, ["FAL_KEY"]),
    (("FAL_KEY", "KIE_API_KEY", "SNAPGEN_API_KEY", "OPENROUTER_API_KEY"), False, ["OPENROUTER_API_KEY", "FAL_KEY"]),
])
def test_setup_readiness_matrix(authed, monkeypatch, cleared, packs, script_missing):
    _clear_keys(monkeypatch, *cleared)
    body = authed.get("/api/setup").json()
    assert body["providers"]["snapgen"] is ("SNAPGEN_API_KEY" not in cleared)
    assert body["providers"]["nextcloud"] is False and body["providers"]["nocodb"] is False
    assert body["features"]["prompt_packs"] == {"ready": packs, "missing": [] if packs else ["SNAPGEN_API_KEY"]}
    assert body["features"]["script_studio"] == {"ready": not script_missing, "missing": script_missing}
    assert body["env_vars"]["fal"] == "FAL_KEY"


def test_create_pack_without_snapgen_key_names_it(authed, started, monkeypatch):
    _clear_keys(monkeypatch, "SNAPGEN_API_KEY")
    r = authed.post("/api/pipelines", json=PACK_4_3)
    assert r.status_code == 400
    assert "SNAPGEN_API_KEY" in r.json()["detail"]
    assert started == []


@pytest.mark.parametrize("cleared,body,expected", [
    (("OPENROUTER_API_KEY",), {}, ["OPENROUTER_API_KEY"]),
    (("FAL_KEY", "KIE_API_KEY"), {"image_gen_model": "snapgen"}, ["FAL_KEY"]),
    (("FAL_KEY",), {}, ["FAL_KEY"]),  # default image model is a fal model
    (("KIE_API_KEY",), {"image_gen_model": "kie"}, ["KIE_API_KEY"]),
])
def test_create_script_missing_keys(authed, started, monkeypatch, cleared, body, expected):
    _clear_keys(monkeypatch, *cleared)
    r = authed.post("/api/script-pipelines", json={"script_text": "Hello.", "tts_voice_id": "Rachel", **body})
    assert r.status_code == 400
    detail = r.json()["detail"]
    for name in expected:
        assert name in detail
    assert started == []


# =============================================================
# OpenRouter + JSON parsing
# =============================================================

def _chat_response(content):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content},
                                                  "finish_reason": "stop"}]})


async def test_openrouter_structured_output_request(monkeypatch):
    sent = []

    def handler(req):
        sent.append((req.headers, json.loads(req.content)))
        return _chat_response('{"scenes": [{"word_start": 0, "word_end": 1, "description": "x"}]}')

    mock_http(monkeypatch, handler)
    text = await openrouter.complete("anthropic/claude-sonnet-5", "sys", "user", ss.SCENES_SCHEMA, "scenes")
    assert ss.extract_json(text)["scenes"][0]["word_end"] == 1
    headers, body = sent[0]
    assert headers["authorization"] == "Bearer test-dummy-key"
    assert headers["x-title"] == config.APP_TITLE
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["response_format"]["type"] == "json_schema"
    assert body["provider"] == {"require_parameters": True}


async def test_openrouter_falls_back_to_text_and_fenced_json_is_parsed(monkeypatch):
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(body)
        if "response_format" in body:
            return httpx.Response(404, json={"error": {"code": 404, "message": "No endpoints found that can "
                                                                                "handle the requested parameters."}})
        return _chat_response('Here you go:\n```json\n{"prompts": ["a", "b"]}\n```')

    mock_http(monkeypatch, handler)
    scenes = [ss.Scene(idx=i, text=f"t{i}", start=i, end=i + 1, image_prompt=f"d{i}") for i in range(2)]
    prompts = await ss.generate_scene_image_prompts(scenes, completion_fn=openrouter.complete, ai_model="m")
    assert prompts == ["a", "b"]
    assert len(sent) == 2 and "response_format" not in sent[1]


async def test_openrouter_error_body_is_surfaced(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(402, json={"error": {"code": 402, "message": "Insufficient credits"}}))
    with pytest.raises(openrouter.OpenRouterError, match=r"HTTP 402.*Insufficient credits"):
        await openrouter.complete("m", "s", "u")


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n[1, 2]\n```', [1, 2]),
    ('```\n{"a": [1]}\n```', {"a": [1]}),
    ('Sure! {"scenes": []} Hope that helps.', {"scenes": []}),
])
def test_extract_json(text, expected):
    assert ss.extract_json(text) == expected


def test_extract_json_rejects_prose():
    with pytest.raises(ValueError):
        ss.extract_json("no json here")


def test_llm_models_endpoint_caches(authed, monkeypatch):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"data": [
            {"id": "anthropic/claude-sonnet-5", "name": "Claude Sonnet 5",
             "pricing": {"prompt": "0.000002", "completion": "0.00001"},
             "supported_parameters": ["structured_outputs"]},
            {"id": "x/free", "name": "A free model", "pricing": {"prompt": "0", "completion": "0"}},
        ]})

    mock_http(monkeypatch, handler)
    body = authed.get("/api/llm/models").json()
    assert body["default"] == config.DEFAULT_LLM_MODEL == "anthropic/claude-sonnet-5"
    sonnet = next(m for m in body["models"] if m["id"] == "anthropic/claude-sonnet-5")
    assert sonnet == {"id": "anthropic/claude-sonnet-5", "name": "Claude Sonnet 5", "prompt_price": 2.0,
                      "completion_price": 10.0, "structured_outputs": True}
    authed.get("/api/llm/models")
    assert calls == ["https://openrouter.ai/api/v1/models"]


def _unreachable(req):
    raise httpx.ConnectError("connection refused", request=req)


def test_llm_models_upstream_failure_is_502(authed, monkeypatch):
    mock_http(monkeypatch, _unreachable)
    assert authed.get("/api/llm/models").status_code == 502


# =============================================================
# fal queue
# =============================================================

async def test_fal_queue_submit_poll_and_result(monkeypatch, fast):
    seen = []
    polls = {"n": 0}
    base = "https://queue.fal.run/fal-ai/nano-banana-2/requests/req-1"

    def handler(req):
        seen.append((req.method, str(req.url), req.headers.get("authorization")))
        if req.method == "POST":
            assert json.loads(req.content)["image_urls"] == ["https://ref.jpg"]
            return httpx.Response(200, json={"request_id": "req-1", "status_url": f"{base}/status",
                                             "response_url": base})
        if str(req.url) == f"{base}/status":
            polls["n"] += 1
            status = "COMPLETED" if polls["n"] >= 3 else ("IN_QUEUE" if polls["n"] == 1 else "IN_PROGRESS")
            return httpx.Response(200, json={"status": status})
        if str(req.url) == base:
            return httpx.Response(200, json={"images": [{"url": "https://v3.fal.media/out.jpg"}]})
        return httpx.Response(404)

    mock_http(monkeypatch, handler)
    url = await fal.generate_pack_image("prompt", "https://ref.jpg", "16:9", "Image 2")
    assert url == "https://v3.fal.media/out.jpg"
    assert seen[0][:2] == ("POST", "https://queue.fal.run/fal-ai/nano-banana-2/edit")
    assert all(h == "Key test-dummy-key" for *_, h in seen)
    assert polls["n"] == 3


async def test_fal_failed_request_raises_with_detail(monkeypatch, fast):
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"request_id": "r", "status_url": "https://queue.fal.run/x/requests/r/status",
                                             "response_url": "https://queue.fal.run/x/requests/r"})
        return httpx.Response(200, json={"status": "COMPLETED", "error": "NSFW content", "error_type": "content_policy"})

    mock_http(monkeypatch, handler)
    with pytest.raises(fal.FalError, match="NSFW content"):
        await fal.generate_image("fal-ai/nano-banana", "p", "16:9")


async def test_fal_422_validation_body_is_surfaced(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(
        422, json={"detail": [{"loc": ["body", "aspect_ratio"], "msg": "unexpected value", "type": "literal_error"}]}))
    with pytest.raises(fal.FalError, match=r"HTTP 422: aspect_ratio: unexpected value"):
        await fal.generate_image("fal-ai/nano-banana", "p", "4:3")


def test_fal_image_inputs_per_family():
    assert fal.image_input("fal-ai/nano-banana-2", "p", "9:16")["resolution"] == "1K"
    assert "resolution" not in fal.image_input("fal-ai/nano-banana", "p", "9:16")
    assert fal.image_input("fal-ai/bytedance/seedream/v4/text-to-image", "p", "16:9")["image_size"] == \
        {"width": 1920, "height": 1080}
    assert fal.image_input("fal-ai/flux-pro/kontext", "p", "1:1", "https://r")["image_url"] == "https://r"


# =============================================================
# SnapGen
# =============================================================

async def test_snapgen_policy_violation_is_terminal(database, monkeypatch, fast):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"uuid": "u", "status": -2, "error_message": "Prompt flagged"})

    mock_http(monkeypatch, handler)
    with pytest.raises(snapgen.SnapGenError, match="content policy violation: Prompt flagged"):
        await snapgen.wait_for("u", "p-x", "Video 1", max_wait=60)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [3, -1])
async def test_snapgen_other_failures_are_terminal(database, monkeypatch, fast, status):
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={"status": status, "error_message": "bad"}))
    with pytest.raises(snapgen.SnapGenError, match="bad"):
        await snapgen.wait_for("u", "p-x", "Image 1", max_wait=60)


def _form_fields(req) -> str:
    return req.content.decode("utf-8", errors="replace")


async def test_snapgen_veo_requests_send_documented_params(monkeypatch):
    bodies = []

    def handler(req):
        bodies.append(_form_fields(req))
        return httpx.Response(200, json={"uuid": f"u{len(bodies)}"})

    mock_http(monkeypatch, handler)
    await snapgen.submit_veo_frames("p", "https://a.jpg", "https://b.jpg", "16:9")
    await snapgen.submit_veo_first("p", "https://a.jpg", "16:9", 8)
    for body in bodies:
        assert 'name="duration"\r\n\r\n8\r\n' in body
        assert 'name="mode_image"\r\n\r\nframe\r\n' in body
        assert 'name="model"\r\n\r\nveo-3.1-fast\r\n' in body
    assert bodies[0].index("https://a.jpg") < bodies[0].index("https://b.jpg")


async def test_snapgen_422_body_is_surfaced_with_portrait_hint(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(
        422, json={"detail": [{"loc": ["body", "aspect_ratio"], "msg": "Input should be '16:9'"}]}))
    with pytest.raises(snapgen.SnapGenError) as exc:
        await snapgen.submit_veo_frames("p", "https://a", "https://b", "9:16")
    msg = str(exc.value)
    assert "HTTP 422" in msg and "aspect_ratio: Input should be '16:9'" in msg
    assert "documented as 16:9 only" in msg and "Grok" in msg

    with pytest.raises(snapgen.SnapGenError) as exc:
        await snapgen.submit_veo_frames("p", "https://a", "https://b", "16:9")
    assert "documented as 16:9 only" not in str(exc.value)


async def test_snapgen_error_code_body_is_surfaced(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(
        402, json={"detail": {"error_code": "NOT_ENOUGH_CREDIT", "error_message": "Top up your account"}}))
    with pytest.raises(snapgen.SnapGenError, match=r"HTTP 402: NOT_ENOUGH_CREDIT Top up your account"):
        await snapgen.submit_image("p", None, "16:9")


async def test_snapgen_non_json_error_body_is_truncated(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(500, text="<html>" + "x" * 2000))
    with pytest.raises(snapgen.SnapGenError) as exc:
        await snapgen.submit_image("p", None, "16:9")
    assert "HTTP 500: <html>" in str(exc.value) and len(str(exc.value)) < 600


# =============================================================
# Kie.ai
# =============================================================

async def test_kie_error_body_and_credit_code(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(422, json={"code": 422, "msg": "aspect_ratio invalid"}))
    with pytest.raises(kie.KieError, match=r"HTTP 422: 422 aspect_ratio invalid"):
        await kie.submit_image("p", None, "16:9")
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={"code": 402, "msg": "Credits insufficient"}))
    with pytest.raises(kie.KieCreditError):
        await kie.submit_image("p", None, "16:9")


async def test_kie_image_request_has_no_google_search(monkeypatch):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"code": 200, "data": {"taskId": "t1"}})

    mock_http(monkeypatch, handler)
    assert await kie.submit_image("p", "https://ref", "9:16") == "t1"
    assert sent[0]["input"]["image_input"] == ["https://ref"]
    assert "google_search" not in sent[0]["input"]


# =============================================================
# Pack image fallback chain
# =============================================================

async def _insert_images(pid, n=2):
    now = db.now_iso()
    await db.insert_pipeline({"id": pid, "room_type": "direct_prompt", "room_name": "x", "status": "running",
                              "num_images": n, "num_videos": 1, "created_at": now, "updated_at": now})
    for i in range(n):
        await db.upsert_image(pid, i, {"prompt": "p", "status": "pending"})


async def test_fallback_skips_unconfigured_and_passes_reference(database, monkeypatch, fast):
    await _insert_images("p-fb")
    _clear_keys(monkeypatch, "SNAPGEN_API_KEY")
    kie_calls, fal_calls = [], []

    async def kie_submit(prompt, ref, aspect):
        kie_calls.append(ref)
        raise kie.KieCreditError("Kie.ai insufficient credits")

    async def fal_gen(prompt, ref, aspect, step, log=None):
        fal_calls.append(ref)
        return "https://fal/out.jpg"

    monkeypatch.setattr(kie, "submit_image", kie_submit)
    monkeypatch.setattr(fal, "generate_pack_image", fal_gen)
    url, service, gen_uuid, kie_id = await images.generate_image_with_fallback(
        1, "prompt", "https://prev.jpg", "p-fb", preferred_service="snapgen")
    assert (url, service, gen_uuid, kie_id) == ("https://fal/out.jpg", "fal", None, None)
    assert kie_calls == ["https://prev.jpg"]  # credit error: no further Kie retries
    assert fal_calls == ["https://prev.jpg"]
    row = (await db.find_images("p-fb"))[1]
    assert row["service"] == "fal" and row["status"] == "completed"
    logs = await db.get_logs("p-fb")
    assert any("SnapGen skipped — SNAPGEN_API_KEY not set" in line for line in logs)


async def test_fallback_preferred_fal_goes_first(database, monkeypatch, fast):
    await _insert_images("p-pref")
    order = []

    async def fal_gen(prompt, ref, aspect, step, log=None):
        order.append("fal")
        return "https://fal/1.jpg"

    async def snap_submit(*a):
        order.append("snapgen")
        return "u"

    monkeypatch.setattr(fal, "generate_pack_image", fal_gen)
    monkeypatch.setattr(snapgen, "submit_image", snap_submit)
    got = await images.generate_image_with_fallback(0, "p", None, "p-pref", preferred_service="fal")
    assert got[1] == "fal" and order == ["fal"]


async def test_fallback_all_unconfigured_raises(database, monkeypatch):
    await _insert_images("p-none")
    _clear_keys(monkeypatch, "SNAPGEN_API_KEY", "KIE_API_KEY", "FAL_KEY")
    with pytest.raises(RuntimeError, match="FAL_KEY not set"):
        await images.generate_image_with_fallback(0, "p", None, "p-none")


# =============================================================
# TTS
# =============================================================

@pytest.fixture
def fake_download(monkeypatch):
    got = []

    async def download(url, dest):
        got.append(url)
        dest.write_bytes(b"mp3")

    monkeypatch.setattr(tts, "download_file", download)
    return got


async def _noop_log(_msg):
    return None


async def test_tts_falls_back_from_fal_to_kie(tmp_path, monkeypatch, fake_download):
    async def fal_fail(text, voice, log=None):
        raise fal.FalError("fal HTTP 500: upstream")

    async def kie_ok(text, voice, log=None):
        assert voice == "Rachel"
        return "https://kie/audio.mp3"

    monkeypatch.setattr(fal, "text_to_speech", fal_fail)
    monkeypatch.setattr(kie, "text_to_speech", kie_ok)
    provider, stamps = await tts.synthesize("Hello world", "Rachel", tmp_path / "n.mp3", _noop_log)
    assert (provider, stamps) == ("kie", None)
    assert fake_download == ["https://kie/audio.mp3"]


async def test_tts_uses_fal_and_returns_timestamps(tmp_path, monkeypatch, fake_download):
    async def fal_ok(text, voice, log=None):
        return "https://fal/audio.mp3", [{"text": "Hello", "start": 0.0, "end": 0.4}]

    async def kie_never(*a, **k):
        raise AssertionError("Kie must not be called when fal succeeds")

    monkeypatch.setattr(fal, "text_to_speech", fal_ok)
    monkeypatch.setattr(kie, "text_to_speech", kie_never)
    provider, stamps = await tts.synthesize("Hello", "Aria", tmp_path / "n.mp3", _noop_log)
    assert provider == "fal" and stamps[0]["text"] == "Hello"


async def test_tts_without_providers_names_env_vars(tmp_path, monkeypatch):
    _clear_keys(monkeypatch, "FAL_KEY", "KIE_API_KEY")
    with pytest.raises(tts.TTSError, match="FAL_KEY or KIE_API_KEY"):
        await tts.synthesize("Hi", "Rachel", tmp_path / "n.mp3", _noop_log)


async def test_fal_tts_request_asks_for_timestamps(monkeypatch, fast):
    posted = []

    def handler(req):
        if req.method == "POST":
            posted.append(json.loads(req.content))
            return httpx.Response(200, json={"request_id": "r", "status_url": "https://queue.fal.run/s",
                                             "response_url": "https://queue.fal.run/r"})
        if str(req.url).endswith("/s"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, json={"audio": {"url": "https://fal/a.mp3"}, "timestamps": []})

    mock_http(monkeypatch, handler)
    url, stamps = await fal.text_to_speech("Hi there", "Rachel")
    assert url == "https://fal/a.mp3" and stamps == []
    assert posted[0]["voice"] == "Rachel" and posted[0]["timestamps"] is True


def test_voice_list_endpoint(authed, monkeypatch):
    body = authed.get("/api/tts/voices").json()
    assert body["provider"] == "fal"
    assert {"id": "Rachel", "name": "Rachel"} in body["voices"]
    assert all(set(v) >= {"id", "name"} for v in body["voices"])
    _clear_keys(monkeypatch, "FAL_KEY")
    assert authed.get("/api/tts/voices").json()["provider"] == "kie"


# =============================================================
# Word timestamps (shape UNVERIFIED — parsed defensively)
# =============================================================

def test_word_timestamps_used_when_they_match_script():
    raw = [{"text": "Hello", "start": 0.0, "end": 0.5}, {"text": " ", "start": 0.5, "end": 0.6, "type": "spacing"},
           {"text": "world", "start": 0.6, "end": 1.1}]
    timings = ss.word_timings_from_timestamps("Hello, world!", raw, 1.2)
    assert [(t.word, t.start, t.end) for t in timings] == [("Hello,", 0.0, 0.5), ("world!", 0.6, 1.1)]


def test_word_timestamps_from_character_alignment():
    raw = {"characters": list("Hi yo"), "character_start_times_seconds": [0, .1, .2, .3, .4],
           "character_end_times_seconds": [.1, .2, .3, .4, .5]}
    timings = ss.word_timings_from_timestamps("Hi yo", raw, 0.5)
    assert [(t.word, t.start, t.end) for t in timings] == [("Hi", 0.0, 0.2), ("yo", 0.3, 0.5)]


@pytest.mark.parametrize("raw", [
    None, [], "nope", [{"word": "Hello", "start": 0, "end": 1}],  # word count mismatch
    [{"word": "a", "start": 1, "end": 0.5}, {"word": "b", "start": 1, "end": 2}],  # end before start
    [{"foo": 1}, {"bar": 2}],
])
def test_unusable_word_timestamps_fall_back(raw):
    assert ss.word_timings_from_timestamps("Hello world", raw, 2.0) == []


# =============================================================
# Catalogues and estimates (never invented prices)
# =============================================================

def _pricing_handler(prices, calls=None):
    def handler(req):
        if calls is not None:
            calls.append(str(req.url))
        assert req.url.host == "api.fal.ai" and req.headers["authorization"] == "Key test-dummy-key"
        ids = req.url.params["endpoint_id"].split(",")
        return httpx.Response(200, json={"prices": [
            {"endpoint_id": e, "unit_price": prices[e][0], "unit": prices[e][1], "currency": "USD"}
            for e in ids if e in prices], "has_more": False})
    return handler


def test_image_models_catalogue_with_fal_prices(authed, monkeypatch):
    calls = []
    mock_http(monkeypatch, _pricing_handler({"fal-ai/nano-banana-2": (0.08, "image"),
                                             "fal-ai/nano-banana": (0.039, "images"),
                                             "fal-ai/nano-banana-pro": (0.15, "megapixel")}, calls))
    body = authed.get("/api/images/models").json()
    by_id = {m["id"]: m for m in body["models"]}
    assert body["default"] == "fal-ai/nano-banana-2"
    assert by_id["fal-ai/nano-banana-2"]["price_usd"] == 0.08
    assert by_id["fal-ai/nano-banana"]["price_usd"] == 0.039
    assert by_id["fal-ai/nano-banana-pro"]["price_usd"] is None  # not a per-image unit
    assert by_id["snapgen"]["price_usd"] is None and by_id["kie"]["provider"] == "kie"
    authed.get("/api/images/models")
    assert len(calls) == 1  # cached


def test_image_models_without_fal_key_has_no_prices(authed, monkeypatch):
    _clear_keys(monkeypatch, "FAL_KEY")
    body = authed.get("/api/images/models").json()
    assert all(m["price_usd"] is None for m in body["models"])
    assert not next(m for m in body["models"] if m["provider"] == "fal")["configured"]


def test_estimate_pack_snapgen_only_has_no_usd(authed):
    body = authed.get("/api/estimate?kind=pack&num_images=4&num_videos=3&video_system=veo31_frame").json()
    assert body["generations"] == {"images": 4, "videos": 3, "tts": 0}
    assert body["providers"] == {"snapgen": {"images": 4, "videos": 3}}
    assert body["estimated_usd"] is None
    assert "straico_coins" not in body and body["notes"]


def test_estimate_pack_fal_images_priced_from_api(authed, monkeypatch):
    mock_http(monkeypatch, _pricing_handler({"fal-ai/nano-banana-2": (0.08, "image"),
                                             "fal-ai/nano-banana-2/edit": (0.08, "image")}))
    body = authed.get("/api/estimate?kind=pack&num_images=4&num_videos=3"
                      "&first_image_model=snapgen&subsequent_images_model=straico").json()
    assert body["providers"]["fal"] == {"images": 3} and body["providers"]["snapgen"]["images"] == 1
    assert body["estimated_usd"] == pytest.approx(0.24)


def test_estimate_unknown_fal_price_is_null(authed, monkeypatch):
    mock_http(monkeypatch, _pricing_handler({"fal-ai/nano-banana-2": (0.08, "image")}))
    body = authed.get("/api/estimate?kind=pack&num_images=2&num_videos=1&first_image_model=fal"
                      "&subsequent_images_model=fal").json()
    assert body["estimated_usd"] is None
    assert any("did not report" in n for n in body["notes"])


def test_estimate_pricing_api_down_is_null(authed, monkeypatch):
    mock_http(monkeypatch, _unreachable)  # pricing API unreachable: the estimate must not invent a number
    body = authed.get("/api/estimate?kind=script&word_count=125").json()
    assert body["generations"] == {"images": 10, "videos": 0, "tts": 1}
    assert body["estimated_usd"] is None


def test_estimate_script(authed, monkeypatch):
    mock_http(monkeypatch, _pricing_handler({"fal-ai/nano-banana-2": (0.08, "image")}))
    body = authed.get("/api/estimate?kind=script&word_count=125").json()
    assert body["providers"] == {"fal": {"images": 10, "tts": 1}, "openrouter": {"llm_calls": 2}}
    assert body["estimated_usd"] == pytest.approx(0.8)
    kie_body = authed.get("/api/estimate?kind=script&word_count=125&image_model=kie").json()
    assert kie_body["estimated_usd"] is None and kie_body["providers"]["kie"] == {"images": 10}
