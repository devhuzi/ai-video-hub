"""Run registry: one asyncio task per pipeline run, pause/cancel control, global lock.

Each run gets its own `RunControl`. Registry entries are only ever removed by
the run that created them, so a stale run finishing late can't clobber the
control state of a newer retry of the same pipeline.
"""
import asyncio
import contextvars
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

import database as db

logger = logging.getLogger(__name__)

# Only one pipeline executes at a time (provider rate limits, CPU for FFmpeg).
pipeline_lock = asyncio.Lock()


class PipelineInterrupt(Exception):
    """Base for user-requested stops. Retry/fallback handlers must re-raise these."""


class PipelineCancelled(PipelineInterrupt):
    pass


class PipelinePaused(PipelineInterrupt):
    pass


@dataclass(eq=False)
class RunControl:
    pipeline_id: str
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    pause: asyncio.Event = field(default_factory=asyncio.Event)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    procs: Set[asyncio.subprocess.Process] = field(default_factory=set)

    def check_cancelled(self):
        if self.cancel.is_set():
            raise PipelineCancelled()

    def checkpoint(self):
        """Call between paid calls: stop here if the user asked to cancel or pause."""
        self.check_cancelled()
        if self.pause.is_set():
            raise PipelinePaused()

    def kill_processes(self):
        for proc in list(self.procs):
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass


_runs: Dict[str, RunControl] = {}
_current: contextvars.ContextVar[Optional[RunControl]] = contextvars.ContextVar("current_run", default=None)


def current() -> Optional[RunControl]:
    return _current.get()


def checkpoint():
    ctl = _current.get()
    if ctl:
        ctl.checkpoint()


def check_cancelled():
    ctl = _current.get()
    if ctl:
        ctl.check_cancelled()


def current_procs() -> Optional[Set[asyncio.subprocess.Process]]:
    ctl = _current.get()
    return ctl.procs if ctl else None


def is_active(pipeline_id: str) -> bool:
    ctl = _runs.get(pipeline_id)
    return ctl is not None and ctl.task is not None and not ctl.task.done()


def get_control(pipeline_id: str) -> Optional[RunControl]:
    return _runs.get(pipeline_id)


def start_run(pipeline_id: str) -> bool:
    """Start a background run. Refuses (returns False) if one is already active."""
    if is_active(pipeline_id):
        return False
    ctl = RunControl(pipeline_id)
    _runs[pipeline_id] = ctl
    ctl.task = asyncio.create_task(_run(ctl), name=f"pipeline-{pipeline_id}")
    return True


def request_pause(pipeline_id: str) -> bool:
    ctl = _runs.get(pipeline_id)
    if ctl is None:
        return False
    ctl.pause.set()
    return True


def request_cancel(pipeline_id: str) -> bool:
    """Signal cancel, kill any FFmpeg child and hard-cancel the task."""
    ctl = _runs.get(pipeline_id)
    if ctl is None:
        return False
    ctl.cancel.set()
    ctl.kill_processes()
    if ctl.task and not ctl.task.done():
        ctl.task.cancel()
    return True


async def _run(ctl: RunControl):
    from . import common, pack, script  # local import: executors depend on this module

    pid = ctl.pipeline_id
    _current.set(ctl)
    try:
        await common.add_log(pid, "Pipeline queued. Waiting for the current pipeline to finish...")
        async with pipeline_lock:
            started = not ctl.cancel.is_set() and await db.transition_status(
                pid, ("queued",), {"status": "running", "error_message": None})
            p = await db.find_pipeline(pid)
            if not started or not p:
                if p:
                    await common.add_log(pid, f"Run skipped (status is '{p.get('status')}').")
                return
            common.sync_status(pid, "running")
            await common.add_log(pid, "Lock acquired. Starting pipeline execution.")
            if (p.get("pipeline_kind") or "paste") == "script":
                await script.execute(pid)
            else:
                await pack.execute(pid)
    except asyncio.CancelledError:
        # The cancel endpoint already recorded status='cancelled'.
        ctl.kill_processes()
        raise
    except Exception:
        logger.exception("Unhandled error in pipeline run %s", pid)
    finally:
        try:
            await db.reset_inflight_items(pid)
        except Exception:
            logger.exception("Could not reset in-flight items for %s", pid)
        if _runs.get(pid) is ctl:
            del _runs[pid]
