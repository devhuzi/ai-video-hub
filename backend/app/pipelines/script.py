"""Script Studio pipelines: script → TTS → scenes → images → Ken Burns clips → subtitled video.

Resumable: every paid artefact is persisted (narration file, scene rows, scene
image URLs + local copies) and reused on retry. Local FFmpeg work is rebuilt
whenever its inputs are newer or missing.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import List

import database as db
import script_studio as ss

from .. import config, uploads
from ..providers import images, openrouter, registry, tts
from . import runner
from .common import (add_log, download_file, enter_stage, finish, handle_stop, normalize_step,
                     publish_final_video, script_work_dir, update_state)

STEPS = [
    ("generating_tts", "Generate narration"),
    ("estimating_word_timings", "Word timings"),
    ("splitting_scenes", "Split into scenes"),
    ("generating_scene_prompts", "Write scene prompts"),
    ("generating_scene_images", "Generate scene images"),
    ("building_scene_clips", "Build scene clips"),
    ("final_mux", "Final mux"),
    ("uploading", "Upload"),
]

# Scene row statuses: 'split' (LLM split done, prompt not written yet),
# 'pending' (prompt ready), 'image_ready' (image generated).


def _is_fresh(output: Path, *inputs: Path) -> bool:
    if not output.exists() or output.stat().st_size == 0:
        return False
    out_mtime = output.stat().st_mtime
    return all(i.exists() and i.stat().st_mtime <= out_mtime for i in inputs)


def _clear_local_scene_files(work: Path):
    for pattern in ("scene_*.png", "clip_*.mp4"):
        for f in work.glob(pattern):
            f.unlink(missing_ok=True)


async def execute(pipeline_id: str):
    stage = "generating_tts"
    try:
        p = await db.find_pipeline(pipeline_id)
        label = p.get("room_name") or "Script Pipeline"
        script_text = p.get("script_text") or ""
        aspect_ratio = p.get("aspect_ratio") or "16:9"
        ai_model = p.get("ai_model") or config.DEFAULT_LLM_MODEL
        if not script_text.strip():
            raise RuntimeError("Script pipeline has no script_text")
        image_model = registry.normalize_scene_image_model(p.get("image_gen_model"))
        missing = registry.script_missing_env(image_model)
        if missing:
            raise RuntimeError(registry.missing_env_message(missing))

        async def log(msg: str):
            await add_log(pipeline_id, msg)

        work = script_work_dir(pipeline_id)
        work.mkdir(parents=True, exist_ok=True)
        width, height = ss.resolution_for_aspect(aspect_ratio)
        final_out = work / "final.mp4"
        procs = runner.current_procs()
        resume = normalize_step(p.get("resume_from_step") or "generating_tts", "script")

        if not (resume == "uploading" and final_out.exists()):
            # ---- 1. TTS ----
            stage = "generating_tts"
            await enter_stage(pipeline_id, stage, 5)
            narration = work / "narration.mp3"
            stamps_file = work / "narration_timestamps.json"
            tts_ran = False
            if narration.exists() and narration.stat().st_size > 0:
                await add_log(pipeline_id, "Reusing existing narration audio.")
            else:
                runner.checkpoint()
                stamps_file.unlink(missing_ok=True)
                provider, stamps = await tts.synthesize(script_text, p.get("tts_voice_id") or "", narration, log)
                if stamps:
                    stamps_file.write_text(json.dumps(stamps), encoding="utf-8")
                await add_log(pipeline_id, f"Narration generated with {registry.LABELS[provider]}.")
                tts_ran = True
            await update_state(pipeline_id, {"narration_audio_url": str(narration), "progress": 15})

            # ---- 2. Word timings: provider timestamps when usable, else an even estimate ----
            stage = "estimating_word_timings"
            await enter_stage(pipeline_id, stage, 20)
            duration = await asyncio.to_thread(ss.probe_audio_duration, str(narration))
            word_timings = _timestamp_timings(stamps_file, script_text, duration)
            source = "TTS word timestamps"
            if not word_timings:
                word_timings = ss.estimate_word_timings(script_text, duration)
                source = "even estimate"
            if not word_timings:
                raise RuntimeError("Could not estimate word timings from script")
            await add_log(pipeline_id, f"Audio is {duration:.1f}s; {len(word_timings)} words (timings: {source})")

            # ---- 3. Scene split ----
            stage = "splitting_scenes"
            await enter_stage(pipeline_id, stage, 30)
            rows = await db.find_scenes(pipeline_id)
            if rows and tts_ran:
                # New narration: rescale every scene to its length (exact for even timings, close otherwise).
                ratio = duration / max(rows[-1]["end_sec"], 0.01)
                for r in rows:
                    await db.update_scene(pipeline_id, r["index"], {
                        "start_sec": r["start_sec"] * ratio, "end_sec": r["end_sec"] * ratio,
                        "updated_at": db.now_iso()})
                for f in work.glob("clip_*.mp4"):
                    f.unlink(missing_ok=True)
                rows = await db.find_scenes(pipeline_id)
            if rows:
                await add_log(pipeline_id, f"Reusing {len(rows)} persisted scenes.")
            else:
                runner.checkpoint()
                await add_log(pipeline_id, "Splitting script into scenes...")
                scenes = await ss.split_script_into_scenes(script_text, word_timings,
                                                           completion_fn=openrouter.complete, ai_model=ai_model)
                if not scenes:
                    raise RuntimeError("Scene split produced no scenes")
                _clear_local_scene_files(work)
                ts = db.now_iso()
                await db.replace_scenes(pipeline_id, [{
                    "text": s.text, "start_sec": s.start, "end_sec": s.end, "image_prompt": s.image_prompt,
                    "image_url": None, "status": "split", "created_at": ts, "updated_at": ts,
                } for s in scenes])
                rows = await db.find_scenes(pipeline_id)
                await add_log(pipeline_id, f"Split into {len(rows)} scenes")

            # ---- 4. Detailed image prompts (single LLM call) ----
            stage = "generating_scene_prompts"
            await enter_stage(pipeline_id, stage, 35)
            if any(r.get("status") == "split" for r in rows):
                runner.checkpoint()
                await add_log(pipeline_id, "Generating detailed image prompts for each scene...")
                scene_objs = [ss.Scene(idx=r["index"], text=r["text"], start=r["start_sec"], end=r["end_sec"],
                                       image_prompt=r.get("image_prompt") or "") for r in rows]
                prompts = await ss.generate_scene_image_prompts(
                    scene_objs, completion_fn=openrouter.complete, ai_model=ai_model,
                    global_style=p.get("global_style") or "")
                for r, prompt in zip(rows, prompts):
                    await db.update_scene(pipeline_id, r["index"], {"image_prompt": prompt, "status": "pending",
                                                                    "updated_at": db.now_iso()})
                rows = await db.find_scenes(pipeline_id)
            await update_state(pipeline_id, {"num_images": len(rows), "progress": 40})

            # ---- 5. One image per scene ----
            stage = "generating_scene_images"
            await enter_stage(pipeline_id, stage, 45)
            await add_log(pipeline_id, f"Using image model: {image_model}")
            image_paths: List[Path] = []
            for n, r in enumerate(rows):
                local = work / f"scene_{n:03d}.png"
                url = r.get("image_url") if r.get("status") == "image_ready" else None
                if not url:
                    runner.checkpoint()
                    await add_log(pipeline_id, f"Generating image for scene {n + 1}/{len(rows)}...")
                    try:
                        result = await images.generate_scene_image(image_model, r["image_prompt"], aspect_ratio,
                                                                   pipeline_id, f"Scene {n + 1}")
                    except runner.PipelineInterrupt:
                        raise
                    except Exception as e:
                        raise RuntimeError(f"Scene {n + 1} image failed: {e}") from e
                    url = result.url
                    await db.update_scene(pipeline_id, r["index"], {
                        "image_url": url, "status": "image_ready", "image_model": result.model,
                        "image_service": result.service, "credits": result.credits, "updated_at": db.now_iso()})
                    local.unlink(missing_ok=True)
                if not local.exists():
                    await download_file(url, local)
                image_paths.append(local)
                await update_state(pipeline_id, {"progress": 45 + int(30 * (n + 1) / len(rows))})

            # ---- 6. Ken Burns clip per scene (local, rebuilt when stale) ----
            stage = "building_scene_clips"
            runner.checkpoint()
            await enter_stage(pipeline_id, stage, 78)
            clip_paths: List[Path] = []
            for n, (r, img) in enumerate(zip(rows, image_paths)):
                clip = work / f"clip_{n:03d}.mp4"
                if not _is_fresh(clip, img):
                    tmp_clip = work / f"clip_{n:03d}.tmp.mp4"
                    cmd = ss.build_scene_clip_cmd(image_path=str(img), duration=max(0.01, r["end_sec"] - r["start_sec"]),
                                                  out_path=str(tmp_clip), width=width, height=height)
                    rc, err = await ss.run_ffmpeg(cmd, procs)
                    if rc != 0:
                        raise RuntimeError(f"Scene clip {n + 1} ffmpeg failed: {err[-400:]}")
                    os.replace(tmp_clip, clip)
                clip_paths.append(clip)

            # ---- 7. Concat + subtitles + watermark + narration ----
            stage = "final_mux"
            runner.checkpoint()
            await enter_stage(pipeline_id, stage, 85)
            concat_out = work / "concat.mp4"
            rc, err = await ss.run_ffmpeg(ss.build_concat_cmd([str(c) for c in clip_paths],
                                                              str(work / "concat.txt"), str(concat_out)), procs)
            if rc != 0:
                raise RuntimeError(f"Concat ffmpeg failed: {err[-400:]}")
            subs_path = work / "subs.ass"
            # Sized from the shorter side so portrait text isn't twice as big as landscape;
            # portrait captions sit higher, above the platform UI on Shorts/Reels/TikTok.
            short_side = min(width, height)
            margin_v = int(height * (0.18 if height > width else 0.08))
            subs_path.write_text(ss.build_subtitle_ass(word_timings, play_res_x=width, play_res_y=height,
                                                       fontsize=int(short_side * 0.055), margin_v=margin_v),
                                 encoding="utf-8")
            await update_state(pipeline_id, {"subtitle_ass_path": str(subs_path)})
            logo = await _prepare_watermark(pipeline_id, p.get("watermark_logo_url"), work)
            await add_log(pipeline_id, "Muxing narration + subtitles + watermark into final video...")
            tmp_final = work / "final.tmp.mp4"
            rc, err = await ss.run_ffmpeg(ss.build_final_mux_cmd(
                video_path=str(concat_out), narration_mp3=str(narration), subtitles_ass=str(subs_path),
                out_path=str(tmp_final), logo_path=str(logo) if logo else None,
                logo_width=int(width * 0.12), logo_margin=int(short_side * 0.04)), procs)
            if rc != 0:
                raise RuntimeError(f"Final mux ffmpeg failed: {err[-500:]}")
            os.replace(tmp_final, final_out)

        # ---- 8. Publish ----
        stage = "uploading"
        runner.checkpoint()
        await enter_stage(pipeline_id, stage, 93)
        await publish_final_video(pipeline_id, final_out, label, "Script_Studio")
        await finish(pipeline_id, label)
    except Exception as e:
        await handle_stop(pipeline_id, e, stage, "Script pipeline")


def _timestamp_timings(stamps_file: Path, script_text: str, duration: float) -> List[ss.WordTiming]:
    if not stamps_file.exists():
        return []
    try:
        raw = json.loads(stamps_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return ss.word_timings_from_timestamps(script_text, raw, duration)


async def _prepare_watermark(pipeline_id: str, stored_ref, work: Path):
    """Resolve the stored logo reference inside the uploads dir and strip its background."""
    if not stored_ref:
        return None
    logo = uploads.resolve_stored_reference(stored_ref)
    if logo is None:
        await add_log(pipeline_id, f"WARNING: watermark logo '{stored_ref}' not found on the server — "
                                   "rendering without a watermark.")
        return None
    # Cached beside the upload: background removal takes ~1 min on CPU and the
    # same logo is typically reused across many runs.
    transparent = logo.with_name(f"{logo.stem}.nobg.png")
    if _is_fresh(transparent, logo):
        await add_log(pipeline_id, "Reusing background-removed watermark logo.")
        return transparent
    await add_log(pipeline_id, "Preparing watermark logo (background removal)...")
    try:
        return Path(await asyncio.to_thread(ss.remove_logo_background, str(logo), str(transparent)))
    except Exception as e:
        await add_log(pipeline_id, f"Watermark background removal failed ({e}) — using the original logo.")
        return logo
