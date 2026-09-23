"""Prompt-pack pipelines: pasted image + video prompts → images → clips → combined video.

Every stage skips work that is already completed, so retry, resume and
regenerate all simply re-run the executor and only the missing pieces are paid for.
"""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import database as db
import script_studio as ss

from .. import config
from ..integrations import nocodb
from ..providers import registry, snapgen
from ..providers.images import generate_image_with_fallback
from . import runner
from .common import (add_log, enter_stage, finish, handle_stop, pack_work_dir, publish_final_video,
                     download_file, normalize_step, update_state)
from .runner import PipelineCancelled, PipelineInterrupt, PipelinePaused

STEPS = [
    ("generating_images", "Generate images"),
    ("generating_videos", "Generate videos"),
    ("combining_videos", "Combine clips"),
    ("uploading", "Upload"),
]

FramePair = Tuple[Optional[int], Optional[int]]


def detect_shape(num_images: int, num_videos: int, video_system: Optional[str]) -> str:
    """'extend' | 'paired' | 'chain' | 'chain_hero' (see video_frame_indices)."""
    if video_system == "grok_sequential_extend":
        return "extend"
    if num_videos >= 1 and num_images == 2 * num_videos:
        return "paired"
    if num_videos == num_images:
        return "chain_hero"
    return "chain"


def video_frame_indices(num_images: int, num_videos: int, video_system: Optional[str]) -> List[FramePair]:
    """Image indices each video uses as (first_frame, last_frame).

    * chain (N images, N-1 videos): video i bridges image i → i+1
    * chain + hero reveal (N images, N videos): as chain, last video uses image N-1 for both frames
    * paired bookends (2N images, N videos): video i uses images 2i and 2i+1
    * sequential extend (1 image): only video 0 uses an image (as its first frame);
      later clips extend the previous clip, so they reference no image.
    """
    shape = detect_shape(num_images, num_videos, video_system)
    if shape == "extend":
        return [(0, None) if i == 0 else (None, None) for i in range(num_videos)]
    if shape == "paired":
        return [(2 * i, 2 * i + 1) for i in range(num_videos)]
    last = num_images - 1
    return [(min(i, last), min(i + 1, last)) for i in range(num_videos)]


def videos_affected_by_image(num_images: int, num_videos: int, video_system: Optional[str],
                             image_index: int) -> List[int]:
    """Videos that must be regenerated when an image changes."""
    if detect_shape(num_images, num_videos, video_system) == "extend":
        # Every extend clip descends from clip 0, which starts from image 0.
        return list(range(num_videos)) if image_index == 0 else []
    return [i for i, (f, l) in enumerate(video_frame_indices(num_images, num_videos, video_system))
            if image_index in (f, l)]


def videos_affected_by_video(num_videos: int, video_system: Optional[str], video_index: int) -> List[int]:
    """Extend clips continue from the previous clip, so later clips must be redone too."""
    if video_system == "grok_sequential_extend":
        return list(range(video_index, num_videos))
    return [video_index]


# =============================================================
# Executor
# =============================================================

async def execute(pipeline_id: str):
    stage = "generating_images"
    try:
        p = await db.find_pipeline(pipeline_id)
        label = p.get("room_name") or "Pipeline"
        final_path = pack_work_dir(pipeline_id) / "final.mp4"
        resume = normalize_step(p.get("resume_from_step") or "generating_images", "pack")
        await add_log(pipeline_id, f"Video system: {p.get('video_system') or 'veo31_frame'}")

        if not (resume == "uploading" and final_path.exists()):
            stage = "generating_images"
            await enter_stage(pipeline_id, stage, 15)
            await _generate_images(pipeline_id, p)

            # Each checkpoint sits after `stage` moves on, so a pause here resumes at the next stage.
            stage = "generating_videos"
            runner.checkpoint()
            await enter_stage(pipeline_id, stage, 55)
            await _generate_videos(pipeline_id, p)

            stage = "combining_videos"
            runner.checkpoint()
            await enter_stage(pipeline_id, stage, 82)
            await _combine(pipeline_id, p, final_path)

        stage = "uploading"
        runner.checkpoint()
        await enter_stage(pipeline_id, stage, 90)
        await publish_final_video(pipeline_id, final_path, label, "Pipelines")
        await finish(pipeline_id, label)
    except Exception as e:
        await handle_stop(pipeline_id, e, stage, "Pipeline")


