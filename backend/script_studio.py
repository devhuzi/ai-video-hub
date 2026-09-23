"""Script Studio — TTS narration + word-timed karaoke subtitles + Ken Burns images.

Stateless helpers; the pipeline executor (app/pipelines/script.py) handles
orchestration, persistence and the provider calls (TTS, LLM, images):

    - probe_audio_duration()          — ffprobe → seconds (sync; call via asyncio.to_thread)
    - probe_has_audio()               — ffprobe → whether a file has an audio stream (sync)
    - estimate_word_timings()         — split script into words with even timing
    - word_timings_from_timestamps()  — script words timed by TTS word timestamps
    - extract_json()                  — parse JSON out of an LLM reply
    - split_script_into_scenes()      — LLM grouping of words → scene list
    - generate_scene_image_prompts()  — LLM: all scenes → image prompts in one call
    - resolution_for_aspect()         — aspect ratio → output (width, height)
    - build_subtitle_ass()            — karaoke ASS with yellow active word
    - remove_logo_background()        — rembg → transparent PNG (sync, CPU heavy)
    - build_scene_clip_cmd()          — per-scene Ken Burns clip command
    - build_concat_cmd()              — concat scene clips command
    - build_final_mux_cmd()           — subtitles + logo + narration mux command
    - run_ffmpeg()                    — run a command; killable on cancel

LLM calls are injected via a `completion_fn` parameter so this module has no
dependency on the app package.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Set, Tuple


logger = logging.getLogger(__name__)

# ---------- Data types ----------

@dataclass
class WordTiming:
    word: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Scene:
    idx: int
    text: str
    start: float
    end: float
    image_prompt: str = ""
    image_url: Optional[str] = None
    animated_clip_url: Optional[str] = None
    words: List[WordTiming] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.01, self.end - self.start)


# =============================================================
# Audio duration + even-slice word timings
# =============================================================

def probe_audio_duration(audio_path: str) -> float:
    """Return the duration of an audio file in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[:300]}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe returned unexpected output: {result.stdout!r}")


