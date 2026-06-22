"""Expiring approval tokens — the PEP-side orchestration for high-risk writes.

When the PDP (radar/opa.evaluate_action) returns `awaiting_token` /
`awaiting_operator`, the gate (radar/api.gate) needs to either VERIFY a token the
caller presented or ISSUE a fresh pending one and hold the action. That side
effect lives here, on top of the durable token table helpers in radar/db.py, so
api.py stays thin and the SQL stays in one place.

This realises the magazine's Scenario B: "The token authorizes only the approved
action class for one branch and one expiry window. All other actions remain
blocked, logged, and replayable." It is fail-secure — if the governance DB is
unavailable, no token can be issued or verified, so the gate denies the write
rather than granting one that can't be audited.
"""

from __future__ import annotations

from awcp.radar import db as _db
from awcp.radar.models import AgentEntry

# Risk tier -> the numeric(4,3) value stored on governance.approval_tokens.risk.
# Tier strings are AWCP's canonical low/medium/high; anything else maps to None.
_RISK_NUMERIC: dict[str, float] = {"low": 0.2, "medium": 0.5, "high": 0.9}


def risk_numeric(tier: str | None) -> float | None:
    return _RISK_NUMERIC.get((tier or "").strip().lower())


def available() -> bool:
    """True when the governance DB backing the token table is reachable."""
    return _db.enabled()


def issue(entry: AgentEntry, action: str, action_class: str, scope: str,
          risk_tier: str, workflow_id: str = "", branch_id: str = "",
          max_uses: int = 1) -> str | None:
    """Issue a pending, branch-scoped, expiring token for one gated write.

    The token's write_scopes are the action's scope when one is given, else the
    agent's full declared scopes — so a presented token can be matched back to the
    exact action. Returns the token id, or None when the DB is unavailable."""
    write_scopes = [scope] if scope else list(entry.write_scopes or [])
    return _db.issue_gate_token(
        agent_id=entry.id,
        action_class=action_class or "gated_write",
        write_scopes=write_scopes,
        risk=risk_numeric(risk_tier),
        workflow_id=workflow_id or None,
        branch_id=branch_id or None,
        context_diff={"action": action, "scope": scope, "risk": risk_tier},
        requested_by=entry.owner,
        max_uses=max_uses,
    )


def verify_and_consume(token_id: str, agent_id: str, scope: str = "") -> tuple[bool, str]:
    """Verify + single-use consume a token for this agent/scope. (ok, reason)."""
    return _db.verify_and_consume_gate_token(token_id, agent_id, scope)


def decide(token_id: str, decision: str, decided_by: str | None = None) -> bool:
    """Operator approves/denies one pending token. decision ∈ approved|denied."""
    return _db.decide_gate_token(token_id, decision, decided_by)


def get(token_id: str) -> dict | None:
    return _db.get_gate_token(token_id)


def list_for_agent(agent_id: str, limit: int = 50) -> list[dict]:
    return _db.list_gate_tokens(agent_id, limit)
