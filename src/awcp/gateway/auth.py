"""AWCP gateway IAM enforcement — Keycloak (authn) + OpenFGA (authz).

Phase 4 of IAM.md. This is a SINGLE Starlette middleware that maps each request's
(method, path) to a required permission and enforces it. It is deliberately
**mode-gated** so it can ship without touching current behaviour:

    AWCP_AUTH_MODE = off      (default) middleware is a pass-through — zero change
                     shadow             validate + log what WOULD happen, allow all
                     enforce            actually 401/403 on failures

This mirrors the repo's existing OPA rollout ("shadow mode first", AWCP_OPA_SHADOW).
Nothing here runs until an operator sets AWCP_AUTH_MODE, so the running UI and the
bundle agents (which don't send tokens until Phase 5-7) keep working untouched.

Two identity planes (IAM.md §5):
  • human   — Bearer = a Keycloak user token; validated against the realm JWKS,
              then OpenFGA check(user:<username>, <permission>, system:awcp).
  • service — Bearer = the shared agent token (AWCP_AGENT_SERVICE_TOKEN) or a valid
              awcp-agent service-account JWT. Used by awcp_kit for register / gate /
              task-execution routes; no OpenFGA role needed.

Unmapped routes are treated as PUBLIC (fail-open) on purpose, so mounted surfaces
(the web UI at /, /docs, /laminar/*, /llm/*, static assets) are never blocked. The
documented control-plane endpoints below are the protected set; tighten to
deny-by-default once the full surface is mapped.
"""

from __future__ import annotations

import logging
import os
import re

import jwt
import requests
from jwt import PyJWKClient
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("awcp.gateway.auth")


# ── config (read from env; safe dev defaults) ─────────────────────────────────
def _mode() -> str:
    return os.getenv("AWCP_AUTH_MODE", "off").strip().lower()


def _cfg(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _keycloak_url() -> str:
    return _cfg("KEYCLOAK_URL", "http://localhost:8083").rstrip("/")


def _realm() -> str:
    return _cfg("KEYCLOAK_REALM", "AWCP")


def _openfga_url() -> str:
    return _cfg("OPENFGA_API_URL", "http://localhost:8082").rstrip("/")


def _store_id() -> str:
    return _cfg("OPENFGA_STORE_ID")


def _system_object() -> str:
    return _cfg("AWCP_AUTH_SYSTEM_OBJECT", "system:awcp")


def _service_token() -> str:
    return _cfg("AWCP_AGENT_SERVICE_TOKEN")


class AuthError(Exception):
    """Raised for an invalid/expired/missing user token (→ 401)."""


# ── Keycloak token validation (local JWKS, cached per certs URL) ──────────────
_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client() -> PyJWKClient:
    url = f"{_keycloak_url()}/realms/{_realm()}/protocol/openid-connect/certs"
    client = _jwks_clients.get(url)
    if client is None:
        client = PyJWKClient(url, cache_keys=True)
        _jwks_clients[url] = client
    return client


def verify_user(token: str) -> dict:
    """Validate a Keycloak access token (signature + expiry + realm issuer).

    Audience is only enforced when KEYCLOAK_AUDIENCE is set (Keycloak defaults the
    `aud` to "account", so strict checking needs an audience mapper). Returns
    {sub, username, roles, claims}. Raises AuthError on any failure.
    """
    if not token:
        raise AuthError("missing bearer token")
    audience = _cfg("KEYCLOAK_AUDIENCE")
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience or None,
            options={"verify_aud": bool(audience), "verify_iss": False},
        )
    except Exception as e:  # noqa: BLE001 — any decode failure is a 401
        raise AuthError(f"token validation failed: {e}") from e
    # Loose issuer check (tolerates localhost vs 127.0.0.1 host differences).
    iss = str(claims.get("iss", ""))
    if not iss.endswith(f"/realms/{_realm()}"):
        raise AuthError(f"unexpected issuer: {iss}")
    username = claims.get("preferred_username") or claims.get("sub") or ""
    roles = (claims.get("realm_access") or {}).get("roles", [])
    return {"sub": claims.get("sub"), "username": username, "roles": roles, "claims": claims}