def probe_has_audio(media_path: str) -> bool:
    """Return True if the file contains at least one audio stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", media_path],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {media_path}: {result.stderr[:300]}")
    return bool(result.stdout.strip())


def resolution_for_aspect(aspect_ratio: str) -> Tuple[int, int]:
    """Output frame size for an aspect ratio (1080p class)."""
    return {"9:16": (1080, 1920), "1:1": (1080, 1080)}.get(aspect_ratio, (1920, 1080))


def _split_script_into_words(script: str) -> List[str]:
    """Tokenize the script into display words.

    We split on whitespace but keep punctuation attached to the preceding word
    so the subtitle reads naturally ("hello, world" → ["hello,", "world"]).
    """
    return [tok for tok in script.split() if tok.strip()]


def estimate_word_timings(script: str, audio_duration: float) -> List[WordTiming]:
    """Split the script into words and assign each an equal slice of the audio.

    TTS narration is paced evenly enough that this gives a karaoke effect that
    tracks the audio well without needing an ASR service. Trailing punctuation
    slightly extends the last word so nothing overruns the clip.
    """
    words = _split_script_into_words(script)
    if not words or audio_duration <= 0:
        return []
    per_word = audio_duration / len(words)
    timings: List[WordTiming] = []
    for i, w in enumerate(words):
        start = i * per_word
        end = (i + 1) * per_word if i < len(words) - 1 else audio_duration
        timings.append(WordTiming(word=w, start=start, end=end))
    return timings


# =============================================================
# Word timestamps returned by the TTS provider
# =============================================================

_WORD_KEYS = ("word", "text", "token", "value")
_START_KEYS = ("start", "start_time", "start_s", "start_sec", "startTime", "start_seconds")
_END_KEYS = ("end", "end_time", "end_s", "end_sec", "endTime", "end_seconds")


def _first_number(d: dict, keys) -> Optional[float]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _words_from_character_alignment(a: dict) -> List[WordTiming]:
    """ElevenLabs-style alignment: parallel character / start / end arrays."""
    chars = a.get("characters") or []
    starts = a.get("character_start_times_seconds") or []
    ends = a.get("character_end_times_seconds") or []
    words: List[WordTiming] = []
    cur, cur_start, cur_end = "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if not isinstance(ch, str):
            return []
        if ch.isspace():
            if cur:
                words.append(WordTiming(cur, cur_start, cur_end))
            cur, cur_start = "", None
            continue
        if cur_start is None:
            cur_start = float(s)
        cur += ch
        cur_end = float(e)
    if cur:
        words.append(WordTiming(cur, cur_start, cur_end))
    return words


def parse_word_timestamps(raw) -> List[WordTiming]:
    """Best-effort parse of provider word timestamps. Returns [] if the shape is unknown.

    Accepted: a list of {word|text, start|start_time, end|end_time} objects, a
    dict wrapping such a list (`words`, `timestamps`, `alignment`), or an
    ElevenLabs character alignment. Whitespace-only entries are dropped.
    """
    if isinstance(raw, dict):
        if "characters" in raw:
            return _words_from_character_alignment(raw)
        for key in ("words", "timestamps", "alignment", "normalized_alignment"):
            if key in raw:
                return parse_word_timestamps(raw[key])
        return []
    if not isinstance(raw, list):
        return []
    out: List[WordTiming] = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        word = next((item[k] for k in _WORD_KEYS if isinstance(item.get(k), str)), None)
        start, end = _first_number(item, _START_KEYS), _first_number(item, _END_KEYS)
        if word is None or start is None or end is None:
            return []
        if not word.strip() or item.get("type") in ("spacing", "audio_event"):
            continue
        out.append(WordTiming(word.strip(), start, end))
    return out


def word_timings_from_timestamps(script: str, raw, audio_duration: float) -> List[WordTiming]:
    """Script words timed by the provider's timestamps, or [] if they can't be trusted.

    Subtitles show the script's own words (with punctuation), so the provider's
    timestamps are only used when they line up one-to-one with them and are
    monotonic and inside the audio.
    """
    stamps = parse_word_timestamps(raw)
    words = _split_script_into_words(script)
    if not stamps or len(stamps) != len(words):
        return []
    prev_end = 0.0
    for t in stamps:
        if t.start < 0 or t.end < t.start or t.start + 0.05 < prev_end or t.end > audio_duration + 1.0:
            return []
        prev_end = t.end
    return [WordTiming(word=w, start=t.start, end=min(t.end, audio_duration)) for w, t in zip(words, stamps)]


# =============================================================
# LLM output parsing
# =============================================================

def extract_json(text: str):
    """Parse JSON from an LLM reply: plain JSON, a ```json fenced block, or the
    first {...} / [...] span embedded in prose. Raises ValueError otherwise."""
    cleaned = (text or "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json|JSON)?\s*\n?(.*?)```", cleaned, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(cleaned):
        if ch in "{[":
            try:
                return decoder.raw_decode(cleaned[i:])[0]
            except json.JSONDecodeError:
                continue
    raise ValueError(f"LLM reply contained no parseable JSON: {cleaned[:300]!r}")


# =============================================================
# Scene splitting (LLM)
# =============================================================

# (model, system, user, json_schema) -> reply text
CompletionFn = Callable[[str, str, str, Optional[dict]], Awaitable[str]]

SCENES_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {"word_start": {"type": "integer"}, "word_end": {"type": "integer"},
                       "description": {"type": "string"}},
        "required": ["word_start", "word_end", "description"],
        "additionalProperties": False,
    }}},
    "required": ["scenes"],
    "additionalProperties": False,
}

PROMPTS_SCHEMA = {
    "type": "object",
    "properties": {"prompts": {"type": "array", "items": {"type": "string"}}},
    "required": ["prompts"],
    "additionalProperties": False,
}


