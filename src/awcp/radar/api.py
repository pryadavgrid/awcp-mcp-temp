"""Agent Radar — REST API + minimal web view.

A background scanner auto-detects running agentic environments (agent frameworks,
MCP servers, LLM runtimes, orchestrators); agents can also self-register. Each
new entry is onboarded via a per-agent Temporal workflow (map -> quarantine-check
-> link-MCP -> admit) when a Temporal server is reachable, else inline. Detected/
uninstrumented agents stay 'quarantined' until they have telemetry + policy hooks.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from awcp.radar import onboarding, policy
from awcp.radar.models import AgentEntry, RegisterRequest
from awcp.radar.store import REGISTRY
from awcp.radar.scanner import SCANNER
from awcp.radar.temporal.config import TEMPORAL_SERVER_URL, TASK_QUEUE, TEMPORAL_UI_BASE
from awcp.radar.temporal.workflows.onboarding import AgentOnboardingWorkflow
from awcp.radar.temporal.workflows.execution import AgentExecutionWorkflow
from awcp.radar.temporal.activities.onboarding import (
    map_identity,
    quarantine_check,
    link_mcp,
    admit,
)
from awcp.radar.temporal.activities.execution import (
    execution_setup,
    execution_llm_call,
    execution_web_search,
    execution_tool_call,
    execution_synthesize_answer,
    execution_complete,
)

# --- Telemetry: link the registry into the shared awcp.observability stack ---
from awcp.observability.setup import setup_otel
from awcp.observability.middleware import instrument_fastapi
from awcp.radar.telemetry import get_radar_metrics, radar_span, log

setup_otel("awcp-radar")
METRICS = get_radar_metrics()
_OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() == "true"

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# runtime state shared with request handlers
STATE: dict = {
    "temporal": False,
    "client": None,
    # task_id → (workflow_id, workflow_handle) for execution workflows
    "exec_workflows": {},
}

# Env-driven task queue for execution workflows (separate from onboarding)
EXEC_TASK_QUEUE = os.getenv("AGENT_EXEC_TASK_QUEUE", "agent-task-execution")

# Recent-decisions log: a registry-local, in-memory ring buffer of the last N
# governance events (onboarding / gate / degradation / operator actions). This is
# NOT the durable Evidence Ledger (a separate component) — it's a lightweight live
# audit view so operators can see what the registry just did.
_EVENTS: deque = deque(maxlen=int(os.getenv("AGENT_RADAR_EVENTS_MAX", "200")))


def _record_event(kind: str, agent_id: str = "", detail: str = "", **extra) -> None:
    _EVENTS.appendleft(
        {"ts": time.time(), "kind": kind, "agent_id": agent_id,
         "detail": detail, **extra}
    )


# ----------------------------------------------------------------------
# Onboarding (Temporal when available, inline fallback otherwise)
# ----------------------------------------------------------------------
async def _onboard_inline(agent_id: str) -> None:
    e = REGISTRY.get(agent_id)
    if not e:
        log.warning("radar.onboard.inline.skipped agent_id=%s reason=not_found", agent_id)
        return

    path = "inline"
    status = "quarantined"
    reason: str | None = None

    with radar_span("radar.onboard.inline", {"agent_id": agent_id, "path": path}):
        # Step 1: map identity (normalize owner/runtime/version)
        t0 = time.monotonic()
        with radar_span("radar.onboard.step.map_identity", {"agent_id": agent_id}):
            try:
                patch = onboarding.map_identity_patch(e)
                REGISTRY.patch(agent_id, **patch)
                log.info(
                    "radar.onboard.map_identity agent_id=%s owner=%s runtime=%s dur_ms=%.1f",
                    agent_id, patch.get("owner"), patch.get("runtime"),
                    (time.monotonic() - t0) * 1000,
                )
                METRICS.record_onboarding_step("map_identity", time.monotonic() - t0, "ok", path)
            except Exception as exc:
                log.error(
                    "radar.onboard.step.error step=map_identity agent_id=%s error=%r",
                    agent_id, exc, exc_info=True,
                )
                METRICS.record_onboarding_step("map_identity", time.monotonic() - t0, "error", path)
                raise

        e = REGISTRY.get(agent_id)

        # Step 2: quarantine check (verify telemetry + policy hooks)
        t0 = time.monotonic()
        with radar_span("radar.onboard.step.quarantine_check", {"agent_id": agent_id}):
            try:
                status, reason = onboarding.decide_status(e)
                REGISTRY.patch(agent_id, status=status, quarantine_reason=reason)
                log.info(
                    "radar.onboard.quarantine_check agent_id=%s status=%s reason=%r dur_ms=%.1f",
                    agent_id, status, reason, (time.monotonic() - t0) * 1000,
                )
                METRICS.record_onboarding_step("quarantine_check", time.monotonic() - t0, "ok", path)
            except Exception as exc:
                log.error(
                    "radar.onboard.step.error step=quarantine_check agent_id=%s error=%r",
                    agent_id, exc, exc_info=True,
                )
                METRICS.record_onboarding_step("quarantine_check", time.monotonic() - t0, "error", path)
                raise

        e = REGISTRY.get(agent_id)

        # Step 3: link MCP (enumerate tools if entry exposes an SSE endpoint)
        t0 = time.monotonic()
        with radar_span("radar.onboard.step.link_mcp", {"agent_id": agent_id, "kind": e.kind}):
            try:
                caps, note = await onboarding.link_mcp(e)
                REGISTRY.patch(agent_id, capabilities=caps, onboarding_state="done")
                log.info(
                    "radar.onboard.link_mcp agent_id=%s caps=%d note=%r dur_ms=%.1f",
                    agent_id, len(caps), note, (time.monotonic() - t0) * 1000,
                )
                METRICS.record_onboarding_step("link_mcp", time.monotonic() - t0, "ok", path)
            except Exception as exc:
                log.error(
                    "radar.onboard.step.error step=link_mcp agent_id=%s error=%r",
                    agent_id, exc, exc_info=True,
                )
                METRICS.record_onboarding_step("link_mcp", time.monotonic() - t0, "error", path)
                raise

        METRICS.onboarding_completed.add(1, {"status": status, "path": path})
        _record_event("onboarded", agent_id, status, reason=reason or "", path=path)
        log.info(
            "radar.onboard.completed agent_id=%s status=%s path=%s",
            agent_id, status, path,
        )


async def _onboarding_manager() -> None:
    """Trigger onboarding for any entry that hasn't been onboarded yet."""
    while True:
        try:
            for e in REGISTRY.all():
                if e.onboarding_state is not None:
                    continue
                if not (e.alive or e.source == "self"):
                    continue
                REGISTRY.patch(e.id, onboarding_state="pending")
                if STATE["temporal"] and STATE["client"] is not None:
                    # Unique workflow ID per registration run so every restart
                    # creates a new visible workflow in the Temporal UI.
                    wf_id = f"onboard-{e.id}-{int(time.time())}"
                    try:
                        await STATE["client"].start_workflow(
                            AgentOnboardingWorkflow.run,
                            e.id,
                            id=wf_id,
                            task_queue=TASK_QUEUE,
                        )
                        REGISTRY.patch(
                            e.id, onboarding_state="running", onboarding_workflow_id=wf_id
                        )
                        log.info(
                            "radar.onboarding.temporal.started agent_id=%s workflow_id=%s",
                            e.id, wf_id,
                        )
                    except Exception as exc:
                        log.warning(
                            "radar.onboarding.temporal.fallback agent_id=%s error=%r",
                            e.id, exc,
                        )
                        await _onboard_inline(e.id)
                else:
                    log.debug("radar.onboarding.inline agent_id=%s", e.id)
                    await _onboard_inline(e.id)
        except Exception as exc:
            log.warning("radar.onboarding_manager.error error=%r", exc, exc_info=True)
        await asyncio.sleep(3)


