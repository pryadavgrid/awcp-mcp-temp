"""Bridge — the ONLY surface the radar talks to, and the only place wiring lives.

Independence contract
=====================
This package never imports awcp.radar.*. Everything it needs from the radar is
INJECTED once via init_laminar():

    get_agent(agent_id)  -> object|None   registry lookup (for risk/name); the
                                          bridge only reads .risk/.name/.id via
                                          getattr, so any object (or None) works
    on_breach(agent_id, evaluation)       called ONCE per upward crossing into
                                          'exhausted' — the radar maps this to
                                          its degradation ladder
    record_event(kind, agent_id, detail, **extra)
                                          the radar's recent-decisions log

so the radar owns governance and the audit trail, and this folder can be
swapped out later without touching either.

Event intake (the monitoring tap)
=================================
The radar forwards every /tasks/execution/* payload here BEFORE its Temporal
handling, so token accounting works even when Temporal is down (one of the
gaps this module deliberately does not inherit). Token usage is read from the
event in a TAXONOMY-FREE way: ANY event whose payload (top level or .extra)
carries input/output token counts is recorded, whatever its "type" string is.
This avoids re-creating the closed _EVENT_TO_ACTIVITY map problem in the
execution workflow — a new agent step type that reports tokens is counted with
zero code change here.

Accepted token keys (first match wins, checked in event.extra then top level):
  input:  input_tokens | prompt_tokens  | gen_ai.usage.input_tokens
  output: output_tokens | completion_tokens | gen_ai.usage.output_tokens

Per record this module:
  1. appends to the ledger (sliding window + lifetime + optional JSONL);
  2. emits ONE OTel span "laminar.token.usage" with gen_ai.* attributes — it
     flows to the Collector (Tempo/Grafana) AND, via exporter.py fan-out, to
     Laminar, which renders token/cost views from those same attributes;
  3. updates OTel metrics (token counters by agent/direction, breach counter);
  4. re-evaluates the agent's budget and, on an UPWARD state transition
     (ok->warn, ->exhausted), records an event / fires on_breach exactly once.
"""

from __future__ import annotations

import logging
import threading

from awcp.laminar import budget, config
from awcp.laminar.exporter import attach_laminar_exporter, exporter_attached
from awcp.laminar.ledger import LEDGER

log = logging.getLogger("awcp.laminar")

# ── injected radar hooks (no-op defaults keep the package standalone-safe) ────
_get_agent = lambda agent_id: None                     # noqa: E731
_list_agents = lambda: []                              # noqa: E731  (registry roster)
_on_breach = lambda agent_id, evaluation: None         # noqa: E731
_record_event = lambda kind, agent_id="", detail="", **extra: None   # noqa: E731

_initialized = False

# task_id -> {"agent_id", "goal", "framework"} — the bridge's OWN map (the
# radar's exec_workflows map exists only when Temporal is up; this one always).
_tasks: dict[str, dict] = {}
# agent_id -> last budget state, for one-shot transition detection.
_last_state: dict[str, str] = {}
_lock = threading.Lock()

# Concurrent pre-check reservation: tracks tokens reserved by in-flight requests
# (pre_check allowed but record_usage not yet called).  Included in window totals
# for subsequent pre_checks so two simultaneous calls can't both pass the same budget.
_inflight: dict[str, int] = {}
_inflight_lock = threading.Lock()

# OTel handles — created lazily AFTER setup_otel has run (init_laminar time).
_tracer = None
_m_tokens = None        # counter: awcp.laminar.tokens.total {agent, direction}
_m_calls = None         # counter: awcp.laminar.llm.calls.total {agent, model}
_m_breaches = None      # counter: awcp.laminar.budget.breaches.total {agent, level}