async def split_script_into_scenes(
    script: str,
    word_timings: List[WordTiming],
    *,
    completion_fn: CompletionFn,
    ai_model: str,
    target_scene_seconds: float = 5.0,
) -> List[Scene]:
    """Ask the LLM to group consecutive words into coherent visual scenes.

    Returns Scene objects with start/end seconds derived from the word timings.
    The LLM output must be JSON: {"scenes": [{"word_start": i, "word_end": j, "description": "..."}]}
    """
    if not word_timings:
        return []

    # Build the numbered word list we give the LLM so it can reference exact indices.
    numbered = [f"[{i}] {w.word}" for i, w in enumerate(word_timings)]
    words_blob = " ".join(numbered)
    total_duration = word_timings[-1].end

    system_message = (
        "You split narration into visual scenes for a video. Each scene must be a "
        "single coherent visual moment that can be shown as one image or short clip. "
        "Group CONSECUTIVE words only — never skip or reorder. Every word in the "
        "narration must belong to exactly one scene. Scenes should average roughly "
        f"{target_scene_seconds:.0f} seconds of spoken audio. Respond ONLY with JSON."
    )
    user_message = (
        f"Script: {script}\n\n"
        f"Total narration duration: {total_duration:.2f} seconds.\n"
        f"Numbered words (each token is [index] word):\n{words_blob}\n\n"
        "Split the narration into scenes. For each scene, return the first and last "
        "word indices (inclusive) and a concise one-sentence visual description of "
        "what should be shown on screen.\n\n"
        'Return only this JSON: {"scenes": [{"word_start": 0, "word_end": 12, '
        '"description": "A wide establishing shot of ..."}]}'
    )

    raw = await completion_fn(ai_model, system_message, user_message, SCENES_SCHEMA)
    parsed = extract_json(raw)
    scene_defs = (parsed.get("scenes") if isinstance(parsed, dict) else parsed) or []

    scenes: List[Scene] = []
    used_end = -1
    for i, sd in enumerate(scene_defs):
        ws = int(sd.get("word_start", used_end + 1))
        we = int(sd.get("word_end", ws))
        ws = max(0, min(ws, len(word_timings) - 1))
        we = max(ws, min(we, len(word_timings) - 1))
        if ws <= used_end:
            ws = used_end + 1
        if ws >= len(word_timings):
            break
        if we < ws:
            we = ws
        span_words = word_timings[ws:we + 1]
        scenes.append(Scene(
            idx=i,
            text=" ".join(w.word for w in span_words),
            start=span_words[0].start,
            end=span_words[-1].end,
            image_prompt=(sd.get("description") or "").strip(),
            words=list(span_words),
        ))
        used_end = we

    # Safety net — if the LLM left a tail unassigned, append it as one final scene.
    if used_end < len(word_timings) - 1:
        tail = word_timings[used_end + 1:]
        scenes.append(Scene(
            idx=len(scenes),
            text=" ".join(w.word for w in tail),
            start=tail[0].start,
            end=tail[-1].end,
            image_prompt="Continuation of the previous scene.",
            words=list(tail),
        ))
    return scenes


# =============================================================
# Per-scene image prompt generation (LLM)
# =============================================================