async def _generate_images(pipeline_id: str, p: dict):
    num_images = p["num_images"]
    prompts = p.get("image_prompts") or []
    if not prompts:
        raise RuntimeError("Pipeline has no image_prompts")
    rows = {r["index"]: r for r in await db.find_images(pipeline_id)}
    urls: List[Optional[str]] = [None] * num_images
    for i in range(num_images):
        r = rows.get(i)
        if r and r.get("status") == "completed" and r.get("url"):
            urls[i] = r["url"]

    missing = [i for i in range(num_images) if not urls[i]]
    if not missing:
        await add_log(pipeline_id, "All images already completed.")
        return
    await add_log(pipeline_id, f"Generating {len(missing)} image(s): {[i + 1 for i in missing]}")

    for i in missing:
        runner.checkpoint()
        # Chain consistency: reference the nearest earlier completed image.
        ref_url = next((urls[j] for j in range(i - 1, -1, -1) if urls[j]), None)
        slot = "first_image_model" if i == 0 else "subsequent_images_model"
        preferred = registry.normalize_service(p.get(slot))
        prompt = (rows.get(i) or {}).get("prompt") or prompts[i]
        await add_log(pipeline_id, f"Generating image {i + 1}/{num_images}...")
        url, service, gen_uuid, kie_id = await generate_image_with_fallback(
            i, prompt, ref_url, pipeline_id, p.get("aspect_ratio") or "16:9", preferred_service=preferred)
        urls[i] = url
        done = sum(1 for u in urls if u)
        await update_state(pipeline_id, {"image_urls": [u for u in urls if u],
                                         "progress": 15 + int(40 * done / num_images)})
        nocodb.sync_image(pipeline_id, i, {"Status": "completed", "Service": service, "ImageUrl": url,
                                           "SnapGenUUID": gen_uuid or "", "KieTaskId": kie_id or "",
                                           "CompletedAt": db.now_iso()})


async def _generate_videos(pipeline_id: str, p: dict):
    num_images, num_videos = p["num_images"], p["num_videos"]
    video_system = p.get("video_system") or "veo31_frame"
    aspect_ratio = p.get("aspect_ratio") or "16:9"
    prompts = p.get("video_prompts") or []
    image_urls = {r["index"]: r["url"] for r in await db.find_images(pipeline_id)
                  if r.get("status") == "completed" and r.get("url")}
    videos = {r["index"]: r for r in await db.find_videos(pipeline_id)}

    def done(i):
        v = videos.get(i)
        return bool(v and v.get("status") == "completed" and v.get("url"))

    def prompt_for(i):
        return (videos.get(i) or {}).get("prompt") or prompts[i]

    if video_system == "grok_sequential_extend":
        start = next((i for i in range(num_videos) if not done(i)), num_videos)
        if start == num_videos:
            await add_log(pipeline_id, "All videos already completed.")
            return
        prev_uuid = videos[start - 1].get("snapgen_uuid") if start > 0 else None
        if 0 not in image_urls:
            raise RuntimeError("Image 1 must be completed before extend-mode video generation")
        engine = "veo" if (p.get("video_engine") or "grok") == "veo" else "grok"
        await add_log(pipeline_id, f"Sequential extend with {engine}, starting at clip {start + 1}")
        await generate_sequential_extend_videos(
            pipeline_id, engine, [prompt_for(i) for i in range(start, num_videos)], image_urls[0],
            aspect_ratio, int(p.get("shot_duration") or 6), start_index=start, initial_prev_uuid=prev_uuid)
    else:
        frames = video_frame_indices(num_images, num_videos, video_system)
        jobs = []
        for i in range(num_videos):
            if done(i):
                continue
            f, l = frames[i]
            first_url, last_url = image_urls.get(f), image_urls.get(l)
            if not first_url or not last_url:
                raise RuntimeError(f"Video {i + 1} needs images {f + 1} and {l + 1} to be completed")
            await db.update_video(pipeline_id, i, {"first_image_url": first_url, "last_image_url": last_url})
            nocodb.sync_video(pipeline_id, i, {"FirstImageUrl": first_url, "LastImageUrl": last_url,
                                               "Status": "generating"})
            jobs.append((i, prompt_for(i), first_url, last_url))
        if not jobs:
            await add_log(pipeline_id, "All videos already completed.")
        else:
            await add_log(pipeline_id, f"Sending {len(jobs)} video generation request(s) in parallel...")
            results = await asyncio.gather(
                *[generate_frame_video(i, pr, fu, lu, pipeline_id, aspect_ratio) for i, pr, fu, lu in jobs],
                return_exceptions=True)
            # A user stop wins over provider failures; cancel wins over pause.
            for kind in (PipelineCancelled, PipelinePaused):
                for r in results:
                    if isinstance(r, kind):
                        raise r
            for (i, *_), r in zip(jobs, results):
                if isinstance(r, BaseException):
                    raise RuntimeError(f"Video {i + 1} failed: {r}")

    completed = await db.find_completed_videos(pipeline_id)
    await update_state(pipeline_id, {"video_urls": [v["url"] for v in completed], "progress": 80})


