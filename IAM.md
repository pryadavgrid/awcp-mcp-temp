# IAM Implementation Plan for AWCP

**Keycloak (authentication) + OpenFGA (authorization) + the existing Postgres**

> This is a step‑by‑step action plan wired to the actual AWCP repo. Every file path,
> endpoint, env var, and table named below exists today (or is a net‑new addition
> called out as such). Nothing here assumes features the repo does not have.

---

## 0. Ground truth — how AWCP is wired today (read this first)

Before adding IAM, here is what already exists, so we protect the real surface and
don't rebuild things:

| Concern | Reality in this repo |
| --- | --- |
| API process | **One** FastAPI app: `src/awcp/gateway/app.py` → `awcp.gateway.app:app`, served on **:8000** (`uvicorn awcp.gateway.app:app`). |
| Routers mounted | `user_router` (`/user/*`), `opa_proxy_router` (`/opa/*`), `radar_router` (root: `/agents`, `/approvals`, `/policy`, `/tasks/*`, `/events`, `/healthz`). See `app.include_router(...)` in `gateway/app.py`. |
| Current auth | **None.** Only `CORSMiddleware(allow_origins=*)` in `gateway/app.py`. No JWT, no API key, no login. IAM is greenfield. |
| Frontend(s) | (a) The **Vite/React SPA** in `ui/` on **:5173** — the dashboard I've been building; it has the login gate `ui/src/main.jsx` → `ui/src/pages/Login.jsx` (currently **visual‑only**). (b) A legacy static dashboard served by the gateway at `GET /` (`radar/static/index.html`). IAM targets the Vite SPA. |
| UI → API | The SPA has **one** fetch chokepoint: `call()` in `ui/src/api.js`; base URL from `ui/src/config.js` `API_BASE` (`VITE_API_BASE`, default `http://localhost:8000`). This is where a `Authorization: Bearer` header goes. |
| Database | Postgres, `postgres:16` container `awcp-postgres` in `observability/docker-compose.yml` (user/pass/db default `awcp`/`awcppassword`/`awcp`). App reads `AGENT_RADAR_DATABASE_URL` (+ `AGENT_RADAR_DB_ADMIN_URL` for DDL) in `src/awcp/radar/db.py`. Schema bootstrapped by `observability/init-db/01-roles.sql` + `02-schema.sql`; schemas: `registry`, `governance`, `evidence`, `ops`. |
| Audit already present | `evidence.ledger` (hash‑chained) records governed writes (gate denials, approvals, scope changes); exposed via `GET /events/audit`. IAM **extends** this, it does not replace it. |
| Launcher | `scripts/run_everything.sh` starts the docker stack, gateway (:8000), and the Vite UI (:5173) with `VITE_API_BASE` pointed at the gateway. New IAM services get added here. |

### Corrections applied to the earlier draft (which was written with partial repo knowledge)

- **Removed "Patch Management" (Create/Approve/Apply/Reject Patch).** There are **no patch endpoints** in this repo; instrumentation‑patch generation is an explicitly *unbuilt* roadmap item. The real "approval" surface is **write‑approvals** and **token/agent approvals** (endpoints listed in §4).
- **Added the missing agent‑vs‑human distinction.** Many endpoints (`/agents/register`, `/agents/{id}/gate`, `/tasks/execution/*`) are **called by the bundle agents** (`awcp-agents/*/awcp_kit.py`), not by a human in a browser. Those need **service identity**, not interactive Keycloak login (§5). The old draft would have locked the agents out.
- **Re‑anchored audit** to the existing `evidence.ledger` / `GET /events/audit`, plus a net‑new `iam.audit` table created with the repo's own `ensure_*()` pattern (§7) — not an invented standalone schema.
- **Mapped roles to real endpoints** (§3/§4) instead of generic verbs.
- **Frontend steps point at the real files** (`main.jsx`, `Login.jsx`, `api.js`, `config.js`).

Out of scope for this phase (unchanged from the draft): Google / external IdPs.

---

## 1. Architecture

```text
Browser (ui/ SPA :5173)
  │  1. redirect to Keycloak, log in (Authorization Code + PKCE)
  ▼
Keycloak (realm: AWCP)  ── issues ──►  JWT access token
  │  2. SPA stores token, attaches it to every call() in ui/src/api.js
  ▼
AWCP Gateway (:8000, gateway/app.py)
  │  3. auth dependency validates the JWT (Keycloak JWKS)
  ▼
OpenFGA  ── check(user, action, resource) ──►  allow / deny
  │  4. deny → 403 (+ audit); allow → run the route
  ▼
Radar / user / opa routes  →  Postgres, Temporal, agents
```

