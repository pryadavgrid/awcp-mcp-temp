"""Policy engine selector — local / opa / shadow.

The single place the radar gate routes through (api.gate -> policy_engine.evaluate).
It lets OPA roll out safely WITHOUT changing the existing Python gate's behaviour:

  AWCP_POLICY_ENGINE=local    use the existing Python gate (policy.evaluate_action)
  AWCP_POLICY_ENGINE=opa      enforce OPA's decision (awcp.rego), with approval tokens
  AWCP_POLICY_ENGINE=shadow   enforce LOCAL, also run OPA, log every mismatch
                              (default — the magazine's safe-rollout strategy)

Magazine alignment: Step 03 (gate) is declarative and lives in OPA; Step 04
(graceful degradation) is the stateful autonomy ladder and stays in Python. So
this module computes the ladder facts (current rung -> writes blocked / hard stop)
from policy.py and PASSES them to OPA as input; OPA then makes the declarative
allow / deny / requires-approval call on top. Nothing is hardcoded — the mode,
the OPA endpoint, and the approval secret/TTL are all env-driven.
"""

from __future__ import annotations

import os

from awcp.radar import approval, opa, policy
from awcp.radar.models import AgentEntry
from awcp.radar.telemetry import log

ENGINE_MODE = os.getenv("AWCP_POLICY_ENGINE", "shadow").lower()

# Count of local/OPA disagreements seen in shadow mode — surfaced on /policy/status
# so an operator can tell when OPA has reached parity and is safe to enforce.
_SHADOW_MISMATCHES = 0


def build_opa_input(entry: AgentEntry, req, *, token_valid: bool) -> dict:
    """Build the OPA `input` document for one gate evaluation.

    AWCP computes the degradation-ladder facts here (Python owns Step 04) and
    hands OPA the declarative facts it needs for Step 03."""
    ladder, idx = policy._rung(entry)
    write_block_index = policy._write_block_index(ladder)
    hard_stop = idx >= len(ladder) - 1 and len(ladder) > 1
    write_blocked = idx >= write_block_index
    return {
        "agent": {
            "id": entry.id,
            "status": entry.status,
            "autonomy_profile": entry.autonomy_profile,
            "write_scopes": list(entry.write_scopes or []),
            "risk": getattr(entry, "risk", "medium"),
            # Stateful ladder facts, precomputed by AWCP (the magazine's Step 04).
            "write_blocked": bool(write_blocked),
            "hard_stop": bool(hard_stop),
        },
        "action": {
            "name": getattr(req, "action", "") or "",
            "write": bool(getattr(req, "write", True)),
            "scope": getattr(req, "scope", "") or "",
            "tool_name": getattr(req, "tool_name", "") or "",
        },
        "approval": {"token_valid": bool(token_valid)},
    }


def _verify_token(entry: AgentEntry, req, *, consume: bool) -> tuple[bool, str]:
    token = getattr(req, "approval_token", "") or ""
    if not token:
        return False, "no approval token supplied"
    return approval.verify(
        token,
        agent_id=entry.id,
        action=getattr(req, "action", "") or "",
        scope=getattr(req, "scope", "") or "",
        workflow_id=getattr(req, "workflow_id", "") or "",
        task_id=getattr(req, "task_id", "") or "",
        consume=consume,
    )


def _opa_decision(entry: AgentEntry, req) -> dict:
    """Evaluate via OPA, applying an approval token only when it actually unblocks.

    First evaluate as if no token were present. If that yields requires_approval
    AND the caller carries a valid token, BURN the token (single-use) and allow —
    so a token is only ever consumed for the exact high-risk write it unblocks."""
    base = opa.query(build_opa_input(entry, req, token_valid=False))
    if base.get("requires_approval"):
        ok, reason = _verify_token(entry, req, consume=False)
        if ok:
            _verify_token(entry, req, consume=True)  # burn it now that it's used
            log.info(
                "radar.policy.opa.approved_via_token agent_id=%s action=%r scope=%r",
                entry.id, getattr(req, "action", ""), getattr(req, "scope", ""),
            )
            return {
                "decision": "allow",
                "reason": "approved — valid approval token for this exact action",
                "mode": entry.autonomy_profile,
                "requires_approval": False,
                "approval_token_used": True,
                "policy_id": "awcp.governance.approved_token",
                "stage": policy.effective_stage(entry),
            }
        # Token missing/invalid — surface why so the operator surface can show it.
        base = dict(base)
        base["approval_reason"] = reason
    return base


def evaluate(entry: AgentEntry, req) -> dict:
    """Evaluate the write-action gate under the active engine mode. Returns a
    decision dict in the same shape as policy.evaluate_action (decision/reason/
    mode/stage), plus requires_approval/approval_scope when OPA gates on it."""
    global _SHADOW_MISMATCHES

    if ENGINE_MODE == "local":
        return policy.evaluate_action(
            entry, action=getattr(req, "action", ""),
            is_write=getattr(req, "write", True), scope=getattr(req, "scope", ""),
        )

    if ENGINE_MODE == "opa":
        return _opa_decision(entry, req)

    # shadow (default): LOCAL enforces; OPA runs alongside and mismatches are logged.
    local = policy.evaluate_action(
        entry, action=getattr(req, "action", ""),
        is_write=getattr(req, "write", True), scope=getattr(req, "scope", ""),
    )
    try:
        opa_dec = _opa_decision(entry, req)
        if opa_dec.get("decision") != local.get("decision"):
            _SHADOW_MISMATCHES += 1
            log.warning(
                "radar.policy.shadow.mismatch agent_id=%s action=%r local=%s opa=%s "
                "opa_reason=%r total_mismatches=%d",
                entry.id, getattr(req, "action", ""), local.get("decision"),
                opa_dec.get("decision"), opa_dec.get("reason"), _SHADOW_MISMATCHES,
            )
        # Carry OPA's verdict alongside the enforced one (purely informational).
        local = dict(local)
        local["shadow_opa"] = {
            "decision": opa_dec.get("decision"),
            "reason": opa_dec.get("reason"),
            "requires_approval": opa_dec.get("requires_approval", False),
            "policy_id": opa_dec.get("policy_id"),
        }
    except Exception as exc:  # noqa: BLE001 — shadow must never break the gate
        log.warning("radar.policy.shadow.error agent_id=%s error=%r", entry.id, exc)
    return local


def status(probe: bool = True) -> dict:
    """Engine status for /policy/status + /healthz. With probe=True it pings the
    OPA server for reachability (a network call); /healthz passes probe=False so
    frequent polls stay network-free. opa_reachable is None when not probed or
    when the active mode doesn't use OPA."""
    uses_opa = ENGINE_MODE in ("opa", "shadow")
    return {
        "mode": ENGINE_MODE,
        "opa_url": opa.OPA_URL,
        "opa_package": opa.OPA_PACKAGE,
        "opa_reachable": opa.reachable() if (probe and uses_opa) else None,
        "fail_mode": opa.OPA_FAIL_MODE,
        "approval_ttl_seconds": approval.APPROVAL_TTL_SECONDS,
        "shadow_mismatches": _SHADOW_MISMATCHES,
    }
