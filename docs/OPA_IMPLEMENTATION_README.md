# OPA Implementation README

This document explains what to implement in each folder to add Open Policy Agent
(OPA) support to AWCP. The goal is to replace the current hardcoded write-action
decision path with policy-as-code, while keeping the existing Python policy as a
safe local fallback.

## What OPA Will Do

OPA will become the main decision engine for governed agent actions.

It will answer three questions:

1. Can this agent perform this action?
2. Does this action require human approval?
3. If an approval token is provided, is it valid for this exact workflow step?

The result should still look like the current gate response:

```json
{
  "decision": "allow",
  "reason": "approved by OPA policy",
  "mode": "active",
  "stage": {},
  "policy_id": "awcp.governance.allow"
}
```

For high-risk actions, OPA should return:

```json
{
  "decision": "deny",
  "requires_approval": true,
  "reason": "high-risk write requires approval token",
  "approval_scope": "workspace.write"
}
```

## Runtime Flow

```text
Agent or workflow wants to perform an action
        |
        v
POST /agents/{agent_id}/gate
        |
        v
Radar loads the agent registry entry
        |
        v
AWCP builds OPA input
        |
        v
OPA evaluates Rego policy
        |
        +--------------------+----------------------+
        |                    |                      |
        v                    v                      v
      allow                deny              requires approval
        |                    |                      |
        v                    v                      v
Action executes       Action blocked      Temporal/user flow pauses
                                                   |
                                                   v
                                      Operator issues approval token
                                                   |
                                                   v
                                      Agent retries with token
```

## Folder-by-Folder Implementation Plan

## `observability/`

This folder owns local infrastructure. Add OPA here because it runs beside
Prometheus, Grafana, Temporal, Loki, Tempo, and Laminar.

### Add

```text
observability/opa/
├── policies/
│   └── awcp.rego
└── data/
    └── awcp.json
```

### Update

```text
observability/docker-compose.yml
```

Add an `opa` service:

```yaml
opa:
  image: openpolicyagent/opa:latest
  container_name: awcp-opa
  restart: unless-stopped
  command:
    - "run"
    - "--server"
    - "--addr=0.0.0.0:8181"
    - "/policies"
  volumes:
    - ./opa/policies:/policies:ro
    - ./opa/data:/data:ro
  ports:
    - "8181:8181"
  networks:
    - awcp-obs
```

### What it does

OPA receives policy queries from the Radar API and returns allow, deny, or
approval-required decisions.

## `observability/opa/policies/`

This folder contains Rego policy files.

### Add

```text
observability/opa/policies/awcp.rego
```

### What it should implement

Rules for:

- read-only actions are allowed
- quarantined agents cannot perform writes
- writes outside declared `write_scopes` are denied
- `recommendation_only` and `suspended` agents cannot write
- high-risk writes require approval
- dangerous workspace tools require approval
- valid approval tokens allow only their exact scope/action/workflow step

Example policy shape:

```rego
package awcp.governance

default allow := false
default requires_approval := false

allow if {
  not input.action.write
}

allow if {
  input.action.write
  input.agent.status == "active"
  input.agent.autonomy_profile == "active"
  input.action.scope in input.agent.write_scopes
  not high_risk_action
}

requires_approval if {
  high_risk_action
  not input.approval.token_valid
}

high_risk_action if {
  input.agent.risk == "high"
  input.action.write
}

high_risk_action if {
  input.action.tool_name == "run_command"
}

high_risk_action if {
  input.action.tool_name == "write_file"
}
```

## `src/awcp/radar/`

This is the main governance boundary today. The existing gate lives here, so OPA
should be integrated here first.

### Existing important files

```text
src/awcp/radar/api.py
src/awcp/radar/policy.py
src/awcp/radar/models.py
src/awcp/radar/store.py
```

### Add

```text
src/awcp/radar/opa.py
src/awcp/radar/policy_engine.py
src/awcp/radar/approval.py
```

### `opa.py`

Responsible for calling the OPA HTTP API.

It should:

- read `AWCP_OPA_URL`
- send input to `/v1/data/awcp/governance`
- enforce a short timeout
- return a normalized decision dict
- fail open or fail closed based on env config

Suggested env vars:

```text
AWCP_OPA_URL=http://localhost:8181
AWCP_OPA_TIMEOUT=1.0
AWCP_OPA_FAIL_MODE=closed
```

### `policy_engine.py`

Responsible for selecting which policy engine is active.

Supported modes:

```text
AWCP_POLICY_ENGINE=local
AWCP_POLICY_ENGINE=opa
AWCP_POLICY_ENGINE=shadow
```

Mode behavior:

- `local`: use existing `policy.evaluate_action`
- `opa`: enforce OPA result
- `shadow`: call both local and OPA, enforce local, log differences

This lets you roll out OPA safely without breaking existing agent flows.

### `approval.py`

Responsible for issuing and validating approval tokens.

Tokens should include:

- `agent_id`
- `workflow_id`
- `task_id`
- `action`
- `scope`
- `expires_at`
- `nonce`

Tokens should be:

- signed with HMAC
- short lived
- single use
- valid only for the exact action/scope/workflow step

Suggested env vars:

```text
AWCP_APPROVAL_SECRET=local-dev-secret
AWCP_APPROVAL_TTL_SECONDS=900
```

### Update `api.py`

Current gate:

```python
decision = policy.evaluate_action(e, action=req.action, is_write=req.write, scope=req.scope)
```

Target gate:

```python
decision = policy_engine.evaluate(e, req)
```

Also extend `GateRequest` with:

```python
workflow_id: str = ""
task_id: str = ""
actor: str = "agent"
tool_name: str = ""
approval_token: str = ""
```

### What it does

Radar becomes the policy-control point. Every governed write goes through this
folder before it runs.

## `src/awcp/temporal/`

Temporal owns durable workflow orchestration. It should pause or continue based
on OPA decisions.

### Existing important files

```text
src/awcp/temporal/workflows/agent_execution.py
src/awcp/temporal/workflows/dynamic_ask.py
src/awcp/temporal/activities/mcp_gateway.py
src/awcp/temporal/worker/run_worker.py
```

### Add or update

Add a policy gate activity in:

```text
src/awcp/temporal/activities/mcp_gateway.py
```

Suggested activity:

```python
@activity.defn(name="policy_gate")
async def policy_gate(payload: dict) -> dict:
    ...
```

Then register it in:

```text
src/awcp/temporal/worker/run_worker.py
```

### Update workflow behavior

In `agent_execution.py`, the intended flow is:

```text
admission -> route -> policy gate -> tool -> generate
```

The current workflow routes and then executes tools. Insert a policy gate before
`mcp_execute_tool`.

For `allow`:

```text
continue to tool execution
```

For `deny`:

```text
return blocked result
```

For `requires_approval`:

```text
pause the workflow or return awaiting_approval
```

### What it does

Temporal makes approval durable. A high-risk action can wait without losing the
workflow state.

## `src/awcp/mcp/`

The MCP server exposes tools that can change workspace state. These must be
covered by policy.

### Existing important file

```text
src/awcp/mcp/server.py
```

### Update

Classify MCP tools by risk and scope:

```text
read_file     -> workspace.read
write_file    -> workspace.write
run_command   -> workspace.exec
execute_tool  -> derived from tool metadata
```

Before executing dangerous MCP tools, call the Radar gate.

### What it does

This prevents powerful tools like `write_file` and `run_command` from bypassing
the OPA gate.

## `src/awcp/runtime/`

Runtime tool execution is another enforcement point.

### Existing important file

```text
src/awcp/runtime/tool_runtime.py
```

### Update

Add optional metadata for tools:

```python
TOOL_SCOPES = {
    "web_search": {"scope": "web.read", "write": False},
    "advanced_web_search": {"scope": "web.read", "write": False},
    "arxiv_search": {"scope": "research.read", "write": False},
}
```

If future tools mutate state, give them write scopes and require policy checks.

