"""Request bodies. Unknown fields are ignored, which keeps older clients working
(e.g. `num_images`, `animate_scenes`, `animation_engine`, `watermark_logo_path`,
and the pre-video-model-picker `video_engine` / `shot_duration` are accepted
and ignored)."""
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, field_validator, model_validator

from . import config
from .providers import registry


def map_legacy_service(value):
    # Older clients send pre-rename provider names; map them instead of rejecting (422).
    if isinstance(value, str):
        return registry.normalize_service(value, default=value)
    return value


AspectRatio = Literal["16:9", "9:16", "1:1"]
SceneImageModel = Annotated[str, StringConstraints(max_length=200),
                           BeforeValidator(registry.normalize_scene_image_model)]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=config.MAX_PROMPT_CHARS)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


def _strict_image_model(value):
    # Current ids and bare/legacy provider names are accepted; anything else is a 422.
    if value is None:
        return None
    model = registry.normalize_image_model(value) if isinstance(value, str) else None
    if not model:
        raise ValueError(f"unknown image model '{value}' — use an id from GET /api/images/models")
    return model


# "snapgen/<model>", "kie/nano-banana-2" or a fal endpoint id.
ImageModel = Annotated[str, StringConstraints(max_length=200), BeforeValidator(_strict_image_model)]


class LoginRequest(BaseModel):
    password: str = Field(max_length=1000)


def map_legacy_video_system(value):
    # Older clients send the pre-Veo-extend name; map it instead of rejecting (422).
    if isinstance(value, str):
        return registry.normalize_video_system(value) or value
    return value


VideoSystem = Annotated[Literal["veo31_frame", "veo_extend"], BeforeValidator(map_legacy_video_system)]
VideoModel = Literal[tuple(registry.VIDEO_MODELS)]


class PipelineCreate(BaseModel):
    pipeline_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    image_prompts: List[Prompt] = Field(min_length=1, max_length=config.MAX_PROMPTS)
    video_prompts: List[Prompt] = Field(min_length=1, max_length=config.MAX_PROMPTS)
    aspect_ratio: AspectRatio = "16:9"
    # None = the default model of the first configured provider in ladder order.
    # Older clients send a provider name in `first_image_model` (+ `subsequent_images_model`,
    # now ignored) instead; it becomes that provider's default model.
    image_model: Optional[ImageModel] = None
    video_model: VideoModel = registry.DEFAULT_VIDEO_MODEL
    # None = the model's default. Vela has no resolution setting.
    video_resolution: Optional[Literal["720p", "1080p"]] = None
    video_duration: Optional[int] = None
    video_system: Optional[VideoSystem] = None
    # Stored for reference only; prompt packs are pasted, so no LLM runs.
    ai_model: Optional[ShortText] = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_image_fields(cls, data):
        if isinstance(data, dict) and data.get("image_model") is None and data.get("first_image_model"):
            data = {**data, "image_model": data["first_image_model"]}
        return data

    @model_validator(mode="after")
    def _valid_video_combo(self):
        spec = registry.VIDEO_MODELS[self.video_model]
        if self.video_resolution is None:
            self.video_resolution = spec["default_resolution"]
        if self.video_duration is None:
            self.video_duration = spec["default_duration"]
        problem = registry.video_model_problem(self.video_model, self.aspect_ratio, self.video_resolution,
                                               self.video_duration, extend=len(self.image_prompts) == 1)
        if problem:
            raise ValueError(problem)
        return self


class ScriptPipelineCreate(BaseModel):
    pipeline_name: Optional[ShortText] = None
    script_text: Annotated[str, StringConstraints(min_length=1, max_length=config.MAX_SCRIPT_CHARS)]
    tts_voice_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    tts_model: ShortText = "eleven_multilingual_v2"
    aspect_ratio: AspectRatio = "16:9"
    ai_model: Optional[ShortText] = None
    # An image model id (see ImageModel); unknown/legacy values map to the default fal model.
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
    # Optional model for just this item (image_model for an image, video_model for a clip).
    image_model: Optional[ImageModel] = None
    video_model: Optional[VideoModel] = None


class RetryRequest(BaseModel):
    """Optional new model selections for a retry / resume. Completed items are kept;
    the remaining items use the new selection. Pack runs take image_model and the
    video_* fields; Script Studio runs take image_model, ai_model and tts_voice_id."""
    image_model: Optional[ImageModel] = None
    video_model: Optional[VideoModel] = None
    video_resolution: Optional[Literal["720p", "1080p"]] = None
    video_duration: Optional[int] = None
    ai_model: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = None
    tts_voice_id: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = None

    def pack_fields(self) -> dict:
        return {k: v for k, v in self.model_dump(include={"video_model", "video_resolution", "video_duration"}).items()
                if v is not None}

    def script_fields(self) -> dict:
        return {k: v for k, v in self.model_dump(include={"ai_model", "tts_voice_id"}).items() if v is not None}
