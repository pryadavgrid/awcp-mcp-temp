"""HTTP surface for the hook system — a small APIRouter the radar includes.

Mirrors how ``awcp.laminar`` exposes its own router: the radar does
``router.include_router(agent_hooks.router)`` so these endpoints live on the same
single port as everything else.

  GET  /hooks            → registered hooks + per-hook stats + system status
  GET  /hooks/recent     → ring buffer of recently dispatched events (newest first)
  POST /hooks/{name}/enable   → turn a hook on
  POST /hooks/{name}/disable  → turn a hook off (without unregistering)

These make the hook system visible in the browser for the demo and let an
operator flip a hook live.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from awcp.agent_hooks.manager import (
    configure_guard,
    get_manager,
    guard_config,
    guard_test,
)

router = APIRouter(prefix="/hooks", tags=["agent-hooks"])


@router.get("")
def list_hooks() -> dict:
    mgr = get_manager()
    return {"status": mgr.status(), "hooks": mgr.list_hooks()}


@router.get("/recent")
def recent(limit: int = 50) -> list[dict]:
    return get_manager().recent(limit)


@router.post("/{name}/enable")
def enable(name: str) -> dict:
    if not get_manager().set_enabled(name, True):
        raise HTTPException(status_code=404, detail=f"unknown hook '{name}'")
    return {"ok": True, "hook": name, "enabled": True}


@router.post("/{name}/disable")
def disable(name: str) -> dict:
    if not get_manager().set_enabled(name, False):
        raise HTTPException(status_code=404, detail=f"unknown hook '{name}'")
    return {"ok": True, "hook": name, "enabled": False}


# ── policy-guard runtime control (so the dashboard can demo it, no restart) ──
class GuardConfig(BaseModel):
    deny_tools: list[str] = []
    enabled: bool = True


class GuardTest(BaseModel):
    agent_id: str = ""
    action: str = ""


@router.get("/guard")
def get_guard() -> dict:
    """Current policy-guard config (loaded? deny-list?)."""
    return guard_config()


@router.post("/guard")
def set_guard(cfg: GuardConfig) -> dict:
    """Turn the policy-guard on with a deny-list (or off). No restart needed."""
    return configure_guard(cfg.deny_tools, cfg.enabled)


@router.post("/guard/test")
def post_guard_test(req: GuardTest) -> dict:
    """Fire a gate evaluation through the guard and report the decision —
    a one-click way to prove the veto from the UI."""
    return guard_test(req.agent_id, req.action)


@router.get("/guard/tools")
def guard_tools() -> dict:
    """The governed-tool catalog + the agents the guard can be exercised against.

    Sourced from the RADAR REGISTRY, not from a live bundle-agent's ``/info``: the
    MCP control server registers its full tool catalog as ``capabilities`` and
    stays up for the whole session, so the deny-list and the test gate remain
    configurable even when no task-worker agent process is currently running
    (which is exactly when the old "union of running agents' tools" went empty).
    Falls back to an empty catalog if the registry is unreachable."""
    try:
        from awcp.radar.api import list_agents as _list_agents
        agents = _list_agents()
    except Exception:  # noqa: BLE001
        agents = []

    tools: set[str] = set()
    out_agents: list[dict] = []
    for a in agents:
        # `capabilities` is the persisted catalog (esp. the MCP server's); `tools`
        # is whatever a live agent additionally advertised. Union both.
        for t in (a.get("capabilities") or []):
            tools.add(t)
        for t in (a.get("tools") or []):
            tools.add(t)
        out_agents.append({
            "id": a.get("id"),
            "name": a.get("name") or a.get("id"),
            "kind": a.get("kind"),
            "alive": a.get("alive"),
        })
    return {"tools": sorted(t for t in tools if t), "agents": out_agents}