Two identity planes (keep them separate):

- **Human plane** — operators/admins in the browser → Keycloak user tokens → OpenFGA. Governs the read + operator + admin endpoints.
- **Agent/service plane** — the bundle agents (`awcp_kit`) calling registration/gate/execution endpoints → Keycloak **service accounts (client‑credentials)** or a shared radar service token. Governs the agent‑facing endpoints (§5). No OpenFGA role check needed here beyond "is a valid AWCP agent service".

---

## 2. Design principles (kept from the draft, still valid)

1. **Authentication ≠ authorization.** Keycloak answers *who*; OpenFGA answers *what they may do*. Never gate on Keycloak realm roles alone.
2. **OpenFGA is the single source of truth for permissions.** Do not duplicate permissions in Postgres or the SPA.
3. **Secure by default.** Every mutating endpoint requires an explicit allow; if the check can't be made, deny.

---

## 3. Roles → real actions

Start with five roles. Each maps to concrete endpoints (full list in §4).

| Role | May do (real endpoints) |
| --- | --- |
| **Super Admin** | Everything, incl. user/role management (in Keycloak + OpenFGA), `PUT /policy`, `DELETE /agents/{id}`. |
| **Platform Admin** | `PUT /policy`, `POST /opa/threshold`, `DELETE /agents/{id}`, plus all Operator actions. |
| **Operator** | Approvals: `POST /approvals/{id}/decide`, `POST /agents/{id}/approve`, `POST /agents/{id}/tokens/{tid}/approve|deny`. Agent control: `POST /agents/{id}/signal` (suspend/resume), `/autonomy`, `/risk`. Run tasks: `POST /user/submit`, `/user/ask`, `/user/upload`, `/user/approve/*`, `/user/stop/*`. |
| **Auditor** | All `GET` reads incl. `GET /events/audit`, `GET /approvals`, `GET /policy`. No writes. |
| **Viewer** | Read subset: `GET /agents`, `/user/workflows`, `/events`, dashboards. No operator actions. |

---

## 4. The real endpoint inventory (what to protect, and how)

Grouped from `src/awcp/radar/api.py`, `src/awcp/gateway/user.py`, `src/awcp/gateway/opa_proxy.py`.

**Public (no auth):** `GET /healthz`.

**Human — read (Viewer+ / Auditor):**
`GET /agents`, `/agents/{id}`, `/agents/{id}/card`, `/agents/{id}/brief`, `/agents/{id}/tokens`,
`GET /approvals`, `/approvals/{id}`, `GET /policy`, `GET /events`, `GET /events/audit`,
`GET /sandbox/events`, `GET /user/agents`, `/user/workflows`, `/user/status/{agent}/{task_id}`,
`GET /opa/tiers`, `/opa/decisions/{task_id}`.

**Human — operator writes (Operator+):**
`POST /approvals`, `/approvals/{id}/decide`, `/agents/{id}/approve`,
`/agents/{id}/tokens/{tid}/approve`, `/agents/{id}/tokens/{tid}/deny`,
`/agents/{id}/signal`, `/agents/{id}/autonomy`, `/agents/{id}/risk`,
`POST /user/submit`, `/user/ask`, `/user/upload`, `/user/approve/{agent}/{task_id}`,
`/user/stop/{agent}/{task_id}`, `POST /opa/threshold`.

**Human — admin writes (Platform/Super Admin):**
`PUT /policy`, `DELETE /agents/{id}`.

**Agent / service plane (NOT interactive login — see §5):**
`POST /agents/register`, `/agents/announce`, `/agents/register-card`, `/agents/{id}/card/refresh`,
`/agents/{id}/gate`, `/tasks/execution/start`, `/tasks/execution/{task_id}/event`,
`/tasks/execution/{task_id}/complete`.

**Served UI:** `GET /` (legacy static dashboard) — protect or leave public per deployment; the Vite SPA is the IAM‑gated one.

Map each `action` string (e.g. `decide_approval`, `set_policy`, `suspend_agent`, `run_task`,
`delete_agent`) to an OpenFGA relation on a resource type in §4‑model below.

---

## 5. Agent / service identity (the plane the draft missed)

The bundle agents self‑register and stream execution events via `awcp_kit.py`
(`_radar_register()`, `_radar_call()`, `_radar_gate()`). Under IAM these calls must
still succeed without a human login.

Options (pick one):
- **Keycloak client‑credentials service account** per agent kind (`awcp-agent-langgraph`, …) or one shared `awcp-agent` client. `awcp_kit` fetches a token from Keycloak's token endpoint and sends it as `Authorization: Bearer`.
- **Shared radar service token** (simpler): a static secret in env, checked by the gateway auth dependency for the agent‑plane routes only.