def init_laminar(*, get_agent=None, list_agents=None, on_breach=None,
                 record_event=None) -> dict:
    """One-time wiring, called by the radar AFTER setup_otel(). Attaches the
    Laminar exporter (if a key is set) and creates this module's OTel
    instruments on the already-configured global providers."""
    global _get_agent, _list_agents, _on_breach, _record_event, _initialized
    global _tracer, _m_tokens, _m_calls, _m_breaches

    if get_agent is not None:
        _get_agent = get_agent
    if list_agents is not None:
        _list_agents = list_agents
    if on_breach is not None:
        _on_breach = on_breach
    if record_event is not None:
        _record_event = record_event

    if not config.ENABLED:
        log.info("laminar.init disabled via LMNR_ENABLED=false")
        _initialized = False
        return status_summary()

    attach_laminar_exporter()      # no-op without LMNR_PROJECT_API_KEY

    try:                            # OTel handles — optional, accounting works without
        from opentelemetry import metrics, trace
        _tracer = trace.get_tracer("awcp.laminar")
        meter = metrics.get_meter("awcp.laminar")
        _m_tokens = meter.create_counter(
            "awcp.laminar.tokens.total",
            description="LLM tokens observed, by agent and direction", unit="1")
        _m_calls = meter.create_counter(
            "awcp.laminar.llm.calls.total",
            description="LLM calls with reported token usage", unit="1")
        _m_breaches = meter.create_counter(
            "awcp.laminar.budget.breaches.total",
            description="Token-budget warn/exhausted transitions", unit="1")
    except Exception as exc:        # noqa: BLE001
        log.warning("laminar.init.otel_unavailable error=%r", exc)

    _initialized = True
    log.info("laminar.init ok exporter=%s window_s=%s default_budget=%s",
             exporter_attached(), config.BUDGET_WINDOW_S, config.DEFAULT_TOKEN_BUDGET)
    return status_summary()


# ── execution-event intake (called by the radar's /tasks/execution/* routes) ──

def on_execution_start(payload: dict) -> None:
    """Remember task -> agent so later events can be attributed."""
    if not (config.ENABLED and _initialized):
        return
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        return
    with _lock:
        _tasks[task_id] = {
            "agent_id": str(payload.get("agent_id") or "unknown"),
            "goal": str(payload.get("goal") or "")[:200],
            "framework": str(payload.get("framework") or ""),
        }


def _gen_ai_system(model: str) -> str:
    """Derive gen_ai.system from a model name — best-effort, no hardcoded list."""
    m = (model or "").lower()
    if "gpt" in m or "o1" in m or "o3" in m or "o4" in m or "openai" in m:
        return "openai"
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "gemini" in m or "google" in m:
        return "google_ai_studio"
    if "llama" in m or "mistral" in m or "qwen" in m or "deepseek" in m or "ollama" in m:
        return "ollama"
    # provider/model format (e.g. "groq/llama3")
    return model.split("/")[0] if "/" in model else model or "unknown"


def _extract_tokens(event: dict) -> tuple[int, int]:
    """Taxonomy-free token extraction: look in event['extra'] first (the schema's
    open extension point), then at the top level; accept the common aliases."""
    in_keys = ("input_tokens", "prompt_tokens", "gen_ai.usage.input_tokens")
    out_keys = ("output_tokens", "completion_tokens", "gen_ai.usage.output_tokens")

    def _find(d: dict, keys) -> int:
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                return int(v)
        return -1

    extra = event.get("extra") or {}
    tin = _find(extra, in_keys)
    if tin < 0:
        tin = _find(event, in_keys)
    tout = _find(extra, out_keys)
    if tout < 0:
        tout = _find(event, out_keys)
    return max(tin, 0), max(tout, 0)        # -1 (absent) normalizes to 0


def _emit_usage_span(agent_id: str, task_id: str, step: str, model: str,
                     tin: int, tout: int, rec: dict, window: dict,
                     evaluation: dict) -> None:
    """Emit ONE 'laminar.token.usage' OTel span for a metered model call.

    Created on the GLOBAL tracer so it fans out to Laminar (via exporter.py's
    added processor) AND the local Collector/Tempo. Carries BOTH the gen_ai.*
    convention (so Laminar/Grafana render tokens, cost and model natively) and
    awcp.* governance context, and stamps the span's trace/span id back onto the
    ledger record so the API + dashboard can deep-link each call to its trace.

    Shared by BOTH metering paths — execution events (on_execution_event) and the
    LLM-gateway proxy (record_usage) — so a token call appears identically in
    Laminar no matter how it was measured. Best-effort: any failure is swallowed
    so token accounting never breaks on a telemetry hiccup."""
    if _tracer is None:
        return
    try:
        with _tracer.start_as_current_span("laminar.token.usage") as span:
            ctx = span.get_span_context()
            if getattr(ctx, "trace_id", 0):
                rec["trace_id"] = format(ctx.trace_id, "032x")
                rec["span_id"] = format(ctx.span_id, "016x")
            cost = rec.get("cost", 0.0)
            for k, v in {
                # lmnr.span.type = "LLM" tells Laminar's native dashboard to
                # render this span in its LLM views (token counts, cost, model).
                "lmnr.span.type": "LLM",
                # ── Laminar's documented MINIMUM SET for a proper LLM span ──
                # (lmnr/opentelemetry_lib/tracing/attributes.py:Attributes). The
                # native UI only renders token usage when ALL of these are present
                # — in particular llm.usage.total_tokens, without which the span
                # shows as a bare trace with no token counts. gen_ai.response.model
                # is required alongside gen_ai.request.model.
                "gen_ai.system": _gen_ai_system(model),
                "gen_ai.request.model": model,
                "gen_ai.response.model": model,
                "gen_ai.usage.input_tokens": tin,
                "gen_ai.usage.output_tokens": tout,
                "llm.usage.total_tokens": tin + tout,
                # Cost under Laminar's own key (gen_ai.usage.cost). 0.0 is honest
                # for local Ollama models (empty price table); the dollar figure
                # appears when LMNR_PRICE_TABLE prices the model.
                "gen_ai.usage.cost": cost,
                # ── AWCP governance context (rendered as plain attributes) ──
                "awcp.agent.id": agent_id,
                "awcp.task.id": task_id,
                "awcp.step": step,
                "awcp.tokens.cost_usd": cost,
                "awcp.budget.window_used": window["total_tokens"],
                "awcp.budget.limit": evaluation["budget_tokens"],
                "awcp.budget.state": evaluation["state"],
            }.items():
                span.set_attribute(k, v)
    except Exception:                       # noqa: BLE001
        pass


