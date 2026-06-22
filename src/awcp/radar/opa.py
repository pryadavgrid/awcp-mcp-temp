"""OPA (Open Policy Agent) adapter — the write-action gate's Policy Decision Point.

The magazine names OPA as the engine behind "Gate Write Actions" (Step 03) and the
Approval Gate Controller ("Temporal + OPA"). Historically the gate decided
everything inline in radar/policy.py. This module externalises that decision to an
OPA server WITHOUT giving up the in-Python guarantees:

  * radar/policy.py stays the single source of truth for the FACTS a decision needs
    (authoritative risk, the resolved ladder, the write-block stages) and for the
    FALLBACK decision;
  * OPA (data.awcp.gate) is consulted only when AWCP_OPA_URL is set;
  * any OPA error / timeout / empty result falls back to policy.evaluate_action
    (fail-secure — the gate never fails open because OPA is down);
  * AWCP_OPA_SHADOW=true calls OPA but ENFORCES the Python result, logging any
    disagreement — so the Rego can be proven faithful before it enforces.

The 4-value decision vocabulary (auto_authorized | awaiting_token |
awaiting_operator | denied) matches governance.policy_decisions.decision and lets
the gate issue the magazine's expiring approval tokens for high-risk writes. Token
ISSUANCE / VERIFICATION is a side effect owned by the PEP (api.gate), not here —
this module only DECIDES.

Every knob is env-driven (same pattern as radar/policy.py and laminar/config.py):

  AWCP_OPA_URL                  ""        OPA base URL, e.g. http://localhost:8181.
                                          EMPTY => OPA disabled, policy.py decides
                                          (identical to the pre-OPA behaviour).
  AWCP_OPA_PACKAGE              "awcp/gate"  Rego package path under /v1/data.
  AWCP_OPA_TIMEOUT             "2"        per-request timeout (seconds).
  AWCP_OPA_SHADOW              "false"    call OPA but enforce policy.py; log diffs.
  AWCP_OPA_TOKEN_RISK_TIERS    ""         risk tiers whose WRITES require an expiring
                                          approval token. EMPTY => no tier requires
                                          one (exact parity). Set "high" to turn on
                                          the magazine's approval-token gate.
  AWCP_OPA_OPERATOR_ACTION_CLASSES ""     action classes that always require operator
                                          approval (e.g. "cross_system").
"""

from __future__ import annotations

import os

import httpx

from awcp.radar import policy
from awcp.radar.models import AgentEntry
from awcp.radar.telemetry import log

OPA_URL = os.getenv("AWCP_OPA_URL", "").strip()
OPA_PACKAGE = os.getenv("AWCP_OPA_PACKAGE", "awcp/gate").strip().strip("/")
OPA_TIMEOUT = float(os.getenv("AWCP_OPA_TIMEOUT", "2"))
OPA_SHADOW = os.getenv("AWCP_OPA_SHADOW", "false").strip().lower() == "true"

# Risk tiers whose writes require an approval token. EMPTY by default so the gate
# behaves EXACTLY as before; set AWCP_OPA_TOKEN_RISK_TIERS=high to enable the
# magazine's expiring-approval-token flow for high-risk writes.
TOKEN_RISK_TIERS: list[str] = [
    t.strip().lower() for t in os.getenv("AWCP_OPA_TOKEN_RISK_TIERS", "").split(",") if t.strip()
]
# Action classes that always require human (operator) approval, regardless of risk.
OPERATOR_ACTION_CLASSES: list[str] = [
    c.strip() for c in os.getenv("AWCP_OPA_OPERATOR_ACTION_CLASSES", "").split(",") if c.strip()
]


def enabled() -> bool:
    """True when an OPA server is configured (AWCP_OPA_URL set)."""
    return bool(OPA_URL)


def _config() -> dict:
    """The decision config OPA needs — passed in `input` so OPA holds no state and
    the Python fallback uses the identical values."""
    return {
        "write_block_stages": sorted(policy.WRITE_BLOCK_STAGES),
        "token_risk_tiers": TOKEN_RISK_TIERS,
        "operator_action_classes": OPERATOR_ACTION_CLASSES,
    }


