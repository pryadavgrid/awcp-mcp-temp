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
    if risk:
        b = config.RISK_TOKEN_BUDGET.get(risk.strip().lower())
        if b:
            return b
    return config.DEFAULT_TOKEN_BUDGET


def evaluate(agent_id: str, window_total_tokens: int, risk: str | None = None, agent_budget: int | None = None) -> dict:
    """Classify usage vs budget. Pure function of (usage, budget) — bridge.py
    owns the transition detection and side effects."""
    budget = budget_for(agent_id, risk, agent_budget)
    ratio = (window_total_tokens / budget) if budget > 0 else 0.0
    if ratio >= 1.0:
        state = "exhausted"
    elif ratio >= config.WARN_RATIO:
        state = "warn"
    else:
        state = "ok"
    return {
        "agent_id": agent_id,
        "state": state,
        "used_tokens": window_total_tokens,
        "budget_tokens": budget,
        "ratio": round(ratio, 4),
        "warn_ratio": config.WARN_RATIO,
        "window_s": config.BUDGET_WINDOW_S,
    }
