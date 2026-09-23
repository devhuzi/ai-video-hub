"""Executor tests. Every provider call is mocked; FFmpeg runs for real when installed."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from PIL import Image

import database as db
import script_studio as ss
from app import config
from app.pipelines import pack, runner, script
from app.pipelines.runner import PipelineCancelled, PipelinePaused, RunControl
from app.providers import images, kie, snapgen, tts

from .conftest import HAS_FFMPEG, mock_http

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


async def _insert_pack(pid, n_img, n_vid, system="veo31_frame", **extra):
    now = db.now_iso()
    await db.insert_pipeline({
        "id": pid, "room_type": "direct_prompt", "room_name": "Test Pack", "pipeline_kind": "paste",
        "status": "running", "num_images": n_img, "num_videos": n_vid, "video_system": system,
        "image_prompts": [f"image {i}" for i in range(n_img)], "video_prompts": [f"video {i}" for i in range(n_vid)],
        "created_at": now, "updated_at": now, **extra,
    })
    for i in range(n_img):
        await db.upsert_image(pid, i, {"prompt": f"image {i}", "status": "pending"})
    for i in range(n_vid):
        await db.upsert_video(pid, i, {"prompt": f"video {i}", "status": "pending"})


@pytest.fixture
def fake_images(monkeypatch):
    calls = []

    async def fake(i, prompt, ref_url, pipeline_id, aspect_ratio="16:9", image_model=None, reference_uuid=None):
        calls.append((i, ref_url, image_model))
        url = f"https://img/{i}"
        await db.update_image(pipeline_id, i, {"status": "completed", "url": url, "service": "fal"})
        return url, "fal", None, None

    monkeypatch.setattr(pack, "generate_image_with_fallback", fake)
    return calls


@pytest.fixture
def fake_videos(monkeypatch):
    submitted = []

    async def submit(model, prompt, image_urls, aspect, resolution, duration):
        submitted.append((prompt, *image_urls))
        return f"uuid-{len(submitted)}"

    async def wait_for(gen_uuid, pipeline_id, label, max_wait):
        return {"generated_video": [{"video_url": f"https://vid/{gen_uuid}"}]}

    monkeypatch.setattr(snapgen, "submit_video", submit)
    monkeypatch.setattr(snapgen, "wait_for", wait_for)
    return submitted


@pytest.fixture
def clip_files(tmp_path):
    """Two 1-second clips: one with an audio track, one silent (no audio stream)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    with_audio = tmp_path / "a.mp4"
    no_audio = tmp_path / "b.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=1", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(with_audio))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1", "-c:v", "libx264", str(no_audio))
    return [with_audio, no_audio]