async def generate_scene_image_prompts(
    scenes: List[Scene],
    *,
    completion_fn: CompletionFn,
    ai_model: str,
    global_style: str = "",
) -> List[str]:
    """Turn all scenes into detailed image prompts in a SINGLE LLM call.

    Sends every scene (description + narration) in one batch and asks the LLM
    to return a JSON array of prompts — one per scene, in order.
    """
    if not scenes:
        return []

    style_line = f"\nGlobal visual style to apply to ALL images: {global_style}" if global_style.strip() else ""
    system_message = (
        "You are a cinematic image prompt engineer. You will be given a list of "
        "scenes, each with a visual description and the narration it accompanies. "
        "For EVERY scene, write one highly detailed image prompt suitable for "
        "photorealistic image generation. Be concrete about camera framing, "
        "lighting, color, and composition. No negative prompts. "
        'Return ONLY a JSON object {"prompts": [...]} with one prompt string per '
        "scene, in order. No markdown, no preamble."
    )

    scene_entries = []
    for i, s in enumerate(scenes):
        scene_entries.append(
            f"Scene {i+1}:\n"
            f"  Description: {s.image_prompt}\n"
            f"  Narration: \"{s.text}\"\n"
            f"  Duration: {s.duration:.1f}s"
        )

    user_message = (
        f"Generate image prompts for all {len(scenes)} scenes below.{style_line}\n\n"
        + "\n\n".join(scene_entries)
        + '\n\nReturn a JSON object with exactly '
        + str(len(scenes))
        + ' prompt strings: {"prompts": ["prompt for scene 1", "prompt for scene 2", ...]}'
    )

    raw = await completion_fn(ai_model, system_message, user_message, PROMPTS_SCHEMA)
    parsed = extract_json(raw)
    if isinstance(parsed, dict):
        parsed = parsed.get("prompts")
    if not isinstance(parsed, list):
        raise RuntimeError(f"Expected a list of prompts from the LLM, got: {type(parsed).__name__}")

    # Ensure we have exactly one prompt per scene (pad or trim)
    out: List[str] = []
    for i in range(len(scenes)):
        if i < len(parsed) and isinstance(parsed[i], str) and parsed[i].strip():
            out.append(parsed[i].strip().strip('"').strip("'").strip())
        else:
            out.append(scenes[i].image_prompt or f"Scene {i+1}")
    return out


# =============================================================
# ASS karaoke subtitles (yellow active word)
# =============================================================

