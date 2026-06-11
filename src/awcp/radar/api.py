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
from awcp.radar.temporal.activities.onboarding import (
    map_identity,
    quarantine_check,
    link_mcp,
    admit,
)

# --- Telemetry: link the registry into the shared awcp.observability stack ---
from awcp.observability.setup import setup_otel, get_meter
from awcp.observability.middleware import instrument_fastapi

setup_otel("awcp-radar")
_meter = get_meter("awcp.radar")
_m_onboarded = _meter.create_counter(
    "awcp.radar.onboarded.total", description="Agents onboarded by the registry", unit="1")
_m_gate = _meter.create_counter(
    "awcp.radar.gate.decisions.total", description="Write-action gate decisions", unit="1")
_m_degrade = _meter.create_counter(
    "awcp.radar.degradations.total", description="Autonomy degradations applied", unit="1")
_OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() == "true"

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# runtime state shared with request handlers
STATE: dict = {"temporal": False, "client": None}

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
        return
    REGISTRY.patch(agent_id, **onboarding.map_identity_patch(e))
    e = REGISTRY.get(agent_id)
    status, reason = onboarding.decide_status(e)
    REGISTRY.patch(agent_id, status=status, quarantine_reason=reason)
    e = REGISTRY.get(agent_id)
    caps, _note = await onboarding.link_mcp(e)
    REGISTRY.patch(
        agent_id, capabilities=caps, onboarding_state="done",
    )
    _m_onboarded.add(1, {"status": status, "path": "inline"})
    _record_event("onboarded", agent_id, status, reason=reason or "", path="inline")


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
                    wf_id = f"onboard-{e.id}"
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
                    except Exception:
                        # already onboarded before, or transient — do it inline
                        await _onboard_inline(e.id)
                else:
                    await _onboard_inline(e.id)
        except Exception:
            pass
        await asyncio.sleep(3)


async def _connect_temporal() -> None:
    """Best-effort: connect to Temporal and start an in-process worker."""
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker

        client = await asyncio.wait_for(Client.connect(TEMPORAL_SERVER_URL), timeout=5)
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[AgentOnboardingWorkflow],
            activities=[map_identity, quarantine_check, link_mcp, admit],
        )
        STATE["client"] = client
        STATE["temporal"] = True
        STATE["worker_task"] = asyncio.create_task(worker.run())
    except Exception:
        STATE["temporal"] = False
        STATE["client"] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    SCANNER.start()
    await _connect_temporal()
    mgr = asyncio.create_task(_onboarding_manager())
    try:
        yield
    finally:
        mgr.cancel()
        wt = STATE.get("worker_task")
        if wt:
            wt.cancel()
        SCANNER.stop()


app = FastAPI(title="Agent Radar", lifespan=lifespan)
instrument_fastapi(app)   # auto-trace every radar HTTP route


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
    decision = policy.evaluate_action(e, action=req.action, is_write=req.write)
    _m_gate.add(1, {"decision": decision["decision"], "mode": decision["mode"]})
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
    if result["degraded"]:
        _m_degrade.add(1, {"to": updated.autonomy_profile})
        _record_event("degraded", agent_id,
                      f"-> {updated.autonomy_profile}", reason=updated.autonomy_reason or "")
    elif not req.ok:
        _record_event("signal", agent_id, f"failure ({updated.failure_count})",
                      reason=req.reason)
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
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
