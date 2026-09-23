"""Environment configuration and shared constants.

Every setting is read once at import time. Values already present in the
process environment always win over values from a `.env` file.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# The project-root .env is the canonical location; backend/.env is a legacy
# fallback. override=False keeps real environment variables authoritative.
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(BACKEND_DIR / ".env", override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ---------- Auth ----------
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_SECRET = _env("SESSION_SECRET")
SESSION_TTL_SECONDS = 7 * 24 * 3600
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 300

# Comma-separated list of allowed cross-origin frontends. Empty = same-origin only.
CORS_ORIGINS = [o.strip() for o in _env("CORS_ORIGINS").split(",") if o.strip()]

# ---------- Storage ----------
# Relative paths (e.g. DATA_DIR=./data in a local .env) are anchored to the project
# root so the location doesn't depend on which folder uvicorn was started from.
DATA_DIR = Path(_env("DATA_DIR", "/app/data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = (PROJECT_ROOT / DATA_DIR).resolve()
SCRIPT_STUDIO_DIR = Path(_env("SCRIPT_STUDIO_DIR") or (DATA_DIR / "script_studio"))
UPLOADS_DIR = DATA_DIR / "uploads"
PACK_WORK_DIR = DATA_DIR / "pack_work"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# ---------- Providers ----------
APP_TITLE = "AI Video Production Hub"


def resolve_snapgen_key(environ=os.environ):
    """SNAPGEN_API_KEY, falling back to the pre-rebrand GEMINIGEN_API_KEY.

    Returns (key, used_legacy_name). SnapGen was formerly called GeminiGen; the
    legacy variable keeps existing deployments working until they rename it.
    """
    key = (environ.get("SNAPGEN_API_KEY") or "").strip()
    if key:
        return key, False
    legacy = (environ.get("GEMINIGEN_API_KEY") or "").strip()
    return legacy, bool(legacy)


SNAPGEN_API_KEY, _SNAPGEN_KEY_IS_LEGACY = resolve_snapgen_key()
if _SNAPGEN_KEY_IS_LEGACY:
    logging.getLogger(__name__).warning(
        "GEMINIGEN_API_KEY is deprecated — rename it to SNAPGEN_API_KEY (the value is used for now).")
SNAPGEN_BASE = "https://api.snapgen.ai/uapi/v1"
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
FAL_KEY = _env("FAL_KEY")
FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_API_BASE = "https://api.fal.ai/v1"
KIE_API_KEY = _env("KIE_API_KEY")
KIE_BASE = "https://api.kie.ai"
DEFAULT_LLM_MODEL = _env("DEFAULT_LLM_MODEL") or "anthropic/claude-sonnet-5"  # an OpenRouter model id
DEFAULT_SCENE_IMAGE_MODEL = "fal-ai/nano-banana-2"
CATALOG_CACHE_SECONDS = 3600

# ---------- Optional integrations ----------
NEXTCLOUD_URL = _env("NEXTCLOUD_URL")
NEXTCLOUD_USERNAME = _env("NEXTCLOUD_USERNAME")
NEXTCLOUD_PASSWORD = os.environ.get("NEXTCLOUD_PASSWORD", "")
NEXTCLOUD_ROOT = _env("NEXTCLOUD_ROOT", "/AI_Video_Production_Hub")
NOCODB_URL = _env("NOCODB_URL")
NOCODB_API_TOKEN = _env("NOCODB_API_TOKEN")
NOCODB_BASE_ID = _env("NOCODB_BASE_ID")

# ---------- Pipeline timing ----------
IMAGE_POLL_TIMEOUT = 600
SNAPGEN_IMAGE_WAIT = 900
VIDEO_POLL_TIMEOUT = 1800  # SnapGen video jobs can be slow
TTS_POLL_TIMEOUT = 600
VIDEO_MAX_RETRIES = 3

# ---------- Input limits ----------
MAX_PROMPTS = 60
MAX_PROMPT_CHARS = 8000
MAX_SCRIPT_CHARS = 50_000
LOG_TAIL = 500
