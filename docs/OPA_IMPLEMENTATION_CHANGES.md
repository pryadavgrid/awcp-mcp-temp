# OPA Implementation — Changes & Magazine Mapping

End-to-end implementation of the OPA README on top of AWCP: policy-as-code (OPA +
Rego) becomes the write-action decision engine, with the magazine's narrow,
expiring **approval tokens** and a **durable Temporal approval wait**. The existing
Python gate is kept as a safe fallback, and the rollout defaults to **shadow** so
nothing changes enforcement until OPA parity is confirmed.

This closes the magazine's **Step 03** gap:

```
position-based gate  ->  OPA policy-as-code + approval tokens
```

## Design principles honoured

- **Nothing hardcoded.** Every knob is env-driven with a sane default: engine mode,
  OPA URL/package/timeout/fail-mode, approval secret/TTL, approval wait. The
  dangerous-tool list and high-risk tiers live in `observability/opa/data/awcp.json`
  (data, not code) so policy changes need no redeploy.
- **Single decision point.** The radar gate `POST /agents/{id}/gate` is the one
  place MCP firewall + both Temporal trees consult — routing OPA through it once
  means every enforcement path inherits it.
- **Gate vs degradation split (magazine Step 03 vs Step 04).** The stateful
  autonomy-ladder facts stay in Python (`policy.py`); the engine computes them and
  passes them to OPA, which makes the declarative allow / deny / requires-approval
  call. The ladder is untouched.
- **Safe rollout.** Default `AWCP_POLICY_ENGINE=shadow`: OPA runs alongside the
  Python gate and logs every mismatch (`/policy/status` → `shadow_mismatches`)
  without changing what is enforced.

## Files added

| File | Purpose |
|------|---------|
| `observability/opa/policies/awcp.rego` | Rego mirror of the Python gate + `requires_approval` (Step 03) |
| `observability/opa/data/awcp.json` | Policy data: `dangerous_tools`, `high_risk_tiers` (editable, no deploy) |
| `src/awcp/radar/opa.py` | OPA HTTP client; env-driven; fail-open/closed |
| `src/awcp/radar/approval.py` | HMAC approval tokens — signed, expiring, single-use, scope-bound |
| `src/awcp/radar/policy_engine.py` | Engine selector `local` / `opa` / `shadow`; builds OPA input; validates tokens |
| `tests/radar/test_policy_engine.py` | local/opa/shadow behaviour + token unblock/replay |
| `tests/radar/test_opa_input.py` | ladder→OPA input mapping |
| `tests/radar/test_approval_tokens.py` | signature/expiry/replay/binding |
| `tests/radar/test_gate_api.py` | full gate decision matrix (README's required cases) |
| `docs/OPA_IMPLEMENTATION_CHANGES.md` | this document |

## Files changed

| File | Change |
|------|--------|
| `observability/docker-compose.yml` | added the `opa` service (host `:8181`, policies+data mounted read-only) |
| `src/awcp/radar/api.py` | gate routes through `policy_engine.evaluate`; `GateRequest` extended (tool_name, workflow_id, task_id, actor, approval_token); new `POST /agents/{id}/approval` (issue token) + `GET /policy/status`; `/healthz` reports policy status |
| `src/awcp/temporal/activities/mcp_gateway.py` | new `policy_gate` activity (calls radar gate over HTTP, fail-open per env) |
| `src/awcp/temporal/workflows/agent_execution.py` | inserts the policy gate before tool execution; `submit_approval` signal + **durable wait** on requires_approval |
| `src/awcp/temporal/worker/run_worker.py` | registers the `policy_gate` activity |
| `src/awcp/mcp/server.py` | `_radar_gate` forwards the new fields; `execute_tool` returns `awaiting_approval`; `write_file`/`run_command` gated via `_guard_sharp_tool` |
| `ui/src/components/Sidebar.jsx` | shows the active policy engine mode (local/opa/shadow) |
| `scripts/run_everything.sh` | exports `AWCP_POLICY_ENGINE` (shadow) + `AWCP_OPA_URL`; banner line for the policy engine |

## Magazine step → what was implemented

| Magazine | Status | Where |
|----------|--------|-------|
| **01 Register + quarantine** | already present; OPA mirrors quarantine + declared-scope checks in Rego | `awcp.rego`, registry |
| **02 Orchestrate workflows** | already present; policy gate inserted into the loop | `agent_execution.py` |
| **03 Gate write actions (OPA + approval tokens)** | **implemented end to end** | `opa.py`, `approval.py`, `policy_engine.py`, `awcp.rego`, radar gate, MCP firewall, Temporal gate |
| **04 Degrade gracefully** | unchanged ladder in Python; facts fed to OPA (gate/degradation split) | `policy.py` → `policy_engine.build_opa_input` |
| **05 Replay & evidence** | gate decisions recorded as radar events + Temporal history; full evidence ledger remains a separate follow-up | `_record_event`, Temporal |
| **06 Instrumentation patches** | unchanged | `onboarding.py` |

## Approval-token flow (Step 03, end to end)

1. Agent attempts a high-risk write → gate returns `requires_approval` (OPA mode).
2. MCP `execute_tool` returns `status: "awaiting_approval"`; the Temporal workflow
   pauses **durably** on the `submit_approval` signal (bounded by
   `AWCP_APPROVAL_WAIT_SECONDS`).
3. Operator issues a token: `POST /agents/{id}/approval` with `{action, scope,
   workflow_id, task_id}` → signed, single-use, expiring token.
4. Agent retries the same action carrying `approval_token`; the gate validates it
   (exact agent/action/scope/workflow/task match), **burns** it, and allows that
   one write. Replays are denied.

## Configuration (all env, all defaulted)

| Env | Default | Meaning |
|-----|---------|---------|
| `AWCP_POLICY_ENGINE` | `shadow` | `local` \| `opa` \| `shadow` |
| `AWCP_OPA_URL` | `http://localhost:8181` | OPA server base URL |
| `AWCP_OPA_PACKAGE` | `awcp/governance/decision` | Rego rule path |
| `AWCP_OPA_TIMEOUT` | `1.0` | per-query timeout (s) |
| `AWCP_OPA_FAIL_MODE` | `open` | `open` (allow) \| `closed` (deny) when OPA is down |
| `AWCP_APPROVAL_SECRET` | dev secret | HMAC signing secret (**set in prod**) |
| `AWCP_APPROVAL_TTL_SECONDS` | `900` | token lifetime |
| `AWCP_APPROVAL_WAIT_SECONDS` | `900` | workflow durable-pause window |

Policy data (`observability/opa/data/awcp.json`): `dangerous_tools`,
`high_risk_tiers`.

## Rollout

1. Start the stack — the `opa` service comes up with the telemetry compose.
2. Run in **shadow** (default). Watch `GET /policy/status` → `shadow_mismatches`.
3. When mismatches stay at 0, set `AWCP_POLICY_ENGINE=opa` to enforce OPA.
4. For production, set `AWCP_APPROVAL_SECRET` and decide `AWCP_OPA_FAIL_MODE`.

## Validation performed

- 36 unit tests pass (`tests/radar`).
- Rego validated **live**: OPA container loads `awcp.rego`; decisions correct
  (read→allow, high-risk write→requires_approval, quarantine/scope/ladder→deny).
- Python client → live OPA round-trip: requires_approval → token issue → allow →
  replay denied → quarantine denied.
- `docker compose config` valid; all run scripts pass `bash -n`.
