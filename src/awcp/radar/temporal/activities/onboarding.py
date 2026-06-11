"""Temporal activities for agent onboarding.

Each step mutates the shared in-memory REGISTRY (the worker runs in the same
process as the API), and returns a short string so the step's outcome is visible
in the Temporal workflow history.
"""

from __future__ import annotations

from temporalio import activity

from awcp.radar import onboarding
from awcp.radar.store import REGISTRY


@activity.defn
async def map_identity(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        return "missing"
    REGISTRY.patch(agent_id, **onboarding.map_identity_patch(e))
    return "identity mapped"


@activity.defn
async def quarantine_check(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        return "missing"
    status, reason = onboarding.decide_status(e)
    REGISTRY.patch(agent_id, status=status, quarantine_reason=reason)
    return f"{status}" + (f" ({reason})" if reason else "")


@activity.defn
async def link_mcp(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        return "missing"
    caps, note = await onboarding.link_mcp(e)
    REGISTRY.patch(agent_id, capabilities=caps)
    return note or (f"{len(caps)} tools linked" if caps else "no link")


@activity.defn
async def admit(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        return "missing"
    REGISTRY.patch(agent_id, onboarding_state="done")
    return e.status