def build_subtitle_ass(
    word_timings: List[WordTiming],
    *,
    play_res_x: int = 1920,
    play_res_y: int = 1080,
    fontsize: int = 56,
    margin_v: int = 90,
    primary_yellow_bgr: str = "&H0000FFFF",   # active word (sung) → yellow
    secondary_white_bgr: str = "&H00FFFFFF",  # not yet spoken → white
    outline_black: str = "&H00000000",
    back_shadow: str = "&H80000000",
    group_size: int = 8,
    max_chars: Optional[int] = None,
) -> str:
    """Return an ASS subtitle file as a string with karaoke-style per-word highlighting.

    Words are packed into on-screen lines of at most `group_size` words and
    `max_chars` characters (default: what fits the frame width at `fontsize`),
    so portrait frames get short lines instead of text running off the edges.
    ASS \\k advances from SecondaryColour → PrimaryColour, so Primary = yellow
    and Secondary = white gives the effect "words turn yellow as they are spoken".
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{fontsize},{primary_yellow_bgr},{secondary_white_bgr},"
        f"{outline_black},{back_shadow},-1,0,0,0,100,100,0,0,1,3,1,2,80,80,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events: List[str] = []
    if not word_timings:
        return header

    def fmt_ass_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds - (h * 3600 + m * 60)
        return f"{h}:{m:02d}:{s:05.2f}"

    if max_chars is None:
        # Bold sans glyphs average ~0.55 em; 80px side margins are set in the style.
        max_chars = max(12, int((play_res_x - 160) / (fontsize * 0.55)))

    groups: List[List[WordTiming]] = []
    current: List[WordTiming] = []
    for w in word_timings:
        candidate_len = len(" ".join(x.word for x in current + [w]))
        if current and (len(current) >= group_size or candidate_len > max_chars):
            groups.append(current)
            current = []
        current.append(w)
    if current:
        groups.append(current)

    for group in groups:
        line_start = group[0].start
        line_end = group[-1].end
        parts: List[str] = []
        cursor = line_start
        for w in group:
            # Gap before the word (if any) — render as silent karaoke
            gap_cs = max(0, int(round((w.start - cursor) * 100)))
            if gap_cs > 0:
                parts.append(f"{{\\k{gap_cs}}}")
            dur_cs = max(1, int(round((w.end - w.start) * 100)))
            safe_word = _escape_ass(w.word)
            parts.append(f"{{\\k{dur_cs}}}{safe_word} ")
            cursor = w.end
        text = "".join(parts).rstrip()
        events.append(
            f"Dialogue: 0,{fmt_ass_time(line_start)},{fmt_ass_time(line_end)},"
            f"Default,,0,0,0,,{text}"
        )

    return header + "\n".join(events) + "\n"


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


# =============================================================
# Watermark prep (rembg)
# =============================================================

def remove_logo_background(logo_path: str, out_path: str) -> str:
    """Run rembg on a logo image; output a PNG with transparent background.

    If rembg is not installed we just copy the input so the pipeline keeps working
    (the user can manually supply an already-transparent PNG).
    """
    try:
        from rembg import remove  # type: ignore
        with open(logo_path, "rb") as f:
            input_bytes = f.read()
        output_bytes = remove(input_bytes)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(output_bytes)
        return out_path
    except ImportError:
        logger.warning("rembg not installed — skipping background removal, copying logo as-is.")
        shutil.copy(logo_path, out_path)
        return out_path
    except Exception as e:
        logger.warning(f"rembg failed ({e}); copying logo as-is.")
        shutil.copy(logo_path, out_path)
        return out_path


# =============================================================
# FFmpeg assembly
# =============================================================

def build_scene_clip_cmd(
    image_path: str,
    duration: float,
    out_path: str,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
) -> List[str]:
    """Build an ffmpeg command that turns a still image into a Ken-Burns clip."""
    total_frames = max(1, int(round(duration * fps)))
    # Gentle zoom from 1.0 → 1.08 over the clip
    zoom_expr = "if(lte(zoom,1.0),1.08,max(1.001,zoom-0.0006))"
    vf = (
        f"scale={width * 2}:{height * 2}:flags=lanczos,"
        f"zoompan=z='{zoom_expr}':d={total_frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={width}x{height}:fps={fps},"
        f"format=yuv420p"
    )
    return [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        out_path,
    ]


def build_concat_cmd(scene_clips: List[str], concat_list_path: str, out_path: str) -> List[str]:
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for clip in scene_clips:
            safe = clip.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    return [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        out_path,
    ]


def build_final_mux_cmd(
    video_path: str,
    narration_mp3: str,
    subtitles_ass: str,
    out_path: str,
    *,
    logo_path: Optional[str] = None,
    logo_opacity: float = 0.35,
    logo_margin: int = 40,
    logo_width: Optional[int] = None,
) -> List[str]:
    """Build the ffmpeg command that burns in subtitles, optionally overlays a
    watermark logo, and muxes the narration audio track."""
    # The `subtitles` filter reads an .ass file and burns it into the frame
    # respecting its embedded karaoke timing.
    subs_arg = subtitles_ass.replace("\\", "/").replace(":", "\\:")
    filter_chain: List[str] = [f"[0:v]subtitles='{subs_arg}'[subbed]"]

    if logo_path:
        # Feed the logo as a second video input and overlay with alpha mixing.
        # Scaled to a fixed share of the frame and pinned top-right, clear of the subtitles.
        scale = f"scale={logo_width}:-1," if logo_width else ""
        filter_chain.append(
            f"[2:v]{scale}format=rgba,colorchannelmixer=aa={logo_opacity}[logo]"
        )
        filter_chain.append(
            f"[subbed][logo]overlay=W-w-{logo_margin}:{logo_margin}[outv]"
        )
    else:
        filter_chain.append("[subbed]null[outv]")

    filter_complex = ";".join(filter_chain)

    cmd: List[str] = ["ffmpeg", "-y", "-i", video_path, "-i", narration_mp3]
    if logo_path:
        cmd.extend(["-i", logo_path])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-shortest",
        out_path,
    ])
    return cmd


async def run_ffmpeg(cmd: List[str], procs: Optional[Set[asyncio.subprocess.Process]] = None) -> Tuple[int, str]:
    """Run an ffmpeg/ffprobe command; return (returncode, stderr_tail).

    The process is registered in `procs` (the run's process set) while it runs
    so a cancel request can kill it, and it is killed if this coroutine is
    cancelled — FFmpeg never outlives a cancelled pipeline.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    if procs is not None:
        procs.add(proc)
    try:
        _stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    finally:
        if procs is not None:
            procs.discard(proc)
    return proc.returncode or 0, stderr.decode("utf-8", errors="replace")[-2000:]
