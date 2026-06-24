"""Approval tokens — the magazine's Step 03 "narrow, expiring approval token".

When the gate returns `requires_approval` for a high-risk write, an operator
issues a token scoped to ONE exact action/scope/workflow step. The agent retries
the same action carrying the token; the gate validates it and lets that one write
through. Tokens are:

  * signed with HMAC-SHA256 (tamper-proof, no DB needed to trust the contents),
  * short-lived (expire after AWCP_APPROVAL_TTL_SECONDS),
  * single-use (a used nonce is burned so a token can't be replayed), and
  * bound to the exact (agent_id, workflow_id, task_id, action, scope) tuple.

Env-driven — nothing hardcoded:
  AWCP_APPROVAL_SECRET        HMAC signing secret (default a dev secret; SET in prod)
  AWCP_APPROVAL_TTL_SECONDS   token lifetime in seconds (default 900 = 15 min)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

from awcp.radar.telemetry import log

APPROVAL_SECRET = os.getenv("AWCP_APPROVAL_SECRET", "local-dev-approval-secret")
APPROVAL_TTL_SECONDS = int(os.getenv("AWCP_APPROVAL_TTL_SECONDS", "900"))

# Burned nonces (single-use enforcement). Process-local, like the radar's other
# live overlays (token hard-stop). A nonce only needs to outlive a token's TTL;
# expired entries are swept lazily on each verify so the set stays bounded.
_USED_NONCES: dict[str, float] = {}


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload_b64: str) -> str:
    mac = hmac.new(APPROVAL_SECRET.encode(), payload_b64.encode(), hashlib.sha256)
    return mac.hexdigest()


def _sweep(now: float) -> None:
    for nonce, exp in list(_USED_NONCES.items()):
        if exp < now:
            _USED_NONCES.pop(nonce, None)


def issue(
    agent_id: str,
    *,
    action: str = "",
    scope: str = "",
    workflow_id: str = "",
    task_id: str = "",
    ttl: int | None = None,
) -> dict:
    """Mint a signed, single-use approval token for one exact action/step.

    Returns {"token", "expires_at", "scope", "action", ...} so the operator
    surface can show what was granted."""
    now = time.time()
    exp = now + (ttl if ttl is not None else APPROVAL_TTL_SECONDS)
    payload = {
        "agent_id": agent_id,
        "workflow_id": workflow_id,
        "task_id": task_id,
        "action": action,
        "scope": scope,
        "exp": exp,
        "nonce": uuid.uuid4().hex,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    token = f"{payload_b64}.{_sign(payload_b64)}"
    log.info(
        "radar.approval.issued agent_id=%s action=%r scope=%r wf=%s ttl=%.0fs",
        agent_id, action, scope, workflow_id, exp - now,
    )
    return {
        "token": token,
        "expires_at": exp,
        "agent_id": agent_id,
        "action": action,
        "scope": scope,
        "workflow_id": workflow_id,
        "task_id": task_id,
    }


def verify(
    token: str,
    *,
    agent_id: str = "",
    action: str = "",
    scope: str = "",
    workflow_id: str = "",
    task_id: str = "",
    consume: bool = True,
) -> tuple[bool, str]:
    """Validate a token for an exact action/step. Returns (ok, reason).

    Checks, in order: structure, signature, expiry, single-use (replay), and that
    each bound field the token carries matches the action being gated. `consume`
    burns the nonce on success (set False for a dry-run/shadow check)."""
    if not token:
        return False, "no approval token supplied"
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return False, "malformed token"
    if not hmac.compare_digest(sig, _sign(payload_b64)):
        return False, "bad signature"
    try:
        payload = json.loads(_b64d(payload_b64))
    except Exception:  # noqa: BLE001
        return False, "undecodable payload"

    now = time.time()
    _sweep(now)
    if float(payload.get("exp", 0)) < now:
        return False, "token expired"

    nonce = payload.get("nonce", "")
    if nonce in _USED_NONCES:
        return False, "token already used (replay)"

    # Each bound field, when present on the token, must match the gated action.
    for field, want in (
        ("agent_id", agent_id),
        ("action", action),
        ("scope", scope),
        ("workflow_id", workflow_id),
        ("task_id", task_id),
    ):
        got = payload.get(field, "")
        if got and want and got != want:
            return False, f"token {field} mismatch (token={got!r} action={want!r})"

    if consume and nonce:
        _USED_NONCES[nonce] = float(payload.get("exp", now))
    return True, "approval token valid"