async def generate_frame_video(index: int, prompt: str, first_img: str, last_img: str,
                               pipeline_id: str, aspect_ratio: str):
    """Veo 3.1 first/last-frame clip with retries. Returns (url, uuid)."""
    step = f"Video {index + 1}"
    last_error = ""
    for attempt in range(1, config.VIDEO_MAX_RETRIES + 1):
        runner.checkpoint()
        gen_uuid = None
        try:
            await add_log(pipeline_id, f"[{step}] Attempt {attempt}/{config.VIDEO_MAX_RETRIES} - "
                                       "Submitting to SnapGen (veo-3.1-fast 1080p, 8s)...")
            gen_uuid = await snapgen.submit_veo_frames(prompt, first_img, last_img, aspect_ratio)
            await add_log(pipeline_id, f"[{step}] SnapGen submitted (uuid: {gen_uuid}). Waiting up to 30 min...")
            await db.update_video(pipeline_id, index, {"snapgen_uuid": gen_uuid, "status": "generating",
                                                       "attempt": attempt, "updated_at": db.now_iso()})
            completed = await snapgen.wait_for(gen_uuid, pipeline_id, f"{step} attempt {attempt}",
                                                 max_wait=config.VIDEO_POLL_TIMEOUT)
            vid_url = snapgen.extract_video_url(completed)
            if not vid_url:
                raise snapgen.SnapGenError("SnapGen returned no video URL")
            await _mark_video_done(pipeline_id, index, vid_url, gen_uuid, attempt)
            await add_log(pipeline_id, f"[{step}] SUCCESS on attempt {attempt}: {vid_url}")
            return vid_url, gen_uuid
        except PipelineInterrupt:
            raise
        except Exception as e:
            last_error = str(e)
            await add_log(pipeline_id, f"[{step}] Attempt {attempt}/{config.VIDEO_MAX_RETRIES} FAILED: {last_error}")
            await db.update_video(pipeline_id, index, {"error": last_error, "snapgen_uuid": gen_uuid,
                                                       "updated_at": db.now_iso()})
            if attempt < config.VIDEO_MAX_RETRIES:
                await asyncio.sleep(5)
    await _mark_video_failed(pipeline_id, index, last_error)
    raise RuntimeError(f"[{step}] All {config.VIDEO_MAX_RETRIES} attempts failed. Last error: {last_error}")


_EXTEND_ENGINES = {
    "grok": ("Grok", snapgen.submit_grok_first),
    "veo": ("Veo", snapgen.submit_veo_first),
}


async def generate_sequential_extend_videos(pipeline_id: str, engine: str, video_prompts: List[str],
                                            reference_image_url: str, aspect_ratio: str, shot_duration: int,
                                            start_index: int = 0, initial_prev_uuid: Optional[str] = None):
    """Sequential extend for Grok or Veo 3.1.

    Clip 0 is generated from the reference image; every later clip extends the
    previous clip's SnapGen uuid, so clips are strictly sequential.
    """
    label, submit_first = _EXTEND_ENGINES[engine]
    prev_uuid = initial_prev_uuid
    results = []
    for offset, prompt in enumerate(video_prompts):
        i = start_index + offset
        step = f"{label} Video {i + 1}"
        last_error = ""
        for attempt in range(1, config.VIDEO_MAX_RETRIES + 1):
            runner.checkpoint()
            try:
                if i == 0:
                    await add_log(pipeline_id, f"[{step}] Attempt {attempt}/{config.VIDEO_MAX_RETRIES} — "
                                               f"generate from image ({shot_duration}s)...")
                    gen_uuid = await submit_first(prompt, reference_image_url, aspect_ratio, shot_duration)
                else:
                    if not prev_uuid:
                        raise RuntimeError(f"No previous clip uuid to extend from for clip {i + 1}")
                    await add_log(pipeline_id, f"[{step}] Attempt {attempt}/{config.VIDEO_MAX_RETRIES} — "
                                               f"extend from uuid={prev_uuid}...")
                    gen_uuid = await snapgen.submit_extend(engine, prompt, prev_uuid)
                await add_log(pipeline_id, f"[{step}] Submitted (uuid={gen_uuid}). Polling (up to 30 min)...")
                await db.update_video(pipeline_id, i, {"snapgen_uuid": gen_uuid, "status": "generating",
                                                       "attempt": attempt, "updated_at": db.now_iso()})
                completed = await snapgen.wait_for(gen_uuid, pipeline_id, f"{step} attempt {attempt}",
                                                     max_wait=config.VIDEO_POLL_TIMEOUT)
                vid_url = snapgen.extract_video_url(completed)
                if not vid_url:
                    raise snapgen.SnapGenError(f"{label} returned no video URL")
                await _mark_video_done(pipeline_id, i, vid_url, gen_uuid, attempt)
                await add_log(pipeline_id, f"[{step}] SUCCESS on attempt {attempt}: {vid_url}")
                prev_uuid = gen_uuid
                results.append((vid_url, gen_uuid))
                break
            except PipelineInterrupt:
                raise
            except Exception as e:
                last_error = str(e)
                await add_log(pipeline_id, f"[{step}] Attempt {attempt}/{config.VIDEO_MAX_RETRIES} FAILED: {last_error}")
                await db.update_video(pipeline_id, i, {"error": last_error, "updated_at": db.now_iso()})
                if attempt < config.VIDEO_MAX_RETRIES:
                    await asyncio.sleep(5)
        else:
            await _mark_video_failed(pipeline_id, i, last_error)
            raise RuntimeError(f"[{step}] All {config.VIDEO_MAX_RETRIES} attempts failed. Last: {last_error}")
    return results


