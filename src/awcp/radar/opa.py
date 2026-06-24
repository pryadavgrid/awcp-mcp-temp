"""OPA (Open Policy Agent) client — the magazine's Step 03 policy engine.

A thin HTTP client around an OPA server's Data API. The radar's policy_engine
builds a governance `input` document (see build_opa_input there) and asks OPA to
evaluate it against awcp.rego; OPA returns a decision dict in the same shape as
the Python gate (awcp.radar.policy.evaluate_action).

Everything is env-driven — nothing hardcoded:
  AWCP_OPA_URL        OPA base URL                 (default http://localhost:8181)
  AWCP_OPA_PACKAGE    rego data path to query      (default awcp/governance/decision)
  AWCP_OPA_TIMEOUT    per-query timeout in seconds (default 1.0)
  AWCP_OPA_FAIL_MODE  "open" | "closed" — what to do when OPA is unreachable or
                      errors (default "open": allow, matching the radar gate's
                      fail-open philosophy; "closed" denies).
"""

from __future__ import annotations

import os

import httpx

from awcp.radar.telemetry import log

OPA_URL = os.getenv("AWCP_OPA_URL", "http://localhost:8181").rstrip("/")
# The rego rule path under /v1/data. Slashes, not dots: package awcp.governance,
# rule `decision` -> awcp/governance/decision.
OPA_PACKAGE = os.getenv("AWCP_OPA_PACKAGE", "awcp/governance/decision").strip("/")
OPA_TIMEOUT = float(os.getenv("AWCP_OPA_TIMEOUT", "1.0"))
OPA_FAIL_MODE = os.getenv("AWCP_OPA_FAIL_MODE", "open").lower()


def _fail_decision(reason: str) -> dict:
    """The decision returned when OPA cannot be consulted, per AWCP_OPA_FAIL_MODE."""
    allow = OPA_FAIL_MODE != "closed"
    return {
        "decision": "allow" if allow else "deny",
        "reason": f"OPA unavailable — failing {'open' if allow else 'closed'}: {reason}",
        "mode": "opa_unavailable",
        "requires_approval": False,
        "policy_id": "awcp.governance.opa_unavailable",
    }


def query(input_doc: dict) -> dict:
    """Evaluate `input_doc` against the OPA policy and return the decision dict.

    On any failure (OPA down, non-200, missing/empty result) falls back to a
    decision per AWCP_OPA_FAIL_MODE so a missing policy engine never hard-breaks
    the gate (unless ops opt into fail-closed)."""
    url = f"{OPA_URL}/v1/data/{OPA_PACKAGE}"
    try:
        resp = httpx.post(url, json={"input": input_doc}, timeout=OPA_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — OPA must never crash the gate
        log.warning("radar.opa.unreachable url=%s error=%r", url, exc)
        return _fail_decision(f"{type(exc).__name__}")
    if resp.status_code != 200:
        log.warning("radar.opa.http_error url=%s status=%s", url, resp.status_code)
        return _fail_decision(f"HTTP {resp.status_code}")
    result = (resp.json() or {}).get("result")
    if not isinstance(result, dict) or "decision" not in result:
        # Undefined result means no rule matched / wrong package path.
        log.warning("radar.opa.no_decision url=%s result=%r", url, result)
        return _fail_decision("policy returned no decision (check AWCP_OPA_PACKAGE)")
    return result


def reachable() -> bool:
    """Best-effort liveness probe of the OPA server (for /policy/status)."""
    try:
        resp = httpx.get(f"{OPA_URL}/health", timeout=OPA_TIMEOUT)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False
