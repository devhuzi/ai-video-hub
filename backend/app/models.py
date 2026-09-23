"""Request bodies. Unknown fields are ignored, which keeps older clients working
(e.g. `num_images`, `animate_scenes`, `animation_engine`, `watermark_logo_path`
are accepted and ignored)."""
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, field_validator

from . import config
from .providers import registry


def map_legacy_service(value):
    # Older clients send pre-rename provider names; map them instead of rejecting (422).
    if isinstance(value, str):
        return registry.normalize_service(value, default=value)
    return value


AspectRatio = Literal["16:9", "9:16", "1:1"]
ImageService = Annotated[Literal["snapgen", "kie", "fal"], BeforeValidator(map_legacy_service)]
SceneImageModel = Annotated[str, StringConstraints(max_length=200),
                           BeforeValidator(registry.normalize_scene_image_model)]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=config.MAX_PROMPT_CHARS)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class LoginRequest(BaseModel):
    password: str = Field(max_length=1000)


class PipelineCreate(BaseModel):
    pipeline_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    image_prompts: List[Prompt] = Field(min_length=1, max_length=config.MAX_PROMPTS)
    video_prompts: List[Prompt] = Field(min_length=1, max_length=config.MAX_PROMPTS)
    aspect_ratio: AspectRatio = "16:9"
    first_image_model: ImageService = "snapgen"
    subsequent_images_model: ImageService = "snapgen"
    shot_duration: int = Field(default=6, ge=1, le=20)
    video_engine: Literal["grok", "veo"] = "grok"
    video_system: Optional[Literal["veo31_frame", "grok_sequential_extend"]] = None
    # Stored for reference only; prompt packs are pasted, so no LLM runs.
    ai_model: Optional[ShortText] = None


class ScriptPipelineCreate(BaseModel):
    pipeline_name: Optional[ShortText] = None
    script_text: Annotated[str, StringConstraints(min_length=1, max_length=config.MAX_SCRIPT_CHARS)]
    tts_voice_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    tts_model: ShortText = "eleven_multilingual_v2"
    aspect_ratio: AspectRatio = "16:9"
    ai_model: Optional[ShortText] = None
    # A fal endpoint id, "snapgen" or "kie"; unknown/legacy values map to the default fal model.
    image_gen_model: SceneImageModel = config.DEFAULT_SCENE_IMAGE_MODEL
    watermark_logo_id: Optional[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]] = None
    global_style: Optional[Annotated[str, StringConstraints(max_length=2000)]] = None

    @field_validator("script_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("script_text must not be blank")
        return v


class RegenerateRequest(BaseModel):
    kind: Literal["image", "video"]
    index: int = Field(ge=0)
