"""Provider names, the env var each one needs, and legacy value normalisation.

Older databases and clients use provider names from before the SnapGen rebrand
and the Straico removal. Every read of a stored provider/model value goes
through the `normalize_*` helpers here, so there is exactly one place that
knows about the old names.
"""
from typing import List, Optional

from .. import config

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


def is_fal_model(model_id: str) -> bool:
    return isinstance(model_id, str) and model_id.startswith("fal-ai/")


def normalize_scene_image_model(value: Optional[str]) -> str:
    """Script Studio image model: a fal endpoint id, 'snapgen' or 'kie'.

    fal ids stored by older versions keep working; anything else (including
    models that only existed on the retired catalogue) maps to the default.
    """
    if not value:
        return config.DEFAULT_SCENE_IMAGE_MODEL
    v = str(value).strip()
    if is_fal_model(v):
        return v
    service = normalize_service(v, default=None)
    if service in ("snapgen", "kie"):
        return service
    return config.DEFAULT_SCENE_IMAGE_MODEL


def scene_model_provider(model: str) -> str:
    return model if model in ("snapgen", "kie") else "fal"


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
        provider = scene_model_provider(image_model)
        if not configured(provider):
            add(ENV_VARS[provider])
    elif not any(configured(p) for p in IMAGE_SERVICES):
        add(ENV_VARS["fal"])
    return missing


def missing_env_message(missing: List[str]) -> str:
    alt = " (or KIE_API_KEY for narration)" if "FAL_KEY" in missing and not configured("kie") else ""
    return (f"Missing environment variable(s): {', '.join(missing)}{alt}. Set them in your .env or "
            "hosting provider's environment variables, then restart.")
