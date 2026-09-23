"""Optional NextCloud upload of final videos with a public share link.

Enabled only when NEXTCLOUD_URL (WebDAV base), NEXTCLOUD_USERNAME and
NEXTCLOUD_PASSWORD are set. When disabled, final videos stay on local disk and
are served by the authenticated /api/pipelines/{id}/final-video endpoint.
"""
import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

import httpx

from .. import config

logger = logging.getLogger(__name__)
_disabled_logged = False


def enabled() -> bool:
    return bool(config.NEXTCLOUD_URL and config.NEXTCLOUD_USERNAME and config.NEXTCLOUD_PASSWORD)


def log_status_once():
    global _disabled_logged
    if not enabled() and not _disabled_logged:
        _disabled_logged = True
        logger.info("NextCloud upload disabled (set NEXTCLOUD_URL, NEXTCLOUD_USERNAME and "
                    "NEXTCLOUD_PASSWORD to enable). Final videos are kept locally.")


def _base_url() -> str:
    """Derive the NextCloud base URL from the WebDAV URL."""
    if "/remote.php/" in config.NEXTCLOUD_URL:
        return config.NEXTCLOUD_URL.split("/remote.php/")[0]
    return config.NEXTCLOUD_URL.rstrip("/")


def slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[&/\\]+", "_and_", s)
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


async def _ensure_folder(client: httpx.AsyncClient, auth: tuple, folder_path: str):
    """MKCOL every level; 'already exists' responses are expected and ignored."""
    current = ""
    for part in [p for p in folder_path.strip("/").split("/") if p]:
        current = f"{current}/{part}"
        try:
            await client.request("MKCOL", f"{config.NEXTCLOUD_URL}{current}", auth=auth)
        except httpx.HTTPError:
            pass


async def _create_share(remote_path: str) -> str:
    base = _base_url()
    auth = (config.NEXTCLOUD_USERNAME, config.NEXTCLOUD_PASSWORD)
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
    data = {"path": remote_path, "shareType": 3, "permissions": 1}
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares",
                            auth=auth, headers=headers, data=data)
        resp.raise_for_status()
        token = resp.json()["ocs"]["data"]["token"]
    return f"{base}/index.php/s/{token}/download"


async def upload_file(file_path: str, label: str, subfolder: str) -> Tuple[str, str]:
    """Upload into <root>/<subfolder>/<year>/<Month>/. Returns (webdav_url, public_share_url)."""
    now = datetime.now(timezone.utc)
    folder_path = f"{config.NEXTCLOUD_ROOT}/{subfolder}/{now.year}/{now.strftime('%B')}"
    filename = f"{slugify(label) or 'pipeline'}_{now.strftime('%Y-%m-%d_%H-%M-%S')}_{random.randint(1000, 9999)}.mp4"
    remote_path = f"{folder_path}/{filename}"
    webdav_url = f"{config.NEXTCLOUD_URL}{remote_path}"
    auth = (config.NEXTCLOUD_USERNAME, config.NEXTCLOUD_PASSWORD)
    file_data = await asyncio.to_thread(Path(file_path).read_bytes)
    async with httpx.AsyncClient(timeout=600) as c:
        await _ensure_folder(c, auth, folder_path)
        resp = await c.put(webdav_url, auth=auth, content=file_data)
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"NextCloud upload failed: {resp.status_code} {resp.text[:200]}")
    share_url = await _create_share(remote_path)
    return webdav_url, share_url