### What it does

The runtime can tell OPA what kind of tool is being requested instead of sending
only a tool name.

## `src/awcp/gateway/`

The gateway is the user-facing API. It should surface approval state cleanly.

### Existing important files

```text
src/awcp/gateway/app.py
src/awcp/gateway/user.py
```

### Update

When a task is blocked by OPA or waiting for approval, return:

```json
{
  "status": "awaiting_approval",
  "awaiting": {
    "action": "write_file",
    "scope": "workspace.write",
    "reason": "high-risk write requires approval token"
  }
}
```

### What it does

The frontend and API caller can tell the difference between failure, denial, and
approval-required pauses.

## `src/awcp/laminar/`

Laminar handles token budgets and degradation signals. OPA should not replace
this. Instead, OPA should consume the current autonomy and budget state.

### Existing important files

```text
src/awcp/laminar/api.py
src/awcp/laminar/bridge.py
src/awcp/laminar/budget.py
src/awcp/laminar/ledger.py
```

### Update

Expose token state to the OPA input when available:

```json
{
  "token_state": "ok",
  "current_tokens": 450,
  "budget_tokens": 500
}
```

### What it does

OPA can make decisions using token-budget facts, while Laminar remains the source
of token usage data.

## `ui/`

The React UI should show OPA decisions and approval-required states.

### Existing important files

```text
ui/src/App.jsx
ui/src/api.js
ui/src/components/Timeline.jsx
ui/src/components/ResultPanel.jsx
ui/src/styles.css
```

### Update

Show:

- policy engine mode: `local`, `opa`, or `shadow`
- gate decision: `allow`, `deny`, `requires approval`
- policy reason
- approval action button when a task is waiting

### What it does

Operators can see why a write was blocked and approve the exact action when
appropriate.

## `tests/`

Tests should prove OPA does not weaken the current gate.

### Add

```text
tests/radar/
├── test_policy_engine.py
├── test_opa_input.py
├── test_approval_tokens.py
└── test_gate_api.py
```

### Test cases

- read-only action is allowed
- quarantined write is denied
- undeclared write scope is denied
- active agent with declared scope is allowed
- `recommendation_only` write is denied
- `suspended` action is denied
- high-risk write requires approval
- valid approval token allows exact action
- expired approval token is denied
- wrong-scope approval token is denied
- reused token is denied
- OPA unavailable follows `AWCP_OPA_FAIL_MODE`
- shadow mode logs mismatches but keeps local enforcement

## `docs/`

This folder should explain the design and rollout.

### Add or update

```text
docs/OPA_IMPLEMENTATION_README.md
docs/AWCP_Architecture_Flow.html
docs/AWCP_Magazine_vs_temp2.html
```

### What it does

Docs should make clear that OPA closes the current Step 03 gap from the magazine:

```text
position-based gate -> OPA policy-as-code + approval tokens
```

## Recommended Implementation Order

1. Add OPA docker-compose service.
2. Add Rego policy that mirrors the current Python gate.
3. Add `opa.py` client.
4. Add `policy_engine.py` with `local`, `opa`, and `shadow` modes.
5. Route `/agents/{agent_id}/gate` through `policy_engine.evaluate`.
6. Add approval-token support.
7. Add Temporal policy gate activity.
8. Gate MCP sharp tools like `write_file` and `run_command`.
9. Add UI approval state.
10. Add tests.

## Safe Rollout Strategy

Start with:

```text
AWCP_POLICY_ENGINE=shadow
```

This means:

- Python policy still enforces decisions.
- OPA runs in parallel.
- Differences are logged.
- Once OPA matches expected behavior, switch to:

```text
AWCP_POLICY_ENGINE=opa
```

## Final Target

After implementation, AWCP should have this control model:

```text
Registry owns identity and declared scopes
Radar owns policy gate and OPA integration
OPA owns policy-as-code decisions
Temporal owns durable wait/resume approval flow
Laminar owns token budget signals
MCP/runtime tools obey gate decisions before execution
UI shows policy outcomes to operators
```

