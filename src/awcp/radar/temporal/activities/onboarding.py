"""Temporal activities for agent onboarding.

Each step mutates the shared in-memory REGISTRY (the worker runs in the same
process as the API), and returns a short string so the step's outcome is visible
in the Temporal workflow history.
"""

from __future__ import annotations

from temporalio import activity

from awcp.radar import onboarding
from awcp.radar.store import REGISTRY
from awcp.radar.temporal.mirror import mirror_activity


@activity.defn
async def fetch_card(agent_id: str) -> str:
    """Step 1 — fetch the agent's AgentCard (/.well-known/agent.json), best-effort.

    Card data is ENRICHMENT only: it populates entry.card / entry.skills for
    introspection and ?skill= filtering. The governance fields a card may carry are
    NEVER patched onto the enforced AgentEntry fields (see card.py governance
    boundary). Short-circuits when a card is already present (e.g. register-card
    pre-populated it), so onboarding never re-fetches a card it already has."""
    import time
    e = REGISTRY.get(agent_id)
    if not e:
        result = "missing"
    elif e.card is not None and e.card_fetched_at is not None:
        result = f"card already present ({len(e.skills or [])} skills) — skipping re-fetch"
    else:
        raw, skills, note = await onboarding.fetch_card(e)
        patch: dict = {"card_fetched_at": time.time()}
        if raw is not None:
            patch["card"] = raw
            patch["skills"] = skills
            patch["card_url"] = (e.endpoint or "").rstrip("/") + "/.well-known/agent.json"
        REGISTRY.patch(agent_id, **patch)
        result = note or "no card"
    mirror_activity(agent_id, result)
    return result


@activity.defn
async def map_identity(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        result = "missing"
    else:
        REGISTRY.patch(agent_id, **onboarding.map_identity_patch(e))
        result = "identity mapped"
    mirror_activity(agent_id, result)
    return result


@activity.defn
async def quarantine_check(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        result = "missing"
    else:
        status, reason = onboarding.decide_status(e)
        REGISTRY.patch(agent_id, status=status, quarantine_reason=reason)
        result = f"{status}" + (f" ({reason})" if reason else "")
    mirror_activity(agent_id, result)
    return result


@activity.defn
async def link_mcp(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        result = "missing"
    else:
        caps, note = await onboarding.link_mcp(e)
        REGISTRY.patch(agent_id, capabilities=caps)
        result = note or (f"{len(caps)} tools linked" if caps else "no link")
    mirror_activity(agent_id, result)
    return result


@activity.defn
async def admit(agent_id: str) -> str:
    e = REGISTRY.get(agent_id)
    if not e:
        mirror_activity(agent_id, "missing")
        return "missing"
    REGISTRY.patch(agent_id, onboarding_state="done")
    # Mark the Temporal onboarding run done in ops.onboarding_runs, keyed by the
    # workflow id the manager assigned. Fail-open (no-op without a DB).
    from awcp.radar import db as _db
    _db.record_onboarding_run(
        e.onboarding_workflow_id or f"onboard-{agent_id}",
        agent_id, "done", payload={"status": e.status})
    mirror_activity(agent_id, e.status)
    return e.status
