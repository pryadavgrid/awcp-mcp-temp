"""Write-action gate + graceful-degradation ladder.

These are the governance primitives that awcp_agents has and awcp.radar lacked.
They are re-implemented here (no import from awcp_agents) and adapted to the
discovery/registry model: instead of gating a tool call *inside* a run we drive,
the radar exposes a gate that an external agent/interceptor asks before doing a
write — the magazine's "gate write actions" applied to runtimes we don't own.

Mapping to awcp_agents:
  - autonomy_profile  active -> recommendation_only -> suspended
    (awcp_agents' active -> recommendation_only -> fatal ladder)
  - active            : writes allowed
  - recommendation_only : writes blocked, agent should recommend not execute
  - suspended         : hard stop (awcp_agents' fatal)
  - a quarantined agent is blocked from writes regardless of profile
    (awcp_agents' admission/quarantine gate)
"""

from __future__ import annotations

import os

from awcp.radar.models import AgentEntry

# System DEFAULTS only. The magazine requires that "each workflow can override
# thresholds and ladders by risk", so these are fallbacks — an agent may declare
# its own ladder/budget at registration (AgentEntry.autonomy_ladder /
# .failure_budget) and the functions below read the per-agent values first.
# All three are env-tunable so NOTHING is hardcoded: the default ladder, the
# default budget, and the risk->budget map can all be redefined at deploy time.
DEFAULT_PROFILE_LADDER: list[str] = [
    s.strip() for s in os.getenv(
        "AGENT_RADAR_LADDER", "active,recommendation_only,suspended"
    ).split(",") if s.strip()
]
DEFAULT_FAILURE_BUDGET = int(os.getenv("AGENT_RADAR_FAILURE_BUDGET", "3"))


def _parse_risk_budget() -> dict[str, int]:
    """Risk tier -> failure budget, from AGENT_RADAR_RISK_BUDGET (e.g.
    "low:5,medium:3,high:1"). Tier names and values are fully configurable — the
    default below is only a seed, and any tier an agent declares that isn't in
    the map simply falls back to DEFAULT_FAILURE_BUDGET. Higher risk = fewer
    tolerated failures (the magazine's "thresholds ... by risk")."""
    out: dict[str, int] = {}
    raw = os.getenv("AGENT_RADAR_RISK_BUDGET", "low:5,medium:3,high:1")
    for pair in raw.split(","):
        if ":" in pair:
            name, _, val = pair.partition(":")
            try:
                out[name.strip().lower()] = int(val)
            except ValueError:
                pass
    return out or {"low": 5, "medium": 3, "high": 1}


RISK_BUDGET: dict[str, int] = _parse_risk_budget()

# Back-compat aliases (some callers/UI may reference these names).
PROFILE_LADDER = DEFAULT_PROFILE_LADDER
FAILURE_BUDGET = DEFAULT_FAILURE_BUDGET


def ladder_for(entry: AgentEntry) -> list[str]:
    """The agent's own degradation ladder, or the system default."""
    return entry.autonomy_ladder or DEFAULT_PROFILE_LADDER


def budget_for(entry: AgentEntry) -> int:
    """The failure budget for this agent. Precedence:
    1. an explicit per-agent failure_budget,
    2. else the budget implied by its risk tier,
    3. else the system default."""
    if entry.failure_budget:
        return entry.failure_budget
    return RISK_BUDGET.get(getattr(entry, "risk", "medium"), DEFAULT_FAILURE_BUDGET)


def _rung(entry: AgentEntry) -> tuple[list[str], int]:
    """Return (ladder, index of the current profile within it)."""
    ladder = ladder_for(entry)
    try:
        return ladder, ladder.index(entry.autonomy_profile)
    except ValueError:
        return ladder, 0


def evaluate_action(entry: AgentEntry, action: str = "", is_write: bool = True) -> dict:
    """The write-action gate. Read actions are always allowed; write actions are
    gated by quarantine status and the agent's position on its OWN ladder.

    Ladder semantics are position-based (no hardcoded rung names): index 0 is
    full autonomy (writes allowed), the last rung is a hard stop, and anything in
    between is recommendation-only.

    Returns {decision: allow|deny, mode, reason, action}.
    """
    base = {"action": action, "mode": entry.autonomy_profile}

    if not is_write:
        return {**base, "decision": "allow", "reason": "read-only action — not gated"}

    # Admission gate: a quarantined agent may never perform a governed write.
    if entry.status == "quarantined":
        return {**base, "mode": "quarantined", "decision": "deny",
                "reason": "agent is quarantined — write actions blocked until onboarded"}

    ladder, idx = _rung(entry)
    if idx >= len(ladder) - 1 and len(ladder) > 1:
        return {**base, "decision": "deny",
                "reason": f"agent at hard stop ('{entry.autonomy_profile}') — no actions permitted"}
    if idx > 0:
        return {**base, "decision": "deny",
                "reason": (f"autonomy reduced ('{entry.autonomy_profile}') — "
                           "recommend, do not execute")}

    return {**base, "decision": "allow", "reason": "approved"}


def next_profile(current: str, ladder: list[str] | None = None) -> str:
    """Return the next rung down the given ladder (clamped at the last rung)."""
    ladder = ladder or DEFAULT_PROFILE_LADDER
    try:
        i = ladder.index(current)
    except ValueError:
        i = 0
    return ladder[min(i + 1, len(ladder) - 1)]


def apply_signal(entry: AgentEntry, ok: bool, reason: str = "") -> dict:
    """Feed an execution outcome into the degradation ladder.

    A success resets the failure budget. A failure increments it, and once the
    agent's OWN budget is exhausted autonomy steps down one rung on the agent's
    OWN ladder. Returns the patch to apply to the entry plus a summary.
    """
    if ok:
        return {
            "patch": {"failure_count": 0},
            "degraded": False,
            "autonomy_profile": entry.autonomy_profile,
        }

    ladder = ladder_for(entry)
    budget = budget_for(entry)
    at_hard_stop = entry.autonomy_profile == ladder[-1]

    count = entry.failure_count + 1
    if count >= budget and not at_hard_stop:
        new_profile = next_profile(entry.autonomy_profile, ladder)
        why = f"failure budget exhausted ({count}/{budget})" + (f": {reason}" if reason else "")
        return {
            "patch": {
                "autonomy_profile": new_profile,
                "autonomy_reason": why,
                "failure_count": 0,
            },
            "degraded": True,
            "autonomy_profile": new_profile,
        }

    return {
        "patch": {"failure_count": count},
        "degraded": False,
        "autonomy_profile": entry.autonomy_profile,
    }
