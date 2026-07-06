"""Self-service account creation — the login page's "Create account".

Replaces the earlier email-approval flow: the user picks a username + password and
the account is created immediately in Keycloak (default role from IAM_SIGNUP_ROLE /
the first of IAM_ALLOWED_ROLES → viewer), plus a matching OpenFGA role tuple, so
they can sign in with it right away. No approval, no email.

Nothing is hardcoded — the Keycloak-admin credentials come from env
(observability/.env, loaded by the gateway). Least privilege by default (viewer).
"""

from __future__ import annotations

import logging
import os
import re

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("awcp.gateway.signup")
router = APIRouter(tags=["iam"])


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _default_role() -> str:
    allowed = [r.strip() for r in _env("IAM_ALLOWED_ROLES", "viewer,operator,auditor").split(",") if r.strip()]
    return _env("IAM_SIGNUP_ROLE", allowed[0] if allowed else "viewer")


def _kc_admin_token() -> str:
    kc = _env("KEYCLOAK_URL", "http://localhost:8083").rstrip("/")
    user = _env("IAM_KC_ADMIN_USER") or _env("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
    pw = _env("IAM_KC_ADMIN_PASSWORD") or _env("KC_BOOTSTRAP_ADMIN_PASSWORD")
    r = requests.post(
        f"{kc}/realms/master/protocol/openid-connect/token",
        data={"client_id": "admin-cli", "grant_type": "password", "username": user, "password": pw},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _openfga_grant(username: str, role: str) -> None:
    url = _env("OPENFGA_API_URL", "http://localhost:8082").rstrip("/")
    store = _env("OPENFGA_STORE_ID")
    if not store:
        return
    try:
        requests.post(f"{url}/stores/{store}/write", json={"writes": {"tuple_keys": [
            {"user": f"user:{username}", "relation": role,
             "object": _env("AWCP_AUTH_SYSTEM_OBJECT", "system:awcp")}]}}, timeout=5)
    except Exception as e:  # noqa: BLE001
        log.warning("signup: OpenFGA grant failed (%s)", e)


class SignupBody(BaseModel):
    username: str
    password: str
    email: str = ""


@router.post("/iam/signup")
def signup(body: SignupBody):
    username = (body.username or "").strip()
    password = body.password or ""
    if len(username) < 3:
        return JSONResponse({"detail": "Username must be at least 3 characters."}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"detail": "Password must be at least 6 characters."}, status_code=400)

    kc = _env("KEYCLOAK_URL", "http://localhost:8083").rstrip("/")
    realm = _env("KEYCLOAK_REALM", "AWCP")
    role = _default_role()
    try:
        at = _kc_admin_token()
        H = {"Authorization": f"Bearer {at}", "content-type": "application/json"}
        # Keycloak 26's declarative user profile requires email + name, and any
        # missing required attribute makes direct-grant login fail with "Account is
        # not fully set up". Signup is username-only, so synthesise a verified email
        # when none is given (login is still by username) and set a name — the
        # account is then complete and immediately usable.
        # A username may contain characters invalid in an email local-part (e.g. a
        # second '@'), so sanitise before synthesising the placeholder address.
        local = re.sub(r"[^a-zA-Z0-9._-]", "", username.split("@")[0].lower()) or "user"
        email = body.email.strip() or f"{local}@awcp.local"
        user_body = {
            "username": username, "enabled": True,
            "requiredActions": [],
            "firstName": username, "lastName": "-",
            "email": email, "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        }
        cr = requests.post(f"{kc}/admin/realms/{realm}/users", headers=H, json=user_body, timeout=8)
        if cr.status_code == 409:
            return JSONResponse({"detail": "That username is already taken."}, status_code=409)
        cr.raise_for_status()
        users = requests.get(f"{kc}/admin/realms/{realm}/users", headers=H,
                             params={"username": username, "exact": "true"}, timeout=8).json()
        uid = users[0]["id"]
        rr = requests.get(f"{kc}/admin/realms/{realm}/roles/{role}", headers=H, timeout=8)
        rr.raise_for_status()
        role_rep = rr.json()
        requests.post(f"{kc}/admin/realms/{realm}/users/{uid}/role-mappings/realm", headers=H,
                      json=[{"id": role_rep["id"], "name": role_rep["name"]}], timeout=8)
        _openfga_grant(username, role)
        log.info("signup: created user %s (role %s)", username, role)
        return {"ok": True, "username": username, "role": role}
    except Exception as e:  # noqa: BLE001
        log.warning("signup failed (%s)", e)
        return JSONResponse({"detail": f"Could not create the account: {e}"}, status_code=502)
