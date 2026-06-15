"""awcp.laminar — Laminar-backed TOKEN MONITORING & CONTROL for the Agent Radar.

A self-contained subpackage (this folder only) that adds the magazine's
economics/observability layer on top of the radar's existing push-based
execution telemetry, WITHOUT modifying how the radar governs agents:

  monitoring  — every execution event that carries LLM token usage is recorded
                in a per-agent sliding-window ledger, emitted as OTel spans /
                metrics (so it lands in Tempo + Prometheus + Grafana), and
                dual-exported to Laminar (an OpenTelemetry-native LLM
                observability backend) when LMNR_PROJECT_API_KEY is set;

  control     — each agent has a TOKEN BUDGET per sliding window (env/risk/
                operator driven, mirroring the radar's failure-budget pattern).
                When an agent exhausts its budget the module fires an injected
                callback — the radar wires that callback to its EXISTING
                degradation ladder, so an over-spending agent steps down to
                recommendation_only / suspended and the EXISTING write-action
                gate starts denying it. No second enforcement mechanism is
                invented (magazine: "gate write actions", "degrade autonomy
                gracefully").

Design rules for this package (why it lives in its own folder):
  * no imports from awcp.radar.* — everything the radar must provide
    (registry lookups, degradation, event recording) is injected via
    bridge.init_laminar(), so this folder can be replaced/relocated later;
  * nothing hardcoded — endpoints, budgets, price tables, windows are all
    environment-driven with documented defaults (config.py);
  * optional everywhere — without a Laminar key it still does local token
    accounting + control; without OTel it degrades to plain accounting;
  * the radar integrates through exactly three guarded touchpoints
    (see radar/api.py): init + router mount, and the three
    /tasks/execution/* endpoints forwarding their payloads to the bridge.
"""

from awcp.laminar.bridge import (          # noqa: F401  (public surface)
    init_laminar,
    on_execution_start,
    on_execution_event,
    on_execution_complete,
    status_summary,
    budget_state,
    is_exhausted,
    record_usage,
)
from awcp.laminar.api import router        # noqa: F401
