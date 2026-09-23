"""User-uploaded logo images, stored under DATA_DIR/uploads and referenced by opaque id."""
import io
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

from . import config

UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FORMAT_EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_CHUNK = 64 * 1024


def _validate_image(data: bytes) -> str:
    """Open the bytes with Pillow; return the file extension for its real format."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"File is not a valid image: {e}")
    if fmt not in _FORMAT_EXT:
        raise HTTPException(status_code=400, detail="Only PNG, JPG/JPEG and WEBP images are accepted")
    return _FORMAT_EXT[fmt]


async def save_logo(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only PNG, JPG/JPEG and WEBP images are accepted")
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Image is larger than 10 MB")
    if not buf:
        raise HTTPException(status_code=400, detail="Empty upload")
    ext = _validate_image(bytes(buf))
    upload_id = uuid.uuid4().hex
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (config.UPLOADS_DIR / f"{upload_id}{ext}").write_bytes(bytes(buf))
    return upload_id


def resolve_id(upload_id: str) -> Optional[Path]:
    if not upload_id or not UPLOAD_ID_RE.match(upload_id):
        return None
    for ext in set(_FORMAT_EXT.values()):
        candidate = config.UPLOADS_DIR / f"{upload_id}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _legacy_logo_dirs():
    return [config.UPLOADS_DIR, config.SCRIPT_STUDIO_DIR / "logos",
            config.PROJECT_ROOT / ".tmp" / "script_studio" / "logos"]


def resolve_stored_reference(value: str) -> Optional[Path]:
    """Resolve a pipeline's stored logo reference.

    New pipelines store an upload id. Older rows stored an absolute path; those
    are honoured only if they point inside a directory this server writes logos to.
    """
    if UPLOAD_ID_RE.match(value or ""):
        return resolve_id(value)
    try:
        path = Path(value).resolve()
    except (OSError, ValueError):
        return None
    for base in _legacy_logo_dirs():
        try:
            path.relative_to(base.resolve())
        except ValueError:
            continue
        return path if path.is_file() else None
    return None