async def _connect_temporal() -> None:
    """Best-effort: connect to Temporal and start an in-process worker."""
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker

        client = await asyncio.wait_for(Client.connect(TEMPORAL_SERVER_URL), timeout=5)

        # One worker handles both task queues — onboarding + task execution
        onboarding_worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[AgentOnboardingWorkflow],
            activities=[map_identity, quarantine_check, link_mcp, admit],
        )
        execution_worker = Worker(
            client,
            task_queue=EXEC_TASK_QUEUE,
            workflows=[AgentExecutionWorkflow],
            activities=[
                execution_setup,
                execution_llm_call,
                execution_web_search,
                execution_tool_call,
                execution_synthesize_answer,
                execution_complete,
            ],
        )
        STATE["client"] = client
        STATE["temporal"] = True
        # Two separate tasks — asyncio.gather() returns a Future in Python 3.12+
        # which create_task() rejects; explicit tasks are cleaner and cancellable.
        STATE["worker_tasks"] = [
            asyncio.create_task(onboarding_worker.run(), name="onboarding-worker"),
            asyncio.create_task(execution_worker.run(), name="execution-worker"),
        ]
        log.info(
            "radar.temporal.connected url=%s onboarding_queue=%s exec_queue=%s",
            TEMPORAL_SERVER_URL, TASK_QUEUE, EXEC_TASK_QUEUE,
        )
    except Exception as exc:
        STATE["temporal"] = False
        STATE["client"] = None
        log.info(
            "radar.temporal.unavailable url=%s reason=%r — falling back to inline onboarding",
            TEMPORAL_SERVER_URL, exc,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("radar.startup starting scanner and connecting to Temporal...")
    SCANNER.start()
    await _connect_temporal()
    mgr = asyncio.create_task(_onboarding_manager())
    log.info("radar.startup complete temporal=%s", STATE["temporal"])
    try:
        yield
    finally:
        log.info("radar.shutdown stopping scanner and workers...")
        mgr.cancel()
        for wt in STATE.get("worker_tasks") or []:
            wt.cancel()
        SCANNER.stop()
        log.info("radar.shutdown complete")


app = FastAPI(title="Agent Radar", lifespan=lifespan)
instrument_fastapi(app)   # auto-trace every radar HTTP route

# ----------------------------------------------------------------------
# Token monitoring & control (awcp.laminar — OPTIONAL, self-contained).
# The laminar package never imports radar internals; the radar injects the
# three hooks it needs right here. A token-budget breach is mapped onto the
# EXISTING degradation ladder (policy.next_profile), so the EXISTING
# write-action gate enforces it — no second enforcement mechanism. Removing
# the src/awcp/laminar folder simply turns all of this off.
# ----------------------------------------------------------------------
try:
    from awcp import laminar as _laminar

    def _on_token_breach(agent_id: str, evaluation: dict) -> None:
        """Token budget exhausted -> step the agent one rung down its OWN
        ladder (same path a failure-budget breach takes in /signal)."""
        e = REGISTRY.get(agent_id)
        if not e:
            return
        ladder = policy.ladder_for(e)
        if e.autonomy_profile == ladder[-1]:
            return                              # already at the hard stop
        new_profile = policy.next_profile(e.autonomy_profile, ladder)
        why = (f"token budget exhausted "
               f"({evaluation['used_tokens']}/{evaluation['budget_tokens']} tokens/window)")
        REGISTRY.patch(agent_id, autonomy_profile=new_profile,
                       autonomy_reason=why, failure_count=0)
        METRICS.record_signal(ok=False, degraded=True)
        _record_event("degraded", agent_id, f"-> {new_profile}", reason=why)
        log.warning("radar.token.degraded agent_id=%s -> %s (%s)",
                    agent_id, new_profile, why)

    _laminar.init_laminar(get_agent=REGISTRY.get,
                          on_breach=_on_token_breach,
                          record_event=_record_event)
    app.include_router(_laminar.router)
    _LAMINAR = True
    log.info("radar.laminar.mounted ui=/laminar/ui")
except Exception as _exc:                       # radar runs fine without the package
    _LAMINAR = False
    log.warning("radar.laminar.unavailable error=%r", _exc)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "agent"


def _to_dict(e: AgentEntry) -> dict:
    d = e.model_dump()
    if e.onboarding_workflow_id:
        d["temporal_url"] = (
            f"{TEMPORAL_UI_BASE}/namespaces/default/workflows/{e.onboarding_workflow_id}"
        )
    # surface the EFFECTIVE degradation policy (after risk/override resolution)
    d["effective_budget"] = policy.budget_for(e)
    d["effective_ladder"] = policy.ladder_for(e)
    return d


@app.get("/agents")
def list_agents() -> list[dict]:
    return [_to_dict(e) for e in REGISTRY.all()]


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict:
    e = REGISTRY.get(agent_id)
    if not e:
        raise HTTPException(status_code=404, detail="agent not found")
    return _to_dict(e)


@app.post("/agents/register")
def register(req: RegisterRequest) -> dict:
    entry = AgentEntry(
        id=req.id or f"reg-{_slug(req.name)}",
        name=req.name,
        kind=req.kind,
        framework=req.framework,
        source="self",
        runtime=req.runtime,
        version=req.version,
        owner=req.owner,
        endpoint=req.endpoint,
        transport=req.transport,
        write_scopes=req.write_scopes,
        feature_flags=req.feature_flags,
        policy_callbacks=req.policy_callbacks,
        telemetry_enabled=req.telemetry_enabled,
        risk=req.risk,
        autonomy_ladder=req.autonomy_ladder,
        failure_budget=req.failure_budget,
    )
    # let the onboarding pipeline decide status/capabilities (re-onboard on update)
    entry.onboarding_state = None
    saved = REGISTRY.register(entry)
    _record_event("registered", saved.id, saved.name, risk=saved.risk)
    log.info(
        "radar.register agent_id=%s name=%r kind=%s framework=%s risk=%s telemetry=%s",
        saved.id, saved.name, saved.kind, saved.framework, saved.risk, saved.telemetry_enabled,
    )
    return _to_dict(saved)


# ----------------------------------------------------------------------
# Write-action gate + degradation ladder (governance, ported from awcp_agents)
# ----------------------------------------------------------------------
class GateRequest(BaseModel):
    action: str = ""
    write: bool = True            # the magazine gates WRITE-capable actions


class SignalRequest(BaseModel):
    ok: bool                      # did the agent's last action succeed?
    reason: str = ""


class AutonomyRequest(BaseModel):
    profile: str                  # operator override: active|recommendation_only|suspended


def _require(agent_id: str) -> AgentEntry:
    e = REGISTRY.get(agent_id)
    if not e:
        raise HTTPException(status_code=404, detail="agent not found")
    return e


@app.post("/agents/{agent_id}/gate")
def gate(agent_id: str, req: GateRequest) -> dict:
    """Evaluate whether an agent may perform an action (the write-action gate).
    An external agent/interceptor calls this before a state-changing action."""
    e = _require(agent_id)
    t0 = time.monotonic()
    decision = policy.evaluate_action(e, action=req.action, is_write=req.write)
    elapsed = time.monotonic() - t0
    METRICS.record_gate(
        decision=decision["decision"],
        mode=decision["mode"],
        duration=elapsed,
        risk=e.risk,
    )
    log.info(
        "radar.gate agent_id=%s action=%r decision=%s mode=%s risk=%s dur_ms=%.2f",
        agent_id, (req.action or "")[:64], decision["decision"],
        decision["mode"], e.risk, elapsed * 1000,
    )
    _record_event("gate", agent_id, f"{decision['decision']} ({decision['mode']})",
                  action=req.action)
    return {"agent_id": agent_id, **decision,
            "status": e.status, "autonomy_profile": e.autonomy_profile}


@app.post("/agents/{agent_id}/signal")
def signal(agent_id: str, req: SignalRequest) -> dict:
    """Report an execution outcome. Failures step autonomy down the ladder once
    the failure budget is exhausted (graceful degradation)."""
    e = _require(agent_id)
    result = policy.apply_signal(e, ok=req.ok, reason=req.reason)
    updated = REGISTRY.patch(agent_id, **result["patch"])
    budget = policy.budget_for(updated)
    METRICS.record_signal(
        ok=req.ok,
        degraded=result["degraded"],
        count=updated.failure_count,
        budget=budget,
    )
    if result["degraded"]:
        log.warning(
            "radar.signal.degraded agent_id=%s from=%s to=%s count=%d budget=%d reason=%r",
            agent_id, e.autonomy_profile, updated.autonomy_profile,
            updated.failure_count, budget, req.reason,
        )
        _record_event("degraded", agent_id,
                      f"-> {updated.autonomy_profile}", reason=updated.autonomy_reason or "")
    elif not req.ok:
        log.info(
            "radar.signal.failure agent_id=%s count=%d budget=%d reason=%r",
            agent_id, updated.failure_count, budget, req.reason,
        )
        _record_event("signal", agent_id, f"failure ({updated.failure_count})",
                      reason=req.reason)
    else:
        log.debug("radar.signal.ok agent_id=%s", agent_id)
    return {
        "agent_id": agent_id,
        "degraded": result["degraded"],
        "autonomy_profile": updated.autonomy_profile,
        "autonomy_reason": updated.autonomy_reason,
        "failure_count": updated.failure_count,
    }


@app.post("/agents/{agent_id}/autonomy")
def set_autonomy(agent_id: str, req: AutonomyRequest) -> dict:
    """Operator override — set the autonomy profile directly (e.g. restore to active)."""
    e = _require(agent_id)
    ladder = policy.ladder_for(e)
    if req.profile not in ladder:
        raise HTTPException(status_code=400, detail=f"profile must be one of {ladder}")
    updated = REGISTRY.patch(
        agent_id, autonomy_profile=req.profile, failure_count=0,
        autonomy_reason=f"operator set to {req.profile}",
    )
    _record_event("autonomy", agent_id, f"operator set to {req.profile}")
    return {"agent_id": agent_id, "autonomy_profile": updated.autonomy_profile}


@app.delete("/agents/{agent_id}")
def deregister(agent_id: str) -> dict:
    """Operator action — remove an entry from the inventory (registry hygiene).
    A still-running scanned process will be re-detected on the next scan."""
    if not REGISTRY.remove(agent_id):
        raise HTTPException(status_code=404, detail="agent not found")
    _record_event("removed", agent_id, "operator removed entry")
    return {"ok": True, "removed": agent_id}


# ----------------------------------------------------------------------
# Agent task execution — start workflow, receive events, complete
# ----------------------------------------------------------------------
class TaskExecStartRequest(BaseModel):
    agent_id: str
    task_id: str
    goal: str
    framework: str = ""


class TaskExecEventRequest(BaseModel):
    type: str
    tool_name: str = ""
    model: str = ""
    query: str = ""
    risk: str = ""
    gate: str = "allowed"
    http_status: int = 200
    call_n: int = 1
    result_len: int = 0
    tools_used: list[str] = []
    extra: dict = {}


class TaskExecCompleteRequest(BaseModel):
    status: str = "done"
    result: str = ""
    tools_used: list[str] = []
    error: str = ""


@app.post("/tasks/execution/start")
async def execution_start(req: TaskExecStartRequest) -> dict:
    """Start an AgentExecutionWorkflow for a task prompt."""
    # token monitor learns task->agent BEFORE any Temporal gating, so token
    # accounting keeps working when Temporal is unavailable
    if _LAMINAR:
        _laminar.on_execution_start(req.model_dump())
    if not (STATE["temporal"] and STATE["client"]):
        log.debug("radar.exec.start.skipped reason=temporal_unavailable task_id=%s", req.task_id)
        return {"ok": False, "reason": "temporal_unavailable"}

    wf_id = f"task-{req.agent_id}-{req.task_id}"
    try:
        handle = await STATE["client"].start_workflow(
            AgentExecutionWorkflow.run,
            {"agent_id": req.agent_id, "task_id": req.task_id,
             "goal": req.goal, "framework": req.framework},
            id=wf_id,
            task_queue=EXEC_TASK_QUEUE,
        )
        STATE["exec_workflows"][req.task_id] = wf_id
        log.info(
            "radar.exec.started agent_id=%s task_id=%s workflow_id=%s",
            req.agent_id, req.task_id, wf_id,
        )
        return {"ok": True, "workflow_id": wf_id}
    except Exception as exc:
        log.warning("radar.exec.start.failed task_id=%s error=%r", req.task_id, exc)
        return {"ok": False, "reason": str(exc)[:200]}


@app.post("/tasks/execution/{task_id}/event")
async def execution_event(task_id: str, req: TaskExecEventRequest) -> dict:
    """Forward a real-time execution event to the running AgentExecutionWorkflow."""
    event = req.model_dump()
    # token monitor taps EVERY event first (taxonomy-free: any event carrying
    # token counts in .extra/top level is recorded; others are ignored). The
    # budget evaluation is returned to the agent so a cooperative runtime can
    # slow down / stop on warn|exhausted — advisory control on top of the
    # authoritative gate, which starts denying once the breach degrades autonomy.
    token_budget = _laminar.on_execution_event(task_id, event) if _LAMINAR else None

    wf_id = STATE["exec_workflows"].get(task_id)
    if not wf_id or not (STATE["temporal"] and STATE["client"]):
        return {"ok": False, "reason": "no_active_workflow", "token_budget": token_budget}

    try:
        handle = STATE["client"].get_workflow_handle(wf_id)
        await handle.signal(AgentExecutionWorkflow.push_event, event)
        log.debug("radar.exec.event task_id=%s type=%s", task_id, req.type)
        return {"ok": True, "token_budget": token_budget}
    except Exception as exc:
        log.warning("radar.exec.event.failed task_id=%s error=%r", task_id, exc)
        return {"ok": False, "reason": str(exc)[:200], "token_budget": token_budget}


@app.post("/tasks/execution/{task_id}/complete")
async def execution_complete_ep(task_id: str, req: TaskExecCompleteRequest) -> dict:
    """Signal the AgentExecutionWorkflow that the task is done."""
    if _LAMINAR:
        _laminar.on_execution_complete(task_id, req.model_dump())
    wf_id = STATE["exec_workflows"].pop(task_id, None)
    if not wf_id or not (STATE["temporal"] and STATE["client"]):
        return {"ok": False, "reason": "no_active_workflow"}

    outcome = req.model_dump()
    try:
        handle = STATE["client"].get_workflow_handle(wf_id)
        await handle.signal(AgentExecutionWorkflow.finish, outcome)
        log.info(
            "radar.exec.completed task_id=%s status=%s workflow_id=%s",
            task_id, req.status, wf_id,
        )
        return {"ok": True, "workflow_id": wf_id}
    except Exception as exc:
        log.warning("radar.exec.complete.failed task_id=%s error=%r", task_id, exc)
        return {"ok": False, "reason": str(exc)[:200]}


@app.get("/events")
def events(limit: int = 50) -> list[dict]:
    """The recent-decisions log (newest first). A live registry audit view — not
    the durable Evidence Ledger."""
    return list(_EVENTS)[: max(1, min(limit, _EVENTS.maxlen or 200))]


@app.get("/healthz")
def healthz() -> dict:
    agents = REGISTRY.all()
    by_kind: dict[str, int] = {}
    by_autonomy: dict[str, int] = {}
    for a in agents:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        by_autonomy[a.autonomy_profile] = by_autonomy.get(a.autonomy_profile, 0) + 1
    return {
        "status": "ok",
        "scan_count": REGISTRY.scan_count,
        "agent_count": len(agents),
        "quarantined": sum(1 for a in agents if a.status == "quarantined"),
        "by_kind": by_kind,
        "by_autonomy": by_autonomy,
        "temporal_connected": STATE["temporal"],
        "otel_enabled": _OTEL_ENABLED,
        "laminar": _laminar.status_summary() if _LAMINAR else {"enabled": False},
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