async def _mark_video_done(pipeline_id: str, index: int, url: str, gen_uuid: str, attempt: int):
    ts = db.now_iso()
    await db.update_video(pipeline_id, index, {"status": "completed", "url": url, "snapgen_uuid": gen_uuid,
                                               "attempt": attempt, "error": None, "completed_at": ts,
                                               "updated_at": ts})
    nocodb.sync_video(pipeline_id, index, {"Status": "completed", "VideoUrl": url,
                                           "SnapGenUUID": gen_uuid or "", "CompletedAt": ts})


async def _mark_video_failed(pipeline_id: str, index: int, error: str):
    await db.update_video(pipeline_id, index, {"status": "failed", "error": error, "updated_at": db.now_iso()})
    nocodb.sync_video(pipeline_id, index, {"Status": "failed", "ErrorMessage": error})


# =============================================================
# FFmpeg combine
# =============================================================

_ENCODE_ARGS = ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]


def normalize_clip_cmd(src: str, dst: str, has_audio: bool) -> List[str]:
    """Re-encode a clip to common codec params; add a silent stereo track if it has no audio,
    so every clip concatenates with the same stream layout."""
    if has_audio:
        return ["ffmpeg", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0", *_ENCODE_ARGS, dst]
    return ["ffmpeg", "-y", "-i", src, "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map", "0:v:0", "-map", "1:a:0", "-shortest", *_ENCODE_ARGS, dst]


async def combine_clips(clips: List[Path], out_path: Path, workdir: Path, log=None):
    """Normalise every clip and concatenate them into `out_path`. Raises on any FFmpeg failure."""
    procs = runner.current_procs()
    normalized = []
    for i, clip in enumerate(clips):
        runner.check_cancelled()
        has_audio = await asyncio.to_thread(ss.probe_has_audio, str(clip))
        norm = workdir / f"norm_{i}.mp4"
        rc, err = await ss.run_ffmpeg(normalize_clip_cmd(str(clip), str(norm), has_audio), procs)
        if rc != 0:
            raise RuntimeError(f"FFmpeg normalisation of clip {i + 1} failed: {err[-500:]}")
        normalized.append(norm)
    concat_list = workdir / "concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for n in normalized:
            f.write("file '{}'\n".format(n.as_posix().replace("'", "'\\''")))
    if log:
        await log("Running FFmpeg concat...")
    rc, err = await ss.run_ffmpeg(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(out_path)], procs)
    if rc != 0:
        raise RuntimeError(f"FFmpeg concat failed: {err[-500:]}")


async def _combine(pipeline_id: str, p: dict, final_path: Path):
    num_videos = p["num_videos"]
    videos = {v["index"]: v for v in await db.find_completed_videos(pipeline_id)}
    missing = [i + 1 for i in range(num_videos) if not (videos.get(i) or {}).get("url")]
    if missing:
        raise RuntimeError(f"Cannot combine: video(s) {missing} are not completed")
    urls = [videos[i]["url"] for i in range(num_videos)]

    work = pack_work_dir(pipeline_id)
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        tmpdir = Path(tmp)
        clips = []
        for i, url in enumerate(urls):
            runner.check_cancelled()
            dest = tmpdir / f"video_{i}.mp4"
            await download_file(url, dest)
            clips.append(dest)
            await add_log(pipeline_id, f"Downloaded video {i + 1}/{len(urls)}")
        out = tmpdir / "final_output.mp4"
        if len(clips) == 1:
            shutil.copy(clips[0], out)
        else:
            await combine_clips(clips, out, tmpdir, log=lambda m: add_log(pipeline_id, m))
        os.replace(out, final_path)
    await add_log(pipeline_id, "Videos combined successfully.")
    await update_state(pipeline_id, {"video_urls": urls, "progress": 88})
