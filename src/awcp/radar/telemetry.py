"""Radar telemetry — metrics, distributed traces, and structured logging for
every radar operation: scanner cycles, onboarding stages, gate decisions,
degradation signals, and live registry state.

This is the single source of truth for all awcp-radar instrumentation. It
mirrors the AWCPMetrics pattern from awcp.observability.middleware but covers
the radar's own domain. All radar modules import `log`, `radar_span()`, and
`get_radar_metrics()` from here.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from opentelemetry.metrics import Observation
from opentelemetry.trace import Status, StatusCode

from awcp.observability.setup import get_tracer, get_meter

# All radar modules share this logger. LoggingInstrumentor (wired in
# setup_otel) auto-injects trace_id + span_id into every log record.
log = logging.getLogger("awcp.radar")

_tracer = get_tracer("awcp.radar")


class RadarMetrics:
    """Business metrics for the AWCP Agent Radar.

    Instantiate exactly once via get_radar_metrics(); do not construct directly.
    Covers four domains: scanner cycles, onboarding steps, gate decisions, and
    degradation signals. Registry state is exposed via observable (pull) gauges.
    """

    def __init__(self) -> None:
        m = get_meter("awcp.radar")

        # ── Scanner ──────────────────────────────────────────────────────
        self.scan_cycles = m.create_counter(
            "awcp.radar.scan.cycles.total",
            description="Scanner cycles completed",
            unit="1",
        )
        self.scan_duration = m.create_histogram(
            "awcp.radar.scan.duration.seconds",
            description="Wall-clock time of a complete scan+reconcile cycle",
            unit="s",
        )
        self.scan_agents_found = m.create_histogram(
            "awcp.radar.scan.agents.found",
            description="Agent processes found per scan cycle",
            unit="1",
        )
        self.scan_new_agents = m.create_counter(
            "awcp.radar.scan.new.agents.total",
            description="Agent processes first seen by the scanner",
            unit="1",
        )
        self.scan_errors = m.create_counter(
            "awcp.radar.scan.errors.total",
            description="Scanner cycle errors",
            unit="1",
        )

        # ── Onboarding ───────────────────────────────────────────────────
        self.onboarding_step_duration = m.create_histogram(
            "awcp.radar.onboarding.step.duration.seconds",
            description="Duration of individual onboarding steps",
            unit="s",
        )
        self.onboarding_step_errors = m.create_counter(
            "awcp.radar.onboarding.step.errors.total",
            description="Errors within individual onboarding steps",
            unit="1",
        )
        self.onboarding_completed = m.create_counter(
            "awcp.radar.onboarding.completed.total",
            description="Onboarding pipelines completed (inline or Temporal)",
            unit="1",
        )

        # ── Gate ─────────────────────────────────────────────────────────
        self.gate_decisions = m.create_counter(
            "awcp.radar.gate.decisions.total",
            description="Write-action gate decisions",
            unit="1",
        )
        self.gate_latency = m.create_histogram(
            "awcp.radar.gate.latency.seconds",
            description="Write-action gate evaluation latency",
            unit="s",
        )

        # ── Signals & degradation ────────────────────────────────────────
        self.signals = m.create_counter(
            "awcp.radar.signals.total",
            description="Execution-outcome signals received",
            unit="1",
        )
        self.degradations = m.create_counter(
            "awcp.radar.degradations.total",
            description="Autonomy degradations applied",
            unit="1",
        )
        self.failure_counts = m.create_histogram(
            "awcp.radar.failure.count",
            description="Failure count at the time of a signal (vs budget)",
            unit="1",
        )

        # ── Registry state: observable gauges (sampled at export time) ───
        m.create_observable_gauge(
            "awcp.radar.agents.total",
            callbacks=[_cb_agents_total],
            description="Total agents in the registry",
            unit="1",
        )
        m.create_observable_gauge(
            "awcp.radar.agents.quarantined",
            callbacks=[_cb_agents_quarantined],
            description="Quarantined agents",
            unit="1",
        )
        m.create_observable_gauge(
            "awcp.radar.agents.active",
            callbacks=[_cb_agents_active],
            description="Active (non-quarantined) agents",
            unit="1",
        )
        m.create_observable_gauge(
            "awcp.radar.scan.count",
            callbacks=[_cb_scan_count],
            description="Total scan cycles run since radar startup",
            unit="1",
        )

    # ── Convenience recording methods ────────────────────────────────────────

    def record_scan(
        self, duration: float, found: int, new: int, error: bool = False
    ) -> None:
        attrs = {"error": str(error).lower()}
        self.scan_cycles.add(1, attrs)
        self.scan_duration.record(duration, attrs)
        self.scan_agents_found.record(found, attrs)
        if new:
            self.scan_new_agents.add(new, {})
        if error:
            self.scan_errors.add(1, {})

    def record_onboarding_step(
        self, step: str, duration: float, status: str, path: str = "inline"
    ) -> None:
        attrs = {"step": step, "status": status, "path": path}
        self.onboarding_step_duration.record(duration, attrs)
        if status == "error":
            self.onboarding_step_errors.add(1, {"step": step, "path": path})

    def record_gate(
        self, decision: str, mode: str, duration: float, risk: str = "unknown"
    ) -> None:
        self.gate_decisions.add(1, {"decision": decision, "mode": mode, "risk": risk})
        self.gate_latency.record(duration, {"decision": decision, "mode": mode})

    def record_signal(
        self, ok: bool, degraded: bool, count: int = 0, budget: int = 0
    ) -> None:
        self.signals.add(1, {"ok": str(ok).lower(), "degraded": str(degraded).lower()})
        if degraded:
            self.degradations.add(1, {})
        if not ok and count:
            self.failure_counts.record(count, {"budget": str(budget)})


# ── Observable gauge callbacks ────────────────────────────────────────────────
# Module-level so they don't hold a reference to the RadarMetrics instance.


def _cb_agents_total(options):
    from awcp.radar.store import REGISTRY
    yield Observation(len(REGISTRY.all()))


def _cb_agents_quarantined(options):
    from awcp.radar.store import REGISTRY
    yield Observation(sum(1 for a in REGISTRY.all() if a.status == "quarantined"))


def _cb_agents_active(options):
    from awcp.radar.store import REGISTRY
    yield Observation(sum(1 for a in REGISTRY.all() if a.status != "quarantined"))


def _cb_scan_count(options):
    from awcp.radar.store import REGISTRY
    yield Observation(REGISTRY.scan_count)


# ── Singleton ─────────────────────────────────────────────────────────────────

_METRICS: RadarMetrics | None = None


def get_radar_metrics() -> RadarMetrics:
    """Return the module-level RadarMetrics singleton (lazy-init).

    Safe to call from background threads; first call must happen after
    setup_otel() has been invoked (guaranteed by the FastAPI lifespan order).
    """
    global _METRICS
    if _METRICS is None:
        _METRICS = RadarMetrics()
    return _METRICS


# ── Span helper ───────────────────────────────────────────────────────────────


@contextmanager
def radar_span(name: str, attributes: dict | None = None) -> Generator:
    """Create a named OTel span, set attributes, and record any exception.

    Usage::

        with radar_span("radar.onboard.step.link_mcp", {"agent_id": aid}) as s:
            caps, note = await onboarding.link_mcp(e)
            s.set_attribute("caps", len(caps))
    """
    with _tracer.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                if v is not None:
                    span.set_attribute(k, str(v)[:512])
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)[:256]))
            span.record_exception(exc)
            raise