def on_execution_event(task_id: str, event: dict) -> dict | None:
    """Record token usage from one execution event; returns the budget
    evaluation when usage was recorded (the radar may surface it), else None."""
    if not (config.ENABLED and _initialized):
        return None

    tin, tout = _extract_tokens(event)
    if tin <= 0 and tout <= 0:
        return None                     # event carries no token usage — not ours

    with _lock:
        meta = _tasks.get(task_id, {})
    agent_id = meta.get("agent_id", "unknown")
    model = str(event.get("model") or "unknown")
    step = str(event.get("type") or "event")
    entry = _get_agent(agent_id)        # may be None — everything below tolerates it
    risk = getattr(entry, "risk", None)
    agent_budget = getattr(entry, "token_budget", None)

    rec = LEDGER.record(agent_id=agent_id, task_id=task_id, step=step,
                        model=model, input_tokens=tin, output_tokens=tout)

    # metrics (guarded — None when OTel was unavailable at init)
    try:
        if _m_tokens is not None:
            _m_tokens.add(tin, {"agent": agent_id, "direction": "input"})
            _m_tokens.add(tout, {"agent": agent_id, "direction": "output"})
        if _m_calls is not None:
            _m_calls.add(1, {"agent": agent_id, "model": model})
    except Exception:                   # noqa: BLE001
        pass

    window = LEDGER.window_usage(agent_id)
    evaluation = budget.evaluate(agent_id, window["total_tokens"], risk, agent_budget)

    # one span per usage record, carrying BOTH the gen_ai.* convention (so
    # Laminar/Grafana render tokens natively) and awcp.* governance context.
    _emit_usage_span(agent_id, task_id, step, model, tin, tout, rec, window, evaluation)

    _handle_transition(agent_id, evaluation)
    return evaluation


def on_execution_complete(task_id: str, outcome: dict) -> None:
    """Forget the task mapping (window records keep their own timestamps)."""
    if not config.ENABLED:
        return
    with _lock:
        _tasks.pop(task_id, None)


# ── budget-state transitions: act ONCE per crossing, not per event ────────────

_ORDER = {"ok": 0, "warn": 1, "exhausted": 2}


def _handle_transition(agent_id: str, evaluation: dict) -> None:
    state = evaluation["state"]
    with _lock:
        prev = _last_state.get(agent_id, "ok")
        _last_state[agent_id] = state
    if _ORDER[state] <= _ORDER[prev]:
        return                          # no upward crossing — nothing to do

    detail = (f"token budget {state}: {evaluation['used_tokens']}"
              f"/{evaluation['budget_tokens']} in window")
    log.warning("laminar.budget.%s agent_id=%s %s", state, agent_id, detail)
    try:
        if _m_breaches is not None:
            _m_breaches.add(1, {"agent": agent_id, "level": state})
    except Exception:                   # noqa: BLE001
        pass
    _record_event(f"token_{state}", agent_id, detail,
                  used=evaluation["used_tokens"], budget=evaluation["budget_tokens"])
    if state == "exhausted" or (state == "warn" and config.ENFORCE_AT_WARN):
        # The radar-side callback applies governance (degrade the autonomy
        # ladder) — this module only reports the breach.  Firing at "warn"
        # (when LMNR_ENFORCE_AT_WARN is set) steps the ladder down one rung
        # at WARN_RATIO * budget so enforcement is applied before the hard
        # limit, shrinking the overshoot window for execution-event reporters.
        try:
            _on_breach(agent_id, evaluation)
        except Exception as exc:        # noqa: BLE001
            log.warning("laminar.on_breach.error agent_id=%s error=%r", agent_id, exc)