def verify_service(token: str) -> dict | None:
    """Validate the agent/service plane. Accepts the shared static token OR a valid
    awcp-agent service-account JWT. Returns an identity dict or None (unauthorised).
    """
    if not token:
        return None
    shared = _service_token()
    if shared and token == shared:
        return {"username": "awcp-agent", "service": True}
    # Fall back to a real service-account JWT (Phase 6 client-credentials).
    try:
        ident = verify_user(token)
    except AuthError:
        return None
    if ident["username"] == "service-account-awcp-agent" or "agent" in ident.get("roles", []):
        return {**ident, "service": True}
    return None


# ── OpenFGA authorization check ───────────────────────────────────────────────
def openfga_check(username: str, permission: str) -> bool:
    store = _store_id()
    if not store:
        log.warning("auth: OPENFGA_STORE_ID unset — cannot authorize %s", permission)
        return False
    try:
        r = requests.post(
            f"{_openfga_url()}/stores/{store}/check",
            json={
                "tuple_key": {
                    "user": f"user:{username}",
                    "relation": permission,
                    "object": _system_object(),
                }
            },
            timeout=5,
        )
        r.raise_for_status()
        return bool(r.json().get("allowed", False))
    except Exception as e:  # noqa: BLE001
        log.warning("auth: OpenFGA check failed (%s) for %s/%s", e, username, permission)
        return False


# ── (method, path) → (permission, plane) map ─────────────────────────────────
# plane: "human" (Keycloak + OpenFGA <permission>) | "service" (agent token) |
# "public" (no check). First match wins; order matters where paths overlap
# (e.g. /events/audit before /events, service agent-POSTs before operator ones).
_RULES: list[tuple[set[str], re.Pattern, str, str]] = [
    # admin (platform/super admin)
    ({"PUT"}, re.compile(r"^/policy/?$"), "can_admin", "human"),
    ({"DELETE"}, re.compile(r"^/agents/[^/]+/?$"), "can_admin", "human"),
    ({"POST"}, re.compile(r"^/opa/threshold/?$"), "can_admin", "human"),
    # audit read (auditor+)
    ({"GET"}, re.compile(r"^/events/audit/?$"), "can_audit", "human"),
    # service / agent plane
    ({"POST"}, re.compile(r"^/agents/register/?$"), "", "service"),
    ({"POST"}, re.compile(r"^/agents/announce/?$"), "", "service"),
    ({"POST"}, re.compile(r"^/agents/register-card/?$"), "", "service"),
    ({"POST"}, re.compile(r"^/agents/[^/]+/card/refresh/?$"), "", "service"),
    ({"POST"}, re.compile(r"^/agents/[^/]+/gate/?$"), "", "service"),
    ({"POST"}, re.compile(r"^/agents/[^/]+/deregister/?$"), "", "service"),
    ({"POST"}, re.compile(r"^/tasks/execution/.*"), "", "service"),
    # operator writes
    ({"POST"}, re.compile(r"^/approvals/?$"), "can_operate", "human"),
    ({"POST"}, re.compile(r"^/approvals/[^/]+/decide/?$"), "can_operate", "human"),
    ({"POST"}, re.compile(r"^/agents/[^/]+/approve/?$"), "can_operate", "human"),
    ({"POST"}, re.compile(r"^/agents/[^/]+/tokens/[^/]+/(approve|deny)/?$"), "can_operate", "human"),
    ({"POST"}, re.compile(r"^/agents/[^/]+/(signal|autonomy|risk)/?$"), "can_operate", "human"),
    ({"POST"}, re.compile(r"^/user/(submit|ask|upload)/?$"), "can_operate", "human"),
    ({"POST"}, re.compile(r"^/user/(approve|stop)/.*"), "can_operate", "human"),
    # reads (viewer+)
    ({"GET"}, re.compile(r"^/agents(/.*)?$"), "can_read", "human"),
    ({"GET"}, re.compile(r"^/approvals(/.*)?$"), "can_read", "human"),
    ({"GET"}, re.compile(r"^/policy/?$"), "can_read", "human"),
    ({"GET"}, re.compile(r"^/events/?$"), "can_read", "human"),
    ({"GET"}, re.compile(r"^/sandbox/events/?$"), "can_read", "human"),
    ({"GET"}, re.compile(r"^/user/(agents|workflows)/?$"), "can_read", "human"),
    ({"GET"}, re.compile(r"^/user/status/.*"), "can_read", "human"),
    ({"GET"}, re.compile(r"^/opa/(tiers|decisions)(/.*)?$"), "can_read", "human"),
]


