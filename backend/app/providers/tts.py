"""Narration: ElevenLabs Multilingual v2 via fal.ai, falling back to Kie.ai.

Neither provider has a voice-list API, so the voices are a static list of
ElevenLabs premade voice names, which both providers accept by name.
"""
from pathlib import Path
from typing import Awaitable, Callable, Optional, Tuple

from ..pipelines.common import download_file
from ..pipelines.runner import PipelineInterrupt
from . import fal, kie, registry

VOICE_NAMES = [
    "Rachel", "Aria", "Roger", "Sarah", "Laura", "Charlie", "George", "Callum", "River", "Liam",
    "Charlotte", "Alice", "Matilda", "Will", "Jessica", "Eric", "Chris", "Brian", "Daniel", "Lily", "Bill",
]
VOICES = [{"id": name, "name": name} for name in VOICE_NAMES]

Log = Callable[[str], Awaitable[None]]


class TTSError(RuntimeError):
    pass


def primary_provider() -> Optional[str]:
    return next((p for p in ("fal", "kie") if registry.configured(p)), None)


async def synthesize(text: str, voice: str, out_path: Path, log: Log) -> Tuple[str, object]:
    """Write narration audio to `out_path`. Returns (provider, raw word timestamps or None)."""
    errors = []
    if registry.configured("fal"):
        try:
            await log(f"Generating narration with fal.ai ElevenLabs (voice: {voice})...")
            url, timestamps = await fal.text_to_speech(text, voice, log)
            await download_file(url, out_path)
            return "fal", timestamps
        except PipelineInterrupt:
            raise
        except Exception as e:
            errors.append(f"fal.ai: {e}")
            await log(f"fal.ai TTS FAILED: {e}")
    if registry.configured("kie"):
        try:
            await log(f"Generating narration with Kie.ai ElevenLabs (voice: {voice})...")
            url = await kie.text_to_speech(text, voice, log)
            await download_file(url, out_path)
            return "kie", None
        except PipelineInterrupt:
            raise
        except Exception as e:
            errors.append(f"Kie.ai: {e}")
            await log(f"Kie.ai TTS FAILED: {e}")
    if not errors:
        raise TTSError("No TTS provider configured — set FAL_KEY or KIE_API_KEY")
    raise TTSError("All TTS providers failed: " + " | ".join(errors))