# ── read API used by api.py / the dashboard / radar healthz ──────────────────

def release_inflight(agent_id: str, amount: int) -> None:
    """Release tokens reserved by pre_check after record_usage is called.

    Should be called by the gateway once the model response has been metered.
    Safe to call with amount=0 (no-op), or if pre_check was never called."""
    if not agent_id or amount <= 0:
        return
    with _inflight_lock:
        cur = _inflight.get(agent_id, 0)
        updated = max(0, cur - amount)
        if updated:
            _inflight[agent_id] = updated
        else:
            _inflight.pop(agent_id, None)


def pre_check(agent_id: str, estimated_tokens: int) -> dict:
    """Pre-execution budget check: would spending estimated_tokens exhaust the budget?

    Called BEFORE a model invocation so governance can deny execution without
    tokens being spent.  Returns {"allowed": True/False, "reason": str, ...projection}.

    Fail-open: returns allowed=True when laminar is disabled or not initialized.
    A control-plane error must never block legitimate traffic — the existing
    post-call is_exhausted / record_usage path is the authoritative backstop.

    Concurrent safety: when allowed, reserves estimated_tokens in _inflight so
    that simultaneous pre_checks see each other's pending spend and don't both
    pass the same budget boundary.  Caller MUST call release_inflight() after
    record_usage() to avoid permanently inflating the inflight counter.
    """
    if not (config.ENABLED and _initialized and agent_id and estimated_tokens > 0):
        return {"allowed": True, "reason": "pre_check_skipped"}

    try:
        entry = _get_agent(agent_id)
        risk = getattr(entry, "risk", None)
        agent_budget_val = getattr(entry, "token_budget", None)
        budget_tokens = budget.budget_for(agent_id, risk, agent_budget_val)
        window = LEDGER.window_usage(agent_id)
        # include tokens already reserved by concurrent in-flight requests
        with _inflight_lock:
            inflight = _inflight.get(agent_id, 0)
        current = window["total_tokens"] + inflight
        projection = budget.project(current, estimated_tokens, budget_tokens)
        allowed = projection["projected_state"] != "exhausted"
        if allowed:
            with _inflight_lock:
                _inflight[agent_id] = _inflight.get(agent_id, 0) + estimated_tokens
        return {
            "allowed": allowed,
            "reason": "within_budget" if allowed else "projected_exhaustion",
            **projection,
        }
    except Exception as exc:  # noqa: BLE001 — must never raise in the request path
        log.warning("laminar.pre_check.error agent_id=%s error=%r", agent_id, exc)
        return {"allowed": True, "reason": "pre_check_error"}


def usage_summary(agent_id: str) -> dict:
    entry = _get_agent(agent_id)
    window = LEDGER.window_usage(agent_id)
    evaluation = budget.evaluate(agent_id, window["total_tokens"],
                                 getattr(entry, "risk", None),
                                 getattr(entry, "token_budget", None))
    # latest call's OTel trace (for a deep-link to Tempo from the dashboards)
    recent = LEDGER.recent(agent_id, limit=1)
    last = recent[0] if recent else {}
    last_trace = last.get("trace_id")
    return {
        "agent_id": agent_id,
        "name": getattr(entry, "name", None) or agent_id,
        "risk": getattr(entry, "risk", None),
        "token_budget": getattr(entry, "token_budget", None),   # declared per-agent (or None → tier)
        "autonomy_profile": getattr(entry, "autonomy_profile", None),
        "window": window,
        "lifetime": LEDGER.lifetime_usage(agent_id),
        "budget": evaluation,
        "last_trace_id": last_trace,
        "last_trace_url": config.trace_url(last_trace),
    }


def budget_state(agent_id: str) -> dict:
    """Authoritative, LIVE budget evaluation for one agent (ok|warn|exhausted).

    A pure function of the current sliding-window ledger and the agent's
    RESOLVED budget (operator override -> declared token_budget -> risk tier ->
    default — all from budget.py, nothing hardcoded). Because it is recomputed
    from the window every call, an over-budget agent reads 'exhausted' until its
    window naturally clears or an operator resets it — there is no stored flag to
    go stale. The radar uses this to HARD-STOP execution, so the control lives in
    the control plane, not in the agent.
    """
    entry = _get_agent(agent_id)
    window = LEDGER.window_usage(agent_id)
    return budget.evaluate(agent_id, window["total_tokens"],
                           getattr(entry, "risk", None),
                           getattr(entry, "token_budget", None))