def match_route(method: str, path: str) -> tuple[str, str]:
    """Return (permission, plane). Unmapped → ("", "public")."""
    for methods, pat, perm, plane in _RULES:
        if method in methods and pat.match(path):
            return perm, plane
    return "", "public"


def _bearer(request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h[:7].lower() == "bearer " else ""


def _audit(username: str, permission: str, plane: str, result: str, request) -> None:
    """Record the decision to iam.audit (Postgres) AND the gateway log. Both are
    best-effort: the DB insert no-ops when the store is down, and any failure here
    must never break the request."""
    resource = f"{request.method} {request.url.path}"
    log.info(
        "iam-audit user=%s perm=%s plane=%s result=%s %s",
        username or "-", permission or "-", plane, result, resource,
    )
    try:
        from awcp.radar import db
        client = request.client
        db.record_iam_audit(
            username=username, action=permission, resource=resource, result=result,
            plane=plane, ip_address=(client.host if client else ""),
        )
    except Exception as e:  # noqa: BLE001 — audit must never break the request
        log.debug("auth: iam.audit insert skipped (%s)", e)


class AWCPAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        mode = _mode()
        if mode == "off" or request.method == "OPTIONS":
            return await call_next(request)

        perm, plane = match_route(request.method, request.url.path)
        if plane == "public":
            return await call_next(request)

        token = _bearer(request)
        username = ""
        status = 0  # 0 = allow, 401 = unauthenticated, 403 = forbidden
        try:
            if plane == "service":
                ident = await run_in_threadpool(verify_service, token)
                if ident is None:
                    status = 401
                else:
                    username = ident.get("username", "awcp-agent")
            else:  # human
                ident = await run_in_threadpool(verify_user, token)
                username = ident["username"]
                allowed = await run_in_threadpool(openfga_check, username, perm)
                if not allowed:
                    status = 403
        except AuthError as e:
            status = 401
            log.debug("auth: %s", e)
        except Exception as e:  # noqa: BLE001 — never 500 the app from auth
            status = 401 if mode == "enforce" else 0  # fail-closed only in enforce
            log.warning("auth: unexpected error (%s)", e)

        result = "allow" if status == 0 else ("unauthenticated" if status == 401 else "deny")
        _audit(username, perm, plane, result, request)

        if mode == "enforce" and status != 0:
            detail = "authentication required" if status == 401 else "forbidden"
            return JSONResponse({"detail": detail}, status_code=status)
        # shadow (and enforce-allow): proceed
        return await call_next(request)


def auth_startup_banner() -> str:
    """One-line summary for the gateway startup log."""
    mode = _mode()
    if mode == "off":
        return "IAM auth: OFF (pass-through; set AWCP_AUTH_MODE=shadow|enforce to enable)"
    return (
        f"IAM auth: {mode.upper()} · keycloak={_keycloak_url()}/realms/{_realm()} "
        f"· openfga={_openfga_url()} store={_store_id() or 'UNSET'}"
    )