Action items:
- Add the token/secret env to `awcp_kit.py` and set it in `scripts/run_everything.sh` (where the agents are spawned) and the gateway env.
- The auth dependency (§6) routes agent‑plane paths to service‑token verification, human paths to Keycloak+OpenFGA.

---

## 6. Phase — Backend enforcement (gateway)

1. **New module** `src/awcp/gateway/auth.py`:
   - `verify_user(token)` — validate the Keycloak JWT against the realm JWKS (issuer, audience `awcp-api`, expiry, signature). Return the identity (`sub`, `preferred_username`, email).
   - `verify_service(request)` — validate the agent/service token (§5).
   - `require(action, resource)` — a FastAPI dependency that: (a) resolves identity, (b) calls OpenFGA `check(user, action, resource)`, (c) 401 if unauthenticated / 403 if denied, (d) records an `iam.audit` row (§7).
   - An `openfga_check()` helper (OpenFGA HTTP API or SDK) reading `OPENFGA_API_URL` + `OPENFGA_STORE_ID` from env.
2. **Apply it** by adding `dependencies=[Depends(require("...", ...))]` to each route in §4 (or a small wrapper per router group). Keep `GET /healthz` public.
3. **Config**: read `KEYCLOAK_URL`, `KEYCLOAK_REALM=AWCP`, `KEYCLOAK_AUDIENCE=awcp-api`, `OPENFGA_API_URL`, `OPENFGA_STORE_ID`, `AWCP_AGENT_SERVICE_TOKEN` from env (add to `gateway/app.py` startup + `run_everything.sh`).
4. **CORS**: tighten `allow_origins` in `gateway/app.py` from `*` to the SPA origin(s) once tokens are required.

### OpenFGA authorization model (Phase 4 of the draft, re‑anchored)

Resource types = the real ones: `agent`, `approval`, `policy`, `task` (workflow run),
`opa_threshold`, `system`. Relations encode the role→action mapping, e.g.:

```text
type policy
  relations
    define can_read:  [role#viewer, role#auditor, role#operator, role#admin]
    define can_write: [role#platform_admin, role#super_admin]   # PUT /policy

type agent
  relations
    define can_control: [role#operator, role#admin]   # signal/autonomy/risk
    define can_delete:  [role#platform_admin, role#super_admin]  # DELETE /agents/{id}

type approval
  relations
    define can_decide:  [role#operator, role#admin]   # /approvals/{id}/decide
```

Seed the store with the five roles and assign users to roles via OpenFGA tuples.

---

## 7. Phase — Audit (extend, don't reinvent)

- **Keep** using `evidence.ledger` for governed control‑plane writes (already hash‑chained; `GET /events/audit` surfaces it).
- **Add** an IAM audit table for auth/login/authorization decisions, following the repo's own pattern:
  - New `iam` schema + table in `observability/init-db/02-schema.sql`:
    ```sql
    CREATE SCHEMA IF NOT EXISTS iam;
    CREATE TABLE iam.audit (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      ts timestamptz NOT NULL DEFAULT now(),
      user_id text, username text,
      action text, resource text,
      result text,               -- 'allow' | 'deny' | 'login' | 'logout' | 'refresh_fail'
      ip_address text
    );
    ```
  - Add `ensure_iam_audit_table()` + `record_iam_audit(...)` in `src/awcp/radar/db.py`, mirroring `ensure_write_approvals_table()` / `create_write_approval()` (same `_engine`/`_enabled` guards, best‑effort, in‑memory fallback when the DB is down).
  - Call `record_iam_audit(...)` from the `require(...)` dependency (§6) and on login/logout/refresh‑fail (via the SPA or a small `/auth/event` endpoint).

---

## 8. Phase — Frontend integration (Vite SPA)

1. **Add** `keycloak-js` to `ui/`. New `ui/src/lib/auth.js`: init Keycloak (Authorization Code + PKCE) with `VITE_KEYCLOAK_URL`, `VITE_KEYCLOAK_REALM=AWCP`, `VITE_KEYCLOAK_CLIENT=awcp-ui`; expose `login()`, `logout()`, `getToken()`, `updateToken()`.
2. **Replace the visual login gate**: in `ui/src/main.jsx` (currently `Landing → Login → App`, all visual), gate the `login → app` transition on a real Keycloak session. `ui/src/pages/Login.jsx`'s "Establish Connection" calls `login()` (redirect) instead of `onLogin()` directly; on return with a valid token, advance to `<App/>`. Keep Landing as the public marketing page.
3. **Attach the token** in the single chokepoint `call()` in `ui/src/api.js`:
   `headers.Authorization = 'Bearer ' + (await getToken())`. Add these headers on every request; on `401`, try `updateToken()` then retry once; on refresh failure, clear tokens and return to the login gate (see §9).
