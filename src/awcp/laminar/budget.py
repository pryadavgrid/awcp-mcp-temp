"""Token budgets — the CONTROL half: how much may an agent spend per window?

This deliberately mirrors the radar's failure-budget design (radar/policy.py)
so operators reason about ONE consistent model:

    failure budget  : how many FAILURES before autonomy degrades
    token budget    : how many TOKENS per window before autonomy degrades

Budget precedence per agent (highest first), nothing hardcoded:
  1. an OPERATOR override set at runtime (POST /laminar/budgets/{agent_id});
  2. the agent's RISK tier via LMNR_RISK_TOKEN_BUDGET (high risk = small budget,
     the magazine's "thresholds ... by risk");
  3. the system default LMNR_TOKEN_BUDGET.

evaluate() classifies an agent's window usage into three POSITION-BASED states
(no special-cased agent or model names anywhere):

    ok         usage <  WARN_RATIO * budget
    warn       usage >= WARN_RATIO * budget      (heads-up, nothing blocked)
    exhausted  usage >= budget                   (control acts)

WHAT "control acts" means is NOT decided here. bridge.py detects the upward
state TRANSITION and fires the injected on_breach callback; the radar wires
that callback to its existing degradation ladder, after which the existing
write-action gate denies the agent. One governance mechanism, two inputs
(failures and tokens) — exactly the magazine's "degrade autonomy gracefully".
"""

from __future__ import annotations

import threading

from awcp.laminar import config

# Operator overrides set at runtime via the API: agent_id -> tokens/window.
_overrides: dict[str, int] = {}
_lock = threading.Lock()

# ── Runtime token POLICY (operator-editable from the console; no restart) ──────
# Initialized from the env config so behaviour is identical until an operator
# changes it via POST /laminar/policy. This is the magazine's "operators set
# thresholds/ladders by risk", moved off the CLI onto the control surface.
_policy_lock = threading.Lock()
_policy: dict = {
    "default": config.DEFAULT_TOKEN_BUDGET,
    "tiers": dict(config.RISK_TOKEN_BUDGET),     # e.g. {"low":..,"medium":..,"high":..}
    "warn_ratio": config.WARN_RATIO,
}


def get_policy() -> dict:
    with _policy_lock:
        return {"default": _policy["default"], "tiers": dict(_policy["tiers"]),
                "warn_ratio": _policy["warn_ratio"]}


def set_policy(default: int | None = None, tiers: dict | None = None,
               warn_ratio: float | None = None) -> dict:
    """Operator edits the token policy at runtime. Only valid values are applied;
    everything else is left untouched (partial updates are fine)."""
    with _policy_lock:
        if isinstance(default, (int, float)) and default > 0:
            _policy["default"] = int(default)
        if isinstance(tiers, dict):
            for k, v in tiers.items():
                if isinstance(v, (int, float)) and v > 0:
                    _policy["tiers"][str(k).strip().lower()] = int(v)
        if isinstance(warn_ratio, (int, float)) and 0 < warn_ratio <= 1:
            _policy["warn_ratio"] = float(warn_ratio)
    return get_policy()


def set_budget(agent_id: str, tokens: int) -> None:
    """Operator override (POST /laminar/budgets/{agent_id})."""
    with _lock:
        if tokens > 0:
            _overrides[agent_id] = int(tokens)
        else:                       # 0 / negative clears the override
            _overrides.pop(agent_id, None)


def overrides() -> dict[str, int]:
    with _lock:
        return dict(_overrides)


def budget_for(agent_id: str, risk: str | None = None, agent_budget: int | None = None) -> int:
    """Resolve this agent's tokens-per-window budget (precedence above)."""
    with _lock:
        if agent_id in _overrides:
            return _overrides[agent_id]
    # The magazine's specific agent_budget overrides the generic risk budget
    if agent_budget is not None and agent_budget > 0:
        return agent_budget
    pol = get_policy()                           # runtime policy (operator-editable)
    if risk:
        b = pol["tiers"].get(risk.strip().lower())
        if b:
            return b
    return pol["default"]


def evaluate(agent_id: str, window_total_tokens: int, risk: str | None = None, agent_budget: int | None = None) -> dict:
    """Classify usage vs budget. Pure function of (usage, budget) — bridge.py
    owns the transition detection and side effects."""
    budget = budget_for(agent_id, risk, agent_budget)
    warn_ratio = get_policy()["warn_ratio"]
    # 10% (configurable) grace band: an agent is only "exhausted" past
    # (1 + OVERSHOOT_RATIO) * budget, so control tolerates a small overshoot.
    exhaust_ratio = 1.0 + config.OVERSHOOT_RATIO
    ratio = (window_total_tokens / budget) if budget > 0 else 0.0
    if ratio >= exhaust_ratio:
        state = "exhausted"
    elif ratio >= warn_ratio:
        state = "warn"
    else:
        state = "ok"
    return {
        "agent_id": agent_id,
        "state": state,
        "used_tokens": window_total_tokens,
        "budget_tokens": budget,
        "ratio": round(ratio, 4),
        "warn_ratio": warn_ratio,
        "window_s": config.BUDGET_WINDOW_S,
    }


def project(window_total_tokens: int, estimated_tokens: int,
            budget_tokens: int, warn_ratio: float | None = None) -> dict:
    """Pure pre-execution projection: what budget state would result from spending
    estimated_tokens more on top of the current window total?

    Mirrors evaluate() but takes concrete values rather than resolving them from
    the registry, so callers (bridge.pre_check) control the lookup and this
    function stays a pure computation. Never raises — used in the hot request path.
    """
    wr = warn_ratio if warn_ratio is not None else get_policy()["warn_ratio"]
    exhaust_ratio = 1.0 + config.OVERSHOOT_RATIO   # same 10% grace band as evaluate()
    projected = window_total_tokens + estimated_tokens
    ratio = (projected / budget_tokens) if budget_tokens > 0 else 0.0
    if ratio >= exhaust_ratio:
        state = "exhausted"
    elif ratio >= wr:
        state = "warn"
    else:
        state = "ok"
    return {
        "current_tokens":    window_total_tokens,
        "estimated_tokens":  estimated_tokens,
        "projected_tokens":  projected,
        "budget_tokens":     budget_tokens,
        "projected_ratio":   round(ratio, 4),
        "projected_state":   state,
    }