def build_input(entry: AgentEntry, action: str, is_write: bool, scope: str,
                action_class: str = "") -> dict:
    """The OPA `input` document. Facts come from policy.py's RESOLVED values
    (authoritative risk, effective ladder) so OPA and the fallback always agree."""
    return {
        "action": action or "",
        "action_class": action_class or "",
        "is_write": bool(is_write),
        "scope": scope or "",
        "agent": {
            "id": entry.id,
            "status": entry.status,
            "autonomy_profile": entry.autonomy_profile,
            "write_scopes": list(entry.write_scopes or []),
            "ladder": policy.ladder_for(entry),
            "risk": policy.authoritative_risk(entry),
        },
        "config": _config(),
    }


def _gate_kind(entry: AgentEntry, base_decision: str, is_write: bool,
               action_class: str) -> tuple[str, str, str | None]:
    """Map a policy.evaluate_action allow/deny into the magazine's 4-value gate
    vocabulary plus the enforceable verdict and an optional mode override. The
    single source of truth for the fallback path; the Rego encodes the same logic
    (and the same mode strings) for the OPA path, so the two always agree."""
    if base_decision == "deny":
        return "denied", "deny", None
    if not is_write:
        return "auto_authorized", "allow", None
    if action_class and action_class in OPERATOR_ACTION_CLASSES:
        return "awaiting_operator", "deny", "operator_required"
    if policy.authoritative_risk(entry) in TOKEN_RISK_TIERS:
        return "awaiting_token", "deny", "token_required"
    return "auto_authorized", "allow", None


def _from_policy(entry: AgentEntry, action: str, is_write: bool, scope: str,
                 action_class: str) -> dict:
    """The fallback / parity decision: policy.evaluate_action enriched with the
    4-value gate kind. This is also the shape every caller of evaluate_action
    relies on (decision, mode, reason, stage, gate, engine)."""
    base = policy.evaluate_action(entry, action=action, is_write=is_write, scope=scope)
    gate, verdict, mode = _gate_kind(entry, base["decision"], is_write, action_class)
    out = {**base, "decision": verdict, "gate": gate, "engine": "policy"}
    if mode:
        out["mode"] = mode
    return out


def _query_opa(input_doc: dict) -> dict | None:
    """POST the input to OPA and return data.awcp.gate.result, or None if OPA
    returns no usable decision. Raises on transport errors (caller falls back)."""
    url = f"{OPA_URL.rstrip('/')}/v1/data/{OPA_PACKAGE}/result"
    r = httpx.post(url, json={"input": input_doc}, timeout=OPA_TIMEOUT)
    r.raise_for_status()
    result = r.json().get("result")
    if not isinstance(result, dict) or "decision" not in result or "gate" not in result:
        return None
    return result


def evaluate_action(entry: AgentEntry, action: str = "", is_write: bool = True,
                    scope: str = "", action_class: str = "") -> dict:
    """The gate decision, OPA-first with a fail-secure policy.py fallback.

    Returns the same shape as policy.evaluate_action plus:
      * "gate"   — auto_authorized | awaiting_token | awaiting_operator | denied
      * "engine" — which PDP produced the enforced verdict (opa / policy / a
                   policy(...) fallback variant)

    When AWCP_OPA_URL is unset this is exactly policy.evaluate_action enriched
    with the gate kind, so existing deployments are unchanged.
    """
    fallback = _from_policy(entry, action, is_write, scope, action_class)

    if not OPA_URL:
        return fallback

    input_doc = build_input(entry, action, is_write, scope, action_class)
    try:
        opa_res = _query_opa(input_doc)
    except Exception as exc:  # noqa: BLE001 — OPA must never break the gate
        log.warning("radar.opa.query_failed url=%s error=%r — using policy fallback",
                    OPA_URL, exc)
        return {**fallback, "engine": "policy(opa_error)"}

    if opa_res is None:
        log.warning("radar.opa.empty_result url=%s — using policy fallback", OPA_URL)
        return {**fallback, "engine": "policy(opa_empty)"}

    # Shadow mode: report the OPA verdict and log any disagreement, but ENFORCE
    # the Python decision until the Rego is proven faithful.
    if OPA_SHADOW:
        if (opa_res.get("decision") != fallback.get("decision")
                or opa_res.get("gate") != fallback.get("gate")):
            log.warning(
                "radar.opa.shadow.disagreement agent=%s action=%r policy=%s/%s opa=%s/%s",
                entry.id, (action or "")[:64], fallback.get("decision"),
                fallback.get("gate"), opa_res.get("decision"), opa_res.get("gate"),
            )
        return {**fallback, "engine": "policy(shadow)", "opa": opa_res}

    # Enforce OPA, keeping the action/stage context the fallback computed.
    return {**fallback, **opa_res, "engine": "opa"}