@pytest.fixture
def fake_download(monkeypatch, clip_files):
    async def fake(url, dest):
        n = int(url.rsplit("-", 1)[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(clip_files[n % 2], dest)

    monkeypatch.setattr(pack, "download_file", fake)


# =============================================================
# Pause / cancel are never swallowed by retry or fallback handlers
# =============================================================

async def test_cancel_during_snapgen_image_does_not_fall_back(database, monkeypatch):
    await _insert_pack("p-img", 2, 1)
    kie_calls = []

    async def submit(*a, **k):
        return "gg-uuid"

    async def wait_for(*a, **k):
        raise PipelineCancelled()

    async def kie_submit(*a):
        kie_calls.append(a)
        return "task"

    monkeypatch.setattr(snapgen, "submit_image", submit)
    monkeypatch.setattr(snapgen, "wait_for", wait_for)
    monkeypatch.setattr(kie, "submit_image", kie_submit)
    with pytest.raises(PipelineCancelled):
        await images.generate_image_with_fallback(0, "prompt", None, "p-img", image_model="snapgen/nano-banana-2")
    assert kie_calls == []


async def test_pause_during_frame_video_retry_stops_paid_retries(database, monkeypatch):
    await _insert_pack("p-vid", 2, 1)
    ctl = RunControl("p-vid")
    runner._current.set(ctl)
    submits = []

    async def submit(*a):
        submits.append(a)
        return "gg-uuid"

    async def wait_for(*a, **k):
        ctl.pause.set()  # the user pauses while the first attempt is in flight...
        raise snapgen.SnapGenError("provider hiccup")  # ...and the attempt fails

    monkeypatch.setattr(snapgen, "submit_video", submit)
    monkeypatch.setattr(snapgen, "wait_for", wait_for)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    with pytest.raises(PipelinePaused):
        await pack.generate_frame_video(0, "p", "https://a", "https://b", "p-vid", "16:9")
    assert len(submits) == 1


async def _no_sleep(*_a, **_k):
    return None


# =============================================================
# SnapGen fail-fast
# =============================================================

_mock_client = mock_http


async def test_snapgen_submit_without_uuid_fails_fast(monkeypatch):
    _mock_client(monkeypatch, lambda req: httpx.Response(200, json={"status": "queued"}))
    with pytest.raises(snapgen.SnapGenError, match="no uuid"):
        await snapgen.submit_image("p", None, "16:9")


async def test_snapgen_status_4xx_fails_immediately(database, monkeypatch):
    await _insert_pack("p-gg", 2, 1)
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(404, json={"detail": "not found"})

    _mock_client(monkeypatch, handler)
    monkeypatch.setattr(snapgen, "POLL_INTERVAL", 0.001)
    with pytest.raises(snapgen.SnapGenError, match="HTTP 404"):
        await snapgen.wait_for("abc", "p-gg", "Image 1", max_wait=10)
    assert len(calls) == 1
    with pytest.raises(snapgen.SnapGenError):
        await snapgen.wait_for(None, "p-gg", "Image 1", max_wait=10)


async def test_snapgen_status_5xx_keeps_polling(database, monkeypatch):
    await _insert_pack("p-gg5", 2, 1)
    _mock_client(monkeypatch, lambda req: httpx.Response(503))
    monkeypatch.setattr(snapgen, "POLL_INTERVAL", 0.001)
    with pytest.raises(TimeoutError):
        await snapgen.wait_for("abc", "p-gg5", "Image 1", max_wait=0.005)


# =============================================================
# Pack executor
# =============================================================

async def test_image_loop_fills_gaps_with_nearest_reference(database, fake_images, monkeypatch):
    await _insert_pack("p-gap", 4, 3)
    await db.update_image("p-gap", 0, {"status": "completed", "url": "https://img/0"})
    await db.update_image("p-gap", 2, {"status": "completed", "url": "https://img/2"})
    p = await db.find_pipeline("p-gap")
    await pack._generate_images("p-gap", p)
    assert fake_images == [(1, "https://img/0", "snapgen/nano-banana-2"), (3, "https://img/2", "snapgen/nano-banana-2")]


@needs_ffmpeg
async def test_pack_end_to_end_local_publish(database, fake_images, fake_videos, fake_download):
    await _insert_pack("p-e2e", 3, 2)
    await pack.execute("p-e2e")
    p = await db.find_pipeline("p-e2e")
    assert p["status"] == "completed", await db.get_logs("p-e2e")
    assert p["final_video_url"] == "/api/pipelines/p-e2e/final-video"
    assert [c[0] for c in fake_images] == [0, 1, 2]
    assert fake_videos == [("video 0", "https://img/0", "https://img/1"), ("video 1", "https://img/1", "https://img/2")]
    final = config.PACK_WORK_DIR / "p-e2e" / "final.mp4"
    info = _probe(final)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert 1.8 < float(info["format"]["duration"]) < 2.4


@needs_ffmpeg
async def test_pack_regenerate_only_redoes_reset_items(database, fake_images, fake_videos, fake_download):
    await _insert_pack("p-regen", 3, 2)
    await pack.execute("p-regen")
    fake_images.clear()
    fake_videos.clear()
    await db.reset_items("p-regen", [2], [1])
    await db.update_pipeline("p-regen", {"status": "running", "resume_from_step": "generating_images"})
    await pack.execute("p-regen")
    assert [c[0] for c in fake_images] == [2]
    assert [v[0] for v in fake_videos] == ["video 1"]
    assert (await db.find_pipeline("p-regen"))["status"] == "completed"


@needs_ffmpeg
async def test_upload_failure_is_resumable_without_recombining(database, fake_images, fake_videos,
                                                               fake_download, monkeypatch):
    from app.integrations import nextcloud

    await _insert_pack("p-up", 2, 1)
    monkeypatch.setattr(nextcloud, "enabled", lambda: True)
    attempts = []

    async def upload(path, label, subfolder):
        attempts.append(path)
        if len(attempts) == 1:
            raise RuntimeError("NextCloud down")
        return "https://dav/file.mp4", "https://share/file"

    monkeypatch.setattr(nextcloud, "upload_file", upload)
    await pack.execute("p-up")
    p = await db.find_pipeline("p-up")
    assert p["status"] == "failed" and p["resume_from_step"] == "uploading"

    combined = []
    real_combine = pack._combine

    async def spy_combine(*a):
        combined.append(a)
        await real_combine(*a)

    monkeypatch.setattr(pack, "_combine", spy_combine)
    await db.update_pipeline("p-up", {"status": "running"})
    await pack.execute("p-up")
    p = await db.find_pipeline("p-up")
    assert p["status"] == "completed"
    assert p["final_video_url"] == "https://share/file"
    assert combined == []  # re-used the existing combined file


async def test_pause_between_stages_records_next_stage(database, fake_images, fake_videos):
    await _insert_pack("p-pause", 2, 1)
    ctl = RunControl("p-pause")
    runner._current.set(ctl)
    ctl.pause.set()
    await db.update_image("p-pause", 0, {"status": "completed", "url": "https://img/0"})
    await db.update_image("p-pause", 1, {"status": "completed", "url": "https://img/1"})
    await pack.execute("p-pause")
    p = await db.find_pipeline("p-pause")
    assert p["status"] == "paused" and p["resume_from_step"] == "generating_videos"
    assert fake_videos == []


@needs_ffmpeg
async def test_combine_fails_loudly_on_bad_clip(tmp_path, clip_files):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(RuntimeError):
        await pack.combine_clips([clip_files[0], bad], tmp_path / "out.mp4", tmp_path)


@needs_ffmpeg
async def test_cancelled_ffmpeg_is_killed(tmp_path):
    procs = set()
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=600", "-f", "null", "-"]
    task = asyncio.create_task(ss.run_ffmpeg(cmd, procs))
    for _ in range(100):
        if procs:
            break
        await asyncio.sleep(0.05)
    proc = next(iter(procs))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.returncode is not None
    assert procs == set()


# =============================================================
# Script executor
# =============================================================

async def _insert_script(pid, aspect="16:9", **extra):
    now = db.now_iso()
    await db.insert_pipeline({
        "id": pid, "room_type": "script", "room_name": "Script Test", "pipeline_kind": "script",
        "status": "running", "num_images": 0, "num_videos": 0, "aspect_ratio": aspect,
        "script_text": "Hello world this is a small test of the studio.", "tts_voice_id": "voice",
        "resume_from_step": "generating_tts", "created_at": now, "updated_at": now, **extra,
    })


def _make_png(dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "blue").save(dest, format="PNG")


@pytest.fixture
def script_env(monkeypatch):
    downloads = []

    async def fake_download(url, dest):
        downloads.append(url)
        _make_png(dest)

    monkeypatch.setattr(script, "download_file", fake_download)
    monkeypatch.setattr(ss, "remove_logo_background", lambda src, out: shutil.copy(src, out) or out)
    return downloads


def _forbid(monkeypatch, obj, name):
    async def boom(*a, **k):
        raise AssertionError(f"{name} must not be called on resume")

    monkeypatch.setattr(obj, name, boom)


@needs_ffmpeg
async def test_script_resume_skips_paid_steps(database, script_env, monkeypatch):
    pid = "s-resume"
    await _insert_script(pid, aspect="1:1", resume_from_step="generating_images", watermark_logo_url="0" * 32)
    work = config.SCRIPT_STUDIO_DIR / pid
    work.mkdir(parents=True)
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=2", str(work / "narration.mp3"))
    await db.replace_scenes(pid, [
        {"text": "Hello world this is", "start_sec": 0.0, "end_sec": 1.0, "image_prompt": "a",
         "image_url": "https://img/s0", "status": "image_ready"},
        {"text": "a small test of the studio.", "start_sec": 1.0, "end_sec": 2.0, "image_prompt": "b",
         "image_url": "https://img/s1", "status": "image_ready"},
    ])
    for name in ("split_script_into_scenes", "generate_scene_image_prompts"):
        _forbid(monkeypatch, ss, name)
    _forbid(monkeypatch, images, "generate_scene_image")
    _forbid(monkeypatch, tts, "synthesize")

    await script.execute(pid)
    p = await db.find_pipeline(pid)
    logs = await db.get_logs(pid)
    assert p["status"] == "completed", logs
    assert script_env == ["https://img/s0", "https://img/s1"]
    assert any("watermark logo" in line and "not found" in line for line in logs)
    video = next(s for s in _probe(work / "final.mp4")["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1080)


@needs_ffmpeg
async def test_script_fresh_run_with_logo_and_tts_rescale(database, script_env, monkeypatch):
    pid = "s-fresh"
    logo_id = "a" * 32
    _make_png(config.UPLOADS_DIR / f"{logo_id}.png")
    await _insert_script(pid, aspect="9:16", watermark_logo_url=logo_id)

    async def fake_tts(text, voice, out_path, log):
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=2", "-f", "mp3", str(out_path))
        return "fal", None

    async def fake_split(script_text, timings, *, completion_fn, ai_model):
        half = len(timings) // 2
        return [ss.Scene(idx=0, text="a", start=0.0, end=timings[half - 1].end, image_prompt="desc a"),
                ss.Scene(idx=1, text="b", start=timings[half].start, end=timings[-1].end, image_prompt="desc b")]

    async def fake_prompts(scenes, *, completion_fn, ai_model, global_style):
        return [f"detailed {s.image_prompt}" for s in scenes]

    generated = []

    async def fake_image(model, prompt, aspect_ratio, pipeline_id, step):
        generated.append(prompt)
        return images.SceneImage(f"https://img/{len(generated)}", "snapgen/grok-image", "snapgen", 4.0)

    monkeypatch.setattr(tts, "synthesize", fake_tts)
    monkeypatch.setattr(ss, "split_script_into_scenes", fake_split)
    monkeypatch.setattr(ss, "generate_scene_image_prompts", fake_prompts)
    monkeypatch.setattr(images, "generate_scene_image", fake_image)

    await script.execute(pid)
    p = await db.find_pipeline(pid)
    assert p["status"] == "completed", await db.get_logs(pid)
    assert generated == ["detailed desc a", "detailed desc b"]
    scenes = await db.find_scenes(pid)
    assert [s["status"] for s in scenes] == ["image_ready", "image_ready"]
    assert {(s["image_model"], s["image_service"], s["credits"]) for s in scenes} == {("snapgen/grok-image", "snapgen", 4.0)}
    work = config.SCRIPT_STUDIO_DIR / pid
    video = next(s for s in _probe(work / "final.mp4")["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert (config.UPLOADS_DIR / f"{logo_id}.nobg.png").exists()  # cached beside the upload

    # Narration lost (e.g. wiped disk) → TTS re-runs, scenes are rescaled, nothing else is re-bought.
    (work / "narration.mp3").unlink()

    async def fake_tts_4s(text, voice, out_path, log):
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-f", "mp3", str(out_path))
        return "kie", None

    monkeypatch.setattr(tts, "synthesize", fake_tts_4s)
    _forbid(monkeypatch, ss, "split_script_into_scenes")
    _forbid(monkeypatch, images, "generate_scene_image")
    await db.update_pipeline(pid, {"status": "running", "resume_from_step": "generating_tts"})
    await script.execute(pid)
    assert (await db.find_pipeline(pid))["status"] == "completed"
    scenes = await db.find_scenes(pid)
    assert 3.9 < scenes[-1]["end_sec"] < 4.2


# =============================================================
# Run registry
# =============================================================

async def test_runner_single_run_cancel_and_registry_ownership(database, monkeypatch):
    from app import api

    await _insert_pack("p-run", 2, 1, status="queued")
    started = asyncio.Event()

    async def slow_execute(pid):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(pack, "execute", slow_execute)
    assert runner.start_run("p-run") is True
    assert runner.start_run("p-run") is False  # never two runs of one pipeline
    await asyncio.wait_for(started.wait(), 5)
    ctl = runner.get_control("p-run")

    await api.cancel_pipeline("p-run")
    assert ctl.task.done()
    assert (await db.find_pipeline("p-run"))["status"] == "cancelled"
    assert runner.get_control("p-run") is None

    # A stale run finishing late must not remove a newer run's registration.
    newer = RunControl("p-run")
    runner._runs["p-run"] = newer
    await runner._run(RunControl("p-run"))  # status is 'cancelled', so it exits immediately
    assert runner.get_control("p-run") is newer


@needs_ffmpeg
async def test_runner_runs_queued_pipeline_to_completion(database, fake_images, fake_videos, fake_download):
    await _insert_pack("p-full", 2, 1, status="queued")
    assert runner.start_run("p-full")
    ctl = runner.get_control("p-full")
    await asyncio.wait_for(ctl.task, 60)
    p = await db.find_pipeline("p-full")
    assert p["status"] == "completed", await db.get_logs("p-full")
    assert p["current_step"] == "done" and p["progress"] == 100
    assert runner.get_control("p-full") is None


def test_subtitle_lines_fit_portrait_width():
    words = [ss.WordTiming(word=w, start=i * 0.4, end=i * 0.4 + 0.35)
             for i, w in enumerate("Deep beneath the ocean a forgotten city waits in silence its towers".split())]
    ass = ss.build_subtitle_ass(words, play_res_x=1080, play_res_y=1920, fontsize=59)
    lines = [l.split(",", 9)[9] for l in ass.splitlines() if l.startswith("Dialogue:")]
    import re as _re
    texts = [_re.sub(r"\{[^}]*\}", "", l).strip() for l in lines]
    max_chars = int((1080 - 160) / (59 * 0.55))
    assert all(len(t) <= max_chars for t in texts), texts
    assert " ".join(texts).split() == [w.word for w in words]  # nothing dropped or reordered
    assert "WrapStyle: 0" in ass


def test_final_mux_scales_logo_top_right():
    cmd = ss.build_final_mux_cmd("in.mp4", "a.mp3", "s.ass", "out.mp4", logo_path="logo.png",
                                 logo_width=230, logo_margin=43)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=230:-1" in graph and "overlay=W-w-43:43" in graph
