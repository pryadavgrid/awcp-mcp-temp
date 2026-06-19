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
# The magazine's full graceful sequence (Step 04): at first failure signals the
# control plane "increases trace sampling, tightens retry and concurrency limits,
# shifts to safer profiles, and only then moves to recommendation-only before a
# hard stop." Those map to the intermediate, still-WRITE-CAPABLE rungs below;
# writes are only blocked from `recommendation_only` onward. Still fully
# env-overridable, and an older/simpler ladder (e.g. the 3-rung
# active,recommendation_only,suspended) keeps working unchanged.
DEFAULT_PROFILE_LADDER: list[str] = [
    s.strip() for s in os.getenv(
        "AGENT_RADAR_LADDER",
        "active,trace_boost,throttled,safe_profile,recommendation_only,suspended",
    ).split(",") if s.strip()
]
DEFAULT_FAILURE_BUDGET = int(os.getenv("AGENT_RADAR_FAILURE_BUDGET", "3"))

# Per-rung semantics — what each degradation stage DOES. The control plane owns
# these directives; the runtime honours the operational ones (sampling, retry,
# concurrency, profile) while the gate enforces `writes`/`hard_stop` directly.
# Unknown rung names (custom ladders) fall back to position-based behaviour.
STAGE_SPECS: dict[str, dict] = {
    "active": {
        "writes": True, "trace_sampling": "normal",
        "max_retries": None, "max_concurrency": None, "profile": "primary",
        "note": "full autonomy",
    },
    "trace_boost": {
        "writes": True, "trace_sampling": "full",
        "max_retries": None, "max_concurrency": None, "profile": "primary",
        "note": "raise trace sampling to capture the failure",
    },
    "throttled": {
        "writes": True, "trace_sampling": "full",
        "max_retries": 1, "max_concurrency": 1, "profile": "primary",
        "note": "tighten retry & concurrency limits",
    },
    "safe_profile": {
        "writes": True, "trace_sampling": "full",
        "max_retries": 1, "max_concurrency": 1, "profile": "safe",
        "note": "shift to a safer model/profile",
    },
    "recommendation_only": {
        "writes": False, "trace_sampling": "full",
        "max_retries": 0, "max_concurrency": 1, "profile": "safe",
        "note": "recommend, do not execute (writes blocked)",
    },
    "suspended": {
        "writes": False, "trace_sampling": "full",
        "max_retries": 0, "max_concurrency": 0, "profile": "safe",
        "hard_stop": True, "note": "hard stop — no actions permitted",
    },
}

# Rungs from which writes are blocked. Anything earlier is a graceful,
# write-capable degradation; anything from here on denies writes.
WRITE_BLOCK_STAGES: frozenset[str] = frozenset({"recommendation_only", "suspended"})


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


# ── Authoritative risk (hardening gap #1) ──────────────────────────────────────
# An agent declares its own risk tier at registration, but a COMPROMISED agent
# could declare "low" to win a bigger budget / more leeway and slip past the
# operator. The rule: a self-declared tier may only make an agent MORE
# restrictive, never less. authoritative_risk(entry) is the max of what the agent
# declared and what the control plane ASSIGNS it (from the governance magazine),
# ordered low < medium < high < critical. Everything that consults a risk tier
# for enforcement (budget_for here, the token-budget tier in laminar) should use
# this, not entry.risk, so the declared value can never relax enforcement.
RISK_ORDER: list[str] = [
    s.strip().lower() for s in os.getenv(
        "AGENT_RADAR_RISK_ORDER", "low,medium,high,critical").split(",") if s.strip()
]


def _risk_rank(tier: str | None) -> int:
    """Position of a tier in RISK_ORDER. Unknown tiers rank -1 (least
    restrictive) so any RECOGNISED tier always wins the max — an attacker can't
    smuggle in an unknown string to dodge the ordering."""
    t = (tier or "").strip().lower()
    return RISK_ORDER.index(t) if t in RISK_ORDER else -1


def more_restrictive(a: str | None, b: str | None) -> str:
    """Return whichever of two risk tiers is more restrictive (higher in
    RISK_ORDER). Used both at the gate and during onboarding's identity mapping
    so neither path can DOWNGRADE an agent's risk below what it declared."""
    a_l = (a or "").strip().lower()
    b_l = (b or "").strip().lower()
    if not a_l:
        return b_l or "low"
    if not b_l:
        return a_l
    return a_l if _risk_rank(a_l) >= _risk_rank(b_l) else b_l


def assigned_risk_for(entry: AgentEntry) -> str | None:
    """The risk tier the control plane ASSIGNS this agent from the governance
    magazine (by agent name, else the magazine's __default__). None when the
    magazine has no opinion. Lazy import avoids an import cycle with onboarding."""
    try:
        from awcp.radar import onboarding
        profile = onboarding.magazine_profile(getattr(entry, "name", "") or "")
    except Exception:
        profile = None
    if not profile:
        return None
    return (profile.get("risk") or "").strip().lower() or None


