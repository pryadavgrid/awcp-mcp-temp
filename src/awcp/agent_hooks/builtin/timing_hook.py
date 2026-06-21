"""TimingHook — a stateful OBSERVER hook that measures task duration.

Records ``task_started`` timestamps and, on ``task_completed`` / ``task_failed``,
logs how long the task took and how many steps it ran. Demonstrates a hook that
correlates *two* lifecycle points using its own state keyed by ``task_id``.

Kept deliberately small: it stores only open tasks and forgets them on
completion, so memory stays bounded even for a long-running fleet.
"""

from __future__ import annotations

import logging
import threading
import time

from awcp.agent_hooks.base import Hook
from awcp.agent_hooks.types import HookCategory, HookContext, HookType

log = logging.getLogger("awcp.agent_hooks.timing")


class TimingHook(Hook):
    name = "timing"
    category = HookCategory.OBSERVER
    subscriptions = (
        HookType.TASK_STARTED,
        HookType.STEP,
        HookType.TASK_COMPLETED,
        HookType.TASK_FAILED,
    )
    priority = 40

    def __init__(self) -> None:
        # task_id -> {"t0": float, "steps": int, "agent_id": str}
        self._open: dict[str, dict] = {}
        self._lock = threading.Lock()

    def handle(self, ctx: HookContext) -> None:
        tid = ctx.task_id or ""
        if not tid:
            return
        if ctx.hook_type == HookType.TASK_STARTED:
            with self._lock:
                self._open[tid] = {"t0": time.monotonic(), "steps": 0, "agent_id": ctx.agent_id}
        elif ctx.hook_type == HookType.STEP:
            with self._lock:
                rec = self._open.get(tid)
                if rec:
                    rec["steps"] += 1
        else:  # TASK_COMPLETED / TASK_FAILED
            with self._lock:
                rec = self._open.pop(tid, None)
            if rec:
                dur_ms = (time.monotonic() - rec["t0"]) * 1000
                log.info(
                    "timing.task agent_id=%s task_id=%s status=%s steps=%d duration_ms=%.0f",
                    rec["agent_id"], tid,
                    "done" if ctx.hook_type == HookType.TASK_COMPLETED else "failed",
                    rec["steps"], dur_ms,
                )
