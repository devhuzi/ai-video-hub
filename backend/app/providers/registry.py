"""Provider names, the env var each one needs, the SnapGen video model catalogue,
and legacy value normalisation.

Older databases and clients use provider names from before the SnapGen rebrand
and the Straico removal, and video settings from before the video model
picker. Every read of a stored provider/model value goes through the
`normalize_*` / `*_settings` helpers here, so there is exactly one place that
knows about the old names.
"""
from typing import Dict, List, Optional

from .. import config

# Also the image fallback ladder order: SnapGen → Kie.ai → fal.ai.
IMAGE_SERVICES = ("snapgen", "kie", "fal")
DEFAULT_IMAGE_SERVICE = "snapgen"

ENV_VARS = {
    "snapgen": "SNAPGEN_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "fal": "FAL_KEY",
    "kie": "KIE_API_KEY",
}

LABELS = {"snapgen": "SnapGen", "kie": "Kie.ai", "fal": "fal.ai", "openrouter": "OpenRouter"}

# Legacy compatibility: stored/requested values from older versions.
# "geminigen" is SnapGen's former name. "straico" proxied fal's nano-banana
# models, so fal is its direct replacement. "kie.ai" was the old service label.
_LEGACY_SERVICE_NAMES = {"geminigen": "snapgen", "straico": "fal", "kie.ai": "kie"}


def configured(provider: str) -> bool:
    return bool({
        "snapgen": config.SNAPGEN_API_KEY,
        "openrouter": config.OPENROUTER_API_KEY,
        "fal": config.FAL_KEY,
        "kie": config.KIE_API_KEY,
    }.get(provider))


def normalize_service(value: Optional[str], default: Optional[str] = DEFAULT_IMAGE_SERVICE) -> Optional[str]:
    """Map a stored or requested image service name onto snapgen | kie | fal."""
    if not value:
        return default
    v = str(value).strip().lower()
    v = _LEGACY_SERVICE_NAMES.get(v, v)
    return v if v in IMAGE_SERVICES else default


def default_image_provider() -> str:
    """First configured provider in ladder order (SnapGen when none is configured)."""
    return next((p for p in IMAGE_SERVICES if configured(p)), DEFAULT_IMAGE_SERVICE)


def image_ladder(start: Optional[str]) -> List[str]:
    """Providers to try: `start`, then the rest in ladder order, wrapping round
    to the earlier ones (kie → kie, fal, snapgen)."""
    first = normalize_service(start)
    i = IMAGE_SERVICES.index(first)
    return list(IMAGE_SERVICES[i:] + IMAGE_SERVICES[:i])


# =============================================================
# Image models
# =============================================================
# Ids: "snapgen/<model>", "kie/nano-banana-2", or a fal endpoint id ("fal-ai/…").
# SnapGen credits are as listed in SnapGen's app (they may change); 0 = free
# within SnapGen's daily limit. `endpoint`: which SnapGen API generates it.
CREDITS_NOTE = "as listed by SnapGen — may change"
FREE_DAILY = "Free (daily limit)"
SNAPGEN_IMAGE_MODELS: Dict[str, dict] = {
    "nano-banana-2": {"label": "Nano Banana 2", "credits": 0, "credits_label": FREE_DAILY,
                      "endpoint": "generate_image"},
    "nano-banana-pro": {"label": "Nano Banana Pro", "credits": 0, "credits_label": FREE_DAILY,
                        "endpoint": "generate_image"},
    "nano-banana-2-lite": {"label": "Nano Banana 2 Lite", "credits": 0, "credits_label": FREE_DAILY,
                           "endpoint": "generate_image"},
    "grok-image": {"label": "Grok Image", "credits": 4, "credits_label": "4 credits (speed mode)",
                   "endpoint": "imagen/grok"},
    "gpt-image-2": {"label": "GPT Image 2", "credits": 3, "credits_label": "3 credits (low, 1K)",
                    "endpoint": "imagen/gpt-image-2"},
}
KIE_IMAGE_MODELS: Dict[str, dict] = {"nano-banana-2": {"label": "Nano Banana 2"}}
# Each provider's model when it is reached by falling back down the ladder.
DEFAULT_IMAGE_MODELS = {"snapgen": "snapgen/nano-banana-2", "kie": "kie/nano-banana-2",
                        "fal": config.DEFAULT_SCENE_IMAGE_MODEL}


def image_model_provider(model: str) -> str:
    """snapgen | kie | fal for a (normalised) image model id."""
    prefix, sep, _ = str(model).partition("/")
    return prefix if sep and prefix in ("snapgen", "kie") else "fal"


