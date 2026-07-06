"""Service identity for AWCP agents calling the radar under IAM enforcement.

Nothing is hardcoded — every value comes from the environment, so credentials can
be rotated in Keycloak + .env with no code change. When nothing is configured this
returns NO header, so agents behave exactly as before (and IAM's default "off" mode
is completely unaffected).

Credential sources (first that is set wins):
  1. AWCP_AGENT_SERVICE_TOKEN                 a static shared token (matches the
                                              gateway's verify_service static path)
  2. AWCP_AGENT_CLIENT_ID (default awcp-agent)
     + AWCP_AGENT_CLIENT_SECRET               Keycloak client-credentials — a real
                                              service-account JWT, fetched + cached
                                              here and refreshed before expiry.
     (uses KEYCLOAK_URL, default http://localhost:8083, and KEYCLOAK_REALM=AWCP)

Dependency-free (stdlib urllib) so it is safe to import from anywhere, including
standalone agent processes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger("awcp.agent_auth")

_lock = threading.Lock()
_cache = {"token": "", "exp": 0.0}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _fetch_client_credentials() -> tuple[str, float]:
    cid = _env("AWCP_AGENT_CLIENT_ID", "awcp-agent")
    secret = _env("AWCP_AGENT_CLIENT_SECRET")
    if not (cid and secret):
        return "", 0.0
    kc = _env("KEYCLOAK_URL", "http://localhost:8083").rstrip("/")
    realm = _env("KEYCLOAK_REALM", "AWCP")
    url = f"{kc}/realms/{realm}/protocol/openid-connect/token"
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": cid, "client_secret": secret}
    ).encode()
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"content-type": "application/x-www-form-urlencoded"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
            d = json.loads(r.read())
        # refresh a little before the real expiry
        return d["access_token"], time.time() + max(30, int(d.get("expires_in", 60)) - 15)
    except Exception as e:  # noqa: BLE001 — best-effort; no token → no header
        log.warning("agent_auth: client-credentials fetch failed (%s)", e)
        return "", 0.0


def service_token() -> str:
    """Current bearer token for radar calls, or "" when IAM identity is unconfigured."""
    static = _env("AWCP_AGENT_SERVICE_TOKEN")
    if static:
        return static
    with _lock:
        if _cache["token"] and time.time() < _cache["exp"]:
            return _cache["token"]
        _cache["token"], _cache["exp"] = _fetch_client_credentials()
        return _cache["token"]


def auth_headers() -> dict:
    """`{"Authorization": "Bearer <token>"}` when configured, else `{}`."""
    tok = service_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}
