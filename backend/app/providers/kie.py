"""Kie.ai market API: nano-banana-2 images and ElevenLabs TTS.

Docs: https://docs.kie.ai/ — POST /api/v1/jobs/createTask, poll
GET /api/v1/jobs/recordInfo?taskId=…, results in `data.resultJson`.
"""
import asyncio
import json
from typing import Awaitable, Callable, Optional

import httpx

from .. import config
from ..pipelines import runner
from .http_errors import describe

POLL_INTERVAL = 10
TTS_MODEL = "elevenlabs/text-to-speech-multilingual-v2"
TTS_MAX_CHARS = 5000  # per-request limit documented for Kie's ElevenLabs models

LogFn = Optional[Callable[[str], Awaitable[None]]]


class KieError(RuntimeError):
    pass


class KieCreditError(KieError):
    """The account is out of credits — retrying won't help."""


def _headers(json_body: bool = True) -> dict:
    h = {"Authorization": f"Bearer {config.KIE_API_KEY}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def is_credit_error(msg: str) -> bool:
    low = msg.lower()
    return "credits insufficient" in low or ("credit" in low and "insufficient" in low)


async def create_task(model: str, input_: dict) -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(f"{config.KIE_BASE}/api/v1/jobs/createTask", headers=_headers(),
                            json={"model": model, "input": input_})
    if resp.status_code == 402:
        raise KieCreditError(f"Kie.ai insufficient credits ({describe(resp)})")
    if resp.status_code >= 400:
        raise KieError(f"Kie.ai createTask {describe(resp)}")
    data = resp.json()
    code = data.get("code")
    if code == 402 or is_credit_error(str(data.get("msg", ""))):
        raise KieCreditError(f"Kie.ai: insufficient credits ({data.get('msg', 'code 402')})")
    if code != 200:
        raise KieError(f"Kie.ai createTask failed (code {code}): {data.get('msg', 'Unknown')}")
    payload = data.get("data")
    task_id = payload.get("taskId") if isinstance(payload, dict) else payload
    if not task_id:
        raise KieError(f"Kie.ai submit response had no taskId: {str(data)[:300]}")
    return task_id


async def submit_image(prompt: str, reference_url: Optional[str], aspect_ratio: str) -> str:
    return await create_task("nano-banana-2", {
        "prompt": prompt, "image_input": [reference_url] if reference_url else [],
        "aspect_ratio": aspect_ratio, "resolution": "1K", "output_format": "jpg",
    })


async def check_status(task_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{config.KIE_BASE}/api/v1/jobs/recordInfo", headers=_headers(False),
                           params={"taskId": task_id})
        resp.raise_for_status()
        return resp.json()


async def wait_for_result(task_id: str, step_label: str, max_wait: int, log: LogFn = None) -> dict:
    """Poll until the task succeeds; return the parsed resultJson."""
    elapsed = 0
    while elapsed < max_wait:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        runner.check_cancelled()
        try:
            result = await check_status(task_id)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if 400 <= code < 500 and code != 429:
                raise KieError(f"Kie.ai recordInfo {describe(e.response)}") from e
            if log:
                await log(f"[{step_label}] Kie.ai recordInfo {describe(e.response)}, will retry")
            continue
        except httpx.HTTPError as e:
            if log:
                await log(f"[{step_label}] Kie.ai network error: {e}")
            continue
        data = result.get("data") or {}
        state = data.get("state", "waiting")
        if log:
            await log(f"[{step_label}] Kie.ai polling... state={state}, elapsed={elapsed}s")
        if state == "success":
            raw = data.get("resultJson") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError as e:
                raise KieError(f"Kie.ai returned unreadable resultJson: {str(raw)[:200]}") from e
            return parsed or {}
        if state == "fail":
            raise KieError(f"Kie.ai generation failed: {data.get('failMsg', 'Unknown')}")
    raise TimeoutError(f"Kie.ai timeout after {max_wait}s for {step_label}")


def first_result_url(parsed: dict, what: str) -> str:
    urls = parsed.get("resultUrls") or []
    if urls:
        return urls[0]
    raise KieError(f"Kie.ai returned success but no {what} URL")


async def wait_for(task_id: str, pipeline_id: str, step_label: str, max_wait: int = config.IMAGE_POLL_TIMEOUT) -> str:
    """Image task → first result URL."""
    from ..pipelines.common import add_log

    async def log(msg):
        await add_log(pipeline_id, msg)

    return first_result_url(await wait_for_result(task_id, step_label, max_wait, log), "image")


async def text_to_speech(text: str, voice: str, log: LogFn = None) -> str:
    """ElevenLabs Multilingual v2 via Kie. Returns the audio URL."""
    if len(text) > TTS_MAX_CHARS:
        raise KieError(f"Kie.ai TTS accepts at most {TTS_MAX_CHARS} characters per request "
                       f"(script has {len(text)}).")
    task_id = await create_task(TTS_MODEL, {
        "text": text, "voice": voice, "stability": 0.5, "similarity_boost": 0.75, "style": 0, "speed": 1,
    })
    if log:
        await log(f"Kie.ai TTS submitted (taskId: {task_id})")
    parsed = await wait_for_result(task_id, "TTS", config.TTS_POLL_TIMEOUT, log)
    return first_result_url(parsed, "audio")
