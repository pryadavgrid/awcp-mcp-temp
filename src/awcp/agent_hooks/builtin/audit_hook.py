"""AuditHook — a persistent OBSERVER hook.

Appends one JSON line per lifecycle event to an append-only file (JSONL). Unlike
the radar's in-memory recent-events ring buffer, this survives restarts, so it is
the durable "who did what, when" trail you'd hand an auditor.

Reference example for a *stateful, persistent* observer: it owns a file handle,
writes are guarded so a disk error can never break an agent, and it subscribes to
the governance-relevant points (not the noisy per-token events).
"""

from __future__ import annotations

import json
import logging
import threading
import time

from awcp.agent_hooks.base import Hook
from awcp.agent_hooks.types import HookCategory, HookContext, HookType

log = logging.getLogger("awcp.agent_hooks.audit")


class AuditHook(Hook):
    name = "audit"
    category = HookCategory.OBSERVER
    subscriptions = (
        HookType.AGENT_REGISTERED,
        HookType.AGENT_DEREGISTERED,
        HookType.TASK_STARTED,
        HookType.TASK_COMPLETED,
        HookType.TASK_FAILED,
        HookType.GATE_EVALUATED,
        HookType.ACTION_BLOCKED,
        HookType.APPROVAL_REQUIRED,
        HookType.AUTONOMY_DEGRADED,
        HookType.BUDGET_EXHAUSTED,
    )
    priority = 20

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()

    def handle(self, ctx: HookContext) -> None:
        record = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ctx.ts)),
            "event": ctx.hook_type.value,
            "agent_id": ctx.agent_id,
            "task_id": ctx.task_id,
            "data": ctx.data,
        }
        try:
            line = json.dumps(record, default=str)
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 — auditing must never break an agent
            log.warning("audit.write_failed path=%s error=%r", self.path, exc)