def authoritative_risk(entry: AgentEntry) -> str:
    """The risk tier that ACTUALLY governs this agent: the more restrictive of
    its self-declared tier and the magazine-assigned tier. Self-declaration can
    only tighten, never loosen (hardening gap #1)."""
    return more_restrictive(getattr(entry, "risk", None), assigned_risk_for(entry))


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
    # Use the AUTHORITATIVE risk, not entry.risk: a self-declared "low" must not
    # be able to buy a bigger failure budget than the magazine assigns.
    return RISK_BUDGET.get(authoritative_risk(entry), DEFAULT_FAILURE_BUDGET)


def _rung(entry: AgentEntry) -> tuple[list[str], int]:
    """Return (ladder, index of the current profile within it)."""
    ladder = ladder_for(entry)
    try:
        return ladder, ladder.index(entry.autonomy_profile)
    except ValueError:
        return ladder, 0


def stage_effects(rung: str) -> dict:
    """The operational directives for a degradation rung — what the control plane
    asks the runtime to apply at this stage (trace sampling, retry/concurrency
    caps, model profile) plus whether writes are permitted. Unknown rung names
    (custom ladders) return just the name; callers fall back to position-based
    behaviour for those."""
    spec = STAGE_SPECS.get(rung)
    if not spec:
        return {"stage": rung}
    return {"stage": rung, "writes": spec.get("writes", True),
            "trace_sampling": spec.get("trace_sampling", "normal"),
            "max_retries": spec.get("max_retries"),
            "max_concurrency": spec.get("max_concurrency"),
            "profile": spec.get("profile", "primary"),
            "hard_stop": spec.get("hard_stop", False),
            "note": spec.get("note", "")}


def _write_block_index(ladder: list[str]) -> int:
    """The first ladder index from which writes are blocked. Uses the explicit
    WRITE_BLOCK_STAGES when present (so the magazine's graceful rungs before
    `recommendation_only` keep writing); otherwise falls back to index 1 — the
    old behaviour where any non-active rung blocks writes — so legacy/custom
    ladders are unchanged."""
    for i, rung in enumerate(ladder):
        if rung in WRITE_BLOCK_STAGES:
            return i
    return 1


def effective_stage(entry: AgentEntry) -> dict:
    """The agent's CURRENT degradation directives — surfaced on the agent record
    and the gate response so the runtime (and operators) can honour the active
    sampling/retry/concurrency/profile for this rung."""
    return stage_effects(entry.autonomy_profile)


def evaluate_action(entry: AgentEntry, action: str = "", is_write: bool = True,
                    scope: str = "") -> dict:
    """The write-action gate. Reads are always allowed. Writes are gated by
    quarantine status, the action's DECLARED write scope, and the agent's
    position on its OWN ladder:

      * index 0 (active)                       -> writes allowed;
      * graceful rungs before recommendation   -> STILL write-capable (the
        magazine raises trace sampling / tightens retry & concurrency / shifts to
        a safer profile here — see stage_effects — without blocking writes);
      * recommendation_only onward             -> writes denied (recommend only);
      * the last rung                          -> hard stop, no actions.

    The active stage's directives ride along on the decision so the caller can
    apply them. Nothing is hardcoded — semantics come from STAGE_SPECS / the
    ladder, and unknown rungs fall back to position-based behaviour.
    """
    eff = effective_stage(entry)
    base = {"action": action, "mode": entry.autonomy_profile, "stage": eff}

    if not is_write:
        return {**base, "decision": "allow", "reason": "read-only action — not gated"}

    # Admission gate: a quarantined agent may never perform a governed write.
    if entry.status == "quarantined":
        return {**base, "mode": "quarantined", "decision": "deny",
                "reason": "agent is quarantined — write actions blocked until onboarded"}

    # Declared-scope gate (magazine Step 01: "declared write scopes"). When the
    # caller names the action's scope, it must be one the agent actually declared
    # at registration — an agent may not write outside its granted scopes,
    # regardless of autonomy. Omitting the scope keeps the old behaviour (no
    # scope check), so this is backward-compatible.
    if scope and scope not in (entry.write_scopes or []):
        return {**base, "mode": "out_of_scope", "decision": "deny",
                "reason": (f"action scope '{scope}' is not in the agent's declared "
                           f"write_scopes {list(entry.write_scopes or [])}")}

    ladder, idx = _rung(entry)
    if idx >= len(ladder) - 1 and len(ladder) > 1:
        return {**base, "decision": "deny",
                "reason": f"agent at hard stop ('{entry.autonomy_profile}') — no actions permitted"}
    if idx >= _write_block_index(ladder):
        return {**base, "decision": "deny",
                "reason": (f"autonomy reduced ('{entry.autonomy_profile}') — "
                           "recommend, do not execute")}

    if idx > 0:
        # A graceful, write-capable degradation rung: allowed, but the runtime
        # should apply this stage's directives (sampling/retry/concurrency/profile).
        return {**base, "decision": "allow",
                "reason": f"approved — degraded ('{entry.autonomy_profile}'): {eff.get('note', '')}"}

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
