"""MetricsHook — an OBSERVER hook that feeds OpenTelemetry metrics.

Turns the lifecycle stream into counters that show up in Prometheus/Grafana
alongside the rest of AWCP's telemetry:

  * ``awcp.hooks.events.total{event,agent}``   — every lifecycle event
  * ``awcp.hooks.tasks.total{status,agent}``   — task completions by outcome
  * ``awcp.hooks.blocked.total{agent}``        — actions denied by governance
  * ``awcp.hooks.degraded.total{agent}``       — autonomy step-downs
  * ``awcp.hooks.tokens{agent,model}``         — metered token usage (histogram)

Reference example for an *integration* hook: it reuses the existing observability
provider via ``get_meter`` and degrades to a no-op if the SDK isn't present, so it
never adds a hard dependency.
"""

from __future__ import annotations

import logging

from awcp.agent_hooks.base import Hook
from awcp.agent_hooks.types import HookCategory, HookContext, HookType

log = logging.getLogger("awcp.agent_hooks.metrics")


class MetricsHook(Hook):
    name = "metrics"
    category = HookCategory.OBSERVER
    subscriptions = tuple(HookType)
    priority = 30

    def __init__(self) -> None:
        self._ok = False
        try:
            from awcp.observability.setup import get_meter
            meter = get_meter("awcp.agent_hooks")
            self._events = meter.create_counter(
                "awcp.hooks.events.total", unit="1",
                description="Agent lifecycle events seen by the hook system")
            self._tasks = meter.create_counter(
                "awcp.hooks.tasks.total", unit="1",
                description="Agent tasks by terminal status")
            self._blocked = meter.create_counter(
                "awcp.hooks.blocked.total", unit="1",
                description="Actions denied by governance")
            self._degraded = meter.create_counter(
                "awcp.hooks.degraded.total", unit="1",
                description="Autonomy degradation events")
            self._tokens = meter.create_histogram(
                "awcp.hooks.tokens", unit="1",
                description="Metered token usage per call")
            self._ok = True
        except Exception as exc:  # noqa: BLE001 — metrics are best-effort
            log.debug("metrics.disabled error=%r", exc)

    def handle(self, ctx: HookContext) -> None:
        if not self._ok:
            return
        agent = ctx.agent_id or "unknown"
        try:
            self._events.add(1, {"event": ctx.hook_type.value, "agent": agent})
            if ctx.hook_type == HookType.TASK_COMPLETED:
                self._tasks.add(1, {"status": "done", "agent": agent})
            elif ctx.hook_type == HookType.TASK_FAILED:
                self._tasks.add(1, {"status": ctx.get("status", "failed"), "agent": agent})
            elif ctx.hook_type == HookType.ACTION_BLOCKED:
                self._blocked.add(1, {"agent": agent})
            elif ctx.hook_type == HookType.AUTONOMY_DEGRADED:
                self._degraded.add(1, {"agent": agent})
            elif ctx.hook_type == HookType.TOKEN_USAGE:
                total = int(ctx.get("tokens", 0) or 0)
                if total:
                    self._tokens.record(total, {"agent": agent, "model": ctx.get("model", "")})
        except Exception as exc:  # noqa: BLE001
            log.debug("metrics.record_failed error=%r", exc)