def image_model_name(model: str) -> str:
    """The provider's own model name: 'snapgen/grok-image' → 'grok-image'."""
    return model.split("/", 1)[1] if image_model_provider(model) in ("snapgen", "kie") else model


def is_known_image_model(model: Optional[str]) -> bool:
    if not isinstance(model, str):
        return False
    provider = image_model_provider(model)
    if provider == "snapgen":
        return image_model_name(model) in SNAPGEN_IMAGE_MODELS
    if provider == "kie":
        return image_model_name(model) in KIE_IMAGE_MODELS
    return is_fal_model(model)


def normalize_image_model(value: Optional[str], default: Optional[str] = None) -> Optional[str]:
    """Map a stored or requested image model onto a current model id.

    Bare provider names — how older versions stored the choice ("snapgen",
    "kie", legacy "geminigen" / "straico" / "kie.ai", "fal") — become that
    provider's default model. Unknown values map to `default`.
    """
    if not value:
        return default
    v = str(value).strip()
    if is_known_image_model(v):
        return v
    service = normalize_service(v, default=None)
    if service:
        return DEFAULT_IMAGE_MODELS[service]
    return default


def default_image_model() -> str:
    """Default model of the first configured provider in ladder order."""
    return DEFAULT_IMAGE_MODELS[default_image_provider()]


def image_model_ladder(model: str) -> List[tuple]:
    """(provider, model) pairs to try: the chosen model, then every other
    provider in ladder order (wrapping round) with its default model."""
    first = image_model_provider(model)
    return [(first, model)] + [(p, DEFAULT_IMAGE_MODELS[p]) for p in image_ladder(first)[1:]]


def pack_image_model(p: dict, override: Optional[str] = None) -> str:
    """Image model of a stored pack row (or of one item, with its override).
    Rows from before the image model setting stored a provider name in
    `first_image_model`; it becomes that provider's default model."""
    return (normalize_image_model(override)
            or normalize_image_model(p.get("image_model"))
            or normalize_image_model(p.get("first_image_model"))
            or DEFAULT_IMAGE_MODELS[DEFAULT_IMAGE_SERVICE])


# =============================================================
# SnapGen video models
# =============================================================
# From SnapGen's API docs and live app. `credits` is the per-clip credit cost
# as listed in SnapGen's app (CREDITS_NOTE); 0 = free within a daily limit. image_mode:
#   frame       — 1–2 images used as the exact first / last frame
#   ingredient  — up to 3 images used as references, not exact frames
#   start_image — one start image only (Vela)
_VEO = {
    "aspects": ["16:9", "9:16"], "resolutions": ["720p", "1080p"], "durations": [4, 6, 8],
    "default_resolution": "720p", "default_duration": 8, "image_mode": "frame",
    "supports_frame_packs": True, "supports_extend": True, "experimental": False,
}
VIDEO_MODELS: Dict[str, dict] = {
    "veo-3.1-fast": {**_VEO, "label": "Veo 3.1 Fast", "credits": 4, "credits_label": "4 credits"},
    "veo-3.1-lite": {**_VEO, "label": "Veo 3.1 Lite", "credits": 4, "credits_label": "4 credits"},
    "veo-3.1": {**_VEO, "label": "Veo 3.1", "credits": 100, "credits_label": "100 credits"},
    "omni-flash": {
        **_VEO, "label": "Omni Flash", "credits": 13, "credits_label": "13 credits", "durations": [4, 6, 8, 10],
        "image_mode": "ingredient", "supports_extend": False,
    },
    # Not in SnapGen's public API docs — found in their web app (provider "meta").
    "vela": {
        "label": "Vela AI (experimental)", "credits": 0, "credits_label": FREE_DAILY,
        "aspects": ["16:9", "9:16", "1:1"],
        "resolutions": [], "durations": [5], "default_resolution": None, "default_duration": 5,
        "image_mode": "start_image", "supports_frame_packs": True, "supports_extend": False,
        "experimental": True,
    },
}
DEFAULT_VIDEO_MODEL = "veo-3.1-fast"
VIDEO_SYSTEMS = ("veo31_frame", "veo_extend")
# Rows stored before the video model picker were rendered at 1080p, 8 s.
LEGACY_VIDEO_RESOLUTION = "1080p"
LEGACY_VIDEO_DURATION = 8