4. **Config**: add the `VITE_KEYCLOAK_*` vars to `ui/src/config.js` and set them in `scripts/run_everything.sh` alongside `VITE_API_BASE`.
5. **Role‑aware UI (optional)**: hide operator/admin controls the user's role can't use (read the roles from the token) — but the backend (§6) remains the real enforcement.

---

## 9. Phase — Sessions & tokens

- Keycloak realm token lifetimes: access token **~15 min**, refresh/SSO session **8–24 h** (set in the AWCP realm).
- Silent refresh via `keycloak-js` `updateToken()` before expiry / on a `401`.
- **On refresh failure**: (1) drop the in‑memory access + refresh tokens, (2) do **not** clear browser cache/localStorage wholesale — only the auth tokens, (3) send the user back to the login gate. (This is the "clear tokens, not cache" correction from the original draft — still correct.)

---

## 10. Phase — Infrastructure (add to the existing stack)

Add two services to `observability/docker-compose.yml` (which already runs `awcp-postgres`):

1. **Keycloak** (`quay.io/keycloak/keycloak`): reuse `awcp-postgres` with a dedicated `keycloak` database; expose e.g. `:8080`; import the `AWCP` realm.
2. **OpenFGA** (`openfga/openfga`): Postgres datastore (a dedicated `openfga` database on `awcp-postgres`, or its own volume); expose the HTTP API (e.g. `:8081`).
3. Create the two extra databases via an init script alongside `observability/init-db/`.
4. Wire the new env into `scripts/run_everything.sh` (start order: Postgres → Keycloak → OpenFGA → gateway → UI) and add health checks.

**Deployment/validation order:** Postgres → Keycloak (realm reachable) → OpenFGA (`/stores` reachable + model loaded) → gateway boots with auth deps → SPA login round‑trips.

---

## 11. Phase — Testing & validation (mapped to real endpoints)

- **Authentication**: login redirect + callback; token attached to `call()`; `GET /agents` succeeds with a valid token and returns `401` without one; refresh before expiry; logout.
- **Authorization** (OpenFGA): Viewer gets `403` on `POST /approvals/{id}/decide`; Operator succeeds; only Platform/Super Admin can `PUT /policy` and `DELETE /agents/{id}`; Auditor can `GET /events/audit` but not any write.
- **Agent plane**: a bundle agent (`awcp_kit`) can still `POST /agents/register`, `/agents/{id}/gate`, and `/tasks/execution/*` using its service token, and is rejected without it.
- **Security**: expired token → `401`; tampered JWT → `401`; wrong audience/issuer → `401`; every allow/deny lands in `iam.audit`.

---

## 12. Implementation order (checklist)

1. Stand up Keycloak + OpenFGA in `observability/docker-compose.yml`; create `keycloak`/`openfga` DBs (§10).
2. Create the `AWCP` realm, `awcp-ui` (public, PKCE) + `awcp-api` (bearer‑only) clients, and the agent service account/token (§2, §5).
3. Load the OpenFGA model + role tuples (§6 model).
4. Add `src/awcp/gateway/auth.py` (`verify_user`, `verify_service`, `require`, `openfga_check`) and its env (§6).
5. Apply `Depends(require(...))` to the endpoints in §4; keep `/healthz` public; tighten CORS.
6. Add `iam` schema + `ensure_iam_audit_table()`/`record_iam_audit()`; wire into `require(...)` (§7).
7. Give `awcp_kit.py` its service token; set it in `run_everything.sh` (§5).
8. Wire `keycloak-js` into `ui/` — `main.jsx` gate, `Login.jsx` `login()`, `api.js` Bearer + refresh, `config.js` env (§8, §9).
9. Test per §11.

---

## Expected outcome

- Centralized authentication via **Keycloak** and authorization via **OpenFGA**, enforced in **one** place (`gateway/auth.py`) across the real endpoints.
- A clean split between the **human plane** (operators/admins) and the **agent/service plane** (the bundle agents), so IAM doesn't break the running control loop.
- IAM audit in `iam.audit` alongside the existing hash‑chained `evidence.ledger`.
- The `ui/` SPA login gate becomes a real Keycloak session; Landing stays public.
- No changes to features the repo doesn't have (no "patch management"), and no duplicated permission stores.