def is_exhausted(agent_id: str) -> bool:
    """True iff the agent has met/exceeded its token budget for the window.

    Fail-open: returns False when this package is disabled or not initialised, so
    turning laminar off simply removes token control and changes nothing else.
    """
    if not (config.ENABLED and _initialized and agent_id):
        return False
    return budget_state(agent_id)["state"] == "exhausted"


def record_usage(agent_id: str, model: str, input_tokens: int, output_tokens: int,
                 task_id: str = "llm-gateway", step: str = "llm_call") -> dict | None:
    """Meter ONE model call straight into the ledger and run the same budget
    evaluation + breach transition the execution-event path does.

    Used by the token-aware LLM gateway, which counts tokens at the source (the
    proxied model response) rather than waiting for the agent to self-report. So
    even an autonomous, uncooperative agent is accounted for — its tokens are
    measured the moment it calls the model, and crossing the budget fires the
    SAME on_breach -> degrade -> process/remote hard-stop chain. Nothing here is
    keyed on a specific agent or model name."""
    if not (config.ENABLED and _initialized and agent_id):
        return None
    model = model or "unknown"
    tin = max(0, int(input_tokens))
    tout = max(0, int(output_tokens))
    rec = LEDGER.record(agent_id=agent_id, task_id=task_id, step=step,
                        model=model, input_tokens=tin, output_tokens=tout)
    try:
        if _m_tokens is not None:
            _m_tokens.add(tin, {"agent": agent_id, "direction": "input"})
            _m_tokens.add(tout, {"agent": agent_id, "direction": "output"})
        if _m_calls is not None:
            _m_calls.add(1, {"agent": agent_id, "model": model})
    except Exception:                       # noqa: BLE001
        pass
    entry = _get_agent(agent_id)
    window = LEDGER.window_usage(agent_id)
    evaluation = budget.evaluate(agent_id, window["total_tokens"],
                                 getattr(entry, "risk", None),
                                 getattr(entry, "token_budget", None))
    # Emit the SAME 'laminar.token.usage' span the execution-event path does, so
    # calls metered at the LLM gateway (the path autonomous agents take) also
    # reach Laminar and deep-link to a trace — previously they only hit the
    # local ledger/metrics, so Laminar showed no token calls for these agents.
    _emit_usage_span(agent_id, task_id, step, model, tin, tout, rec, window, evaluation)
    _handle_transition(agent_id, evaluation)
    return evaluation


def all_usage() -> list[dict]:
    """Every agent with recorded token usage, PLUS every self-registered (running)
    agent that hasn't spent yet — so an agent appears in the monitor, with a
    settable budget, the moment it is registered, not only after its first metered
    LLM call. Scanner-detected processes (source='scan': ollama/temporal/duplicate
    rows) are excluded; their tokens attribute to the agent's own self-registered
    id anyway. Falls back to ledger-only if the registry roster is unavailable."""
    exclude = config.USAGE_EXCLUDE
    ids = [a for a in LEDGER.agents() if a not in exclude]
    seen = set(ids) | exclude
    try:
        for e in _list_agents() or []:
            aid = getattr(e, "id", None)
            if aid and aid not in seen and getattr(e, "source", "") == "self":
                ids.append(aid)
                seen.add(aid)
    except Exception:                       # noqa: BLE001 — never break the feed
        pass
    return [usage_summary(a) for a in ids]


def reset_agent(agent_id: str) -> dict:
    """Operator: clear the window + state so a restored agent starts clean
    (pairs with POST /agents/{id}/autonomy to restore the ladder)."""
    cleared = LEDGER.reset_window(agent_id)
    with _lock:
        _last_state.pop(agent_id, None)
    _record_event("token_reset", agent_id, f"window cleared ({cleared} records)")
    return {"agent_id": agent_id, "cleared_records": cleared}


def status_summary() -> dict:
    pol = budget.get_policy()           # LIVE (operator-edited) policy, not the env seed
    return {
        "enabled": config.ENABLED,
        "initialized": _initialized,
        "laminar_export": exporter_attached(),
        "laminar_endpoint": config.OTLP_ENDPOINT if config.PROJECT_API_KEY else None,
        "window_s": config.BUDGET_WINDOW_S,
        "default_budget_tokens": pol["default"],
        "risk_budgets": pol["tiers"],
        "warn_ratio": pol["warn_ratio"],
        "price_table_models": sorted(config.PRICE_TABLE.keys()),
        "agents_tracked": len(LEDGER.agents()),
        "active_tasks": len(_tasks),
    }