def video_model_problem(model: str, aspect_ratio: str, resolution: Optional[str], duration: Optional[int],
                        extend: bool = False) -> Optional[str]:
    """Why this combination is invalid on SnapGen, or None when it is valid."""
    spec = VIDEO_MODELS.get(model)
    if not spec:
        return f"Unknown video model '{model}'. Choose one of: {', '.join(VIDEO_MODELS)}"
    label = spec["label"]
    if extend and not spec["supports_extend"]:
        return f"{label} is not available for extend packs (1 image + clips) — use a Veo 3.1 model"
    if aspect_ratio not in spec["aspects"]:
        return f"{label} supports aspect ratios {', '.join(spec['aspects'])}, not {aspect_ratio}"
    if spec["resolutions"]:
        if resolution not in spec["resolutions"]:
            return f"{label} supports resolutions {', '.join(spec['resolutions'])}, not {resolution}"
    elif resolution:
        return f"{label} has no resolution setting — leave video_resolution empty"
    if duration not in spec["durations"]:
        return f"{label} supports clip durations {', '.join(map(str, spec['durations']))}s, not {duration}s"
    return None


def normalize_video_system(value: Optional[str]) -> Optional[str]:
    """Map a stored or requested video system onto veo31_frame | veo_extend.

    Every sequential-extend value from older versions (grok_sequential_extend,
    veo31_sequential_extend) is now Veo extend.
    """
    if not value:
        return None
    v = str(value).strip().lower()
    if "extend" in v:
        return "veo_extend"
    return v if v in VIDEO_SYSTEMS else None


def video_settings(p: dict) -> dict:
    """Effective video settings of a stored pack row (used by executors and views).

    Rows from before the video model picker (no `video_model`; a Grok engine or
    a legacy extend system) render with Veo 3.1 Fast at 1080p / 8 s, as the old
    code did. Extend packs always get a model that supports extend.
    """
    system = normalize_video_system(p.get("video_system"))
    stored = p.get("video_model")
    model = stored if stored in VIDEO_MODELS else DEFAULT_VIDEO_MODEL
    if system == "veo_extend" and not VIDEO_MODELS[model]["supports_extend"]:
        model = DEFAULT_VIDEO_MODEL
    spec = VIDEO_MODELS[model]
    legacy = stored not in VIDEO_MODELS

    resolution = p.get("video_resolution")
    if resolution not in spec["resolutions"]:
        resolution = LEGACY_VIDEO_RESOLUTION if legacy else spec["default_resolution"]
    try:
        duration = int(p.get("video_duration"))
    except (TypeError, ValueError):
        duration = None
    if duration not in spec["durations"]:
        duration = LEGACY_VIDEO_DURATION if legacy else spec["default_duration"]
    return {"video_system": system, "video_model": model, "video_resolution": resolution,
            "video_duration": duration}


def is_fal_model(model_id: str) -> bool:
    return isinstance(model_id, str) and model_id.startswith("fal-ai/")


def normalize_scene_image_model(value: Optional[str]) -> str:
    """Script Studio image model id (see normalize_image_model).

    fal ids stored by older versions keep working, and "snapgen" / "kie" become
    those providers' default models; anything else (including models that only
    existed on the retired catalogue) maps to the default.
    """
    return normalize_image_model(value, default=config.DEFAULT_SCENE_IMAGE_MODEL)


def missing_env(providers: List[str]) -> List[str]:
    return [ENV_VARS[p] for p in providers if not configured(p)]


def pack_missing_env() -> List[str]:
    """Prompt packs render every clip on SnapGen; images fall back across any configured provider."""
    return missing_env(["snapgen"])


def script_missing_env(image_model: Optional[str] = None) -> List[str]:
    """Script Studio needs the LLM, a TTS provider (fal or Kie) and an image provider.

    With `image_model` the check is for that model's provider; without it, any
    configured image provider will do (the UI then preselects a usable model).
    """
    missing = missing_env(["openrouter"])

    def add(name: str):
        if name not in missing:
            missing.append(name)

    if not (configured("fal") or configured("kie")):
        add(ENV_VARS["fal"])
    if image_model:
        provider = image_model_provider(image_model)
        if not configured(provider):
            add(ENV_VARS[provider])
    elif not any(configured(p) for p in IMAGE_SERVICES):
        add(ENV_VARS["fal"])
    return missing


def missing_env_message(missing: List[str]) -> str:
    alt = " (or KIE_API_KEY for narration)" if "FAL_KEY" in missing and not configured("kie") else ""
    return (f"Missing environment variable(s): {', '.join(missing)}{alt}. Set them in your .env or "
            "hosting provider's environment variables, then restart.")
