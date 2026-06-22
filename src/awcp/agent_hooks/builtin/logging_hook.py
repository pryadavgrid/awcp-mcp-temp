"""LoggingHook — the simplest OBSERVER hook.

Emits one structured log line for every lifecycle point. Because the radar's
logging is bridged to OpenTelemetry, these lines also land in Loki/Grafana, so
this hook doubles as a live "what are my agents doing" feed.

This is the reference example for writing an observer hook: subscribe to the
points you care about (here: all of them), read the context, do a side-effect,
return nothing.
"""

from __future__ import annotations

import logging

from awcp.agent_hooks.base import Hook
from awcp.agent_hooks.types import HookCategory, HookContext, HookType

log = logging.getLogger("awcp.agent_hooks.event")


class LoggingHook(Hook):
    name = "logging"
    category = HookCategory.OBSERVER
    # Subscribe to EVERY lifecycle point.
    subscriptions = tuple(HookType)
    priority = 10  # run early so the log reflects the raw event before guards mutate intent

    def handle(self, ctx: HookContext) -> None:
        # Keep the payload compact and safe for a log line.
        compact = {k: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v)
                   for k, v in ctx.data.items()}
        log.info(
            "hook.event type=%s agent_id=%s task_id=%s data=%s",
            ctx.hook_type.value, ctx.agent_id or "-", ctx.task_id or "-", compact,
        )
