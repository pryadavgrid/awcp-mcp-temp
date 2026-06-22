# OPA Agent — per-tool-call risk tiering, governance & blocking

A complete write-up of the **OPA agent** feature and every file it touches, across
the three repos:

- `awcp-mcp-temp/`  — control plane backend + dashboard UI (this repo)
- `awcp-agents/`    — the agent bundle (the OPA agent lives here)
- `awcp-user-ui/`   — the end-user chat UI

> Everything is **env-driven — nothing is hardcoded** (ports, tiers, tools, the
> block set, URLs, fail-open). When the OPA agent is not wired (`AWCP_OPA_AGENT_URL`
> unset / `SKIP_OPA=1`) the whole feature is a no-op and the platform behaves
> exactly as before.

---

## 1. What it does

The five worker agents (LangGraph / CrewAI / PydanticAI / arXiv / File Inspector)
call tools through the AWCP **MCP server**. The OPA agent is a **hidden, background
policy decision point** that, for every tool call:

1. Resolves the tool's **risk tier** — `low | medium | high | severe` (a new 4-tier
   vocabulary). The tier is set by an operator on the **control-plane UI** (Agent
   Hooks → *Tool Risk Policy* sliders) and stored in the OPA agent (the single
   cross-process authority).
2. Decides **allow / block** — block iff the tier is in the block set (default
   `high,severe`). The decision runs through **OPA / Rego** when configured, with a
   fail-secure deterministic fallback.
3. Records every call into a **per-question structured JSON**
   (`{tool_name, risk_tier, decision}`).
4. The tool call's **real token usage** is logged to **Laminar** (so every tool
   call shows in the Token Monitor, not just LLM calls).

When a tool is `high`/`severe`, the **answer is blocked in the user UI** with a
severity message — reusing the exact same path as the Agent-Hooks Policy Guard.

It is **NOT a worker agent**: it runs no LLM, answers no prompts, never
self-registers, and is hidden from both the control-plane radar and the user-UI
agent picker.

---

## 2. End-to-end flow

```
 operator ── slider ──▶ gateway /tools/policy ──▶ OPA agent tier store (per tool)
                                                       │
 worker agent ── tool call ─▶ MCP execute_tool ────────┼─▶ meters REAL tokens ─▶ gateway /laminar/record ─▶ Laminar / Token Monitor
        │  (kit sends agent_id + task_id)              │
        └─ emits tool_called event ─▶ radar execution_event handler
                                          │ (AWCP_OPA_AGENT_URL set?)
                                          ├─▶ OPA agent /evaluate ─▶ {risk_tier, decision} + records the per-question JSON
                                          │
                                          └─ decision == block (high/severe)
                                                  └─▶ finish workflow "blocked" ─▶ _apply_block (gateway/user.py)
                                                          └─▶ user UI: "⛔ Blocked — Tool Risk Policy: tool 'web_search' is HIGH risk…"
```

The OPA agent is an **additional** decision layer; the existing radar write-action
gate still runs. Both must allow.

---

## 3. Components & files

### 3.1 The OPA agent — `awcp-agents/opa_agent/` (new)

Mirrors the other agents' folder layout, but is a focused policy service.

| File | Purpose |
|---|---|
| `opa_agent.py` | FastAPI service. Tier store (persisted), OPA/Rego decision + Python fallback, per-task JSON, Laminar logging. |
| `run.sh` | Self-bootstraps a venv on first run; launches in the background. Does **not** export `AGENT_RADAR_URL` (so it never self-registers). |
| `requirements.txt` | `fastapi`, `uvicorn`, `httpx`, `pydantic` (no LLM/framework). |
| `README.md` | Agent-local docs. |
| `about.html` | Small landing page served at `/`. |
| `.gitignore` | venv / pycache / logs. |

**HTTP surface**

| Method | Path | Purpose |
|---|---|---|
| GET  | `/` | landing page |
| GET  | `/health` | status + tier vocabulary |
| GET  | `/tools` | `{tiers, block_tiers, default_tier, policy}` — the slider feed |
| POST | `/tools/{name}/risk` | set one tool's tier (`{"tier":"high"}`; `""`/default clears) |
| POST | `/evaluate` | decide a tool call; records the JSON (+ optional Laminar) |
| GET  | `/decisions/{task_id}` | the structured tool-risk JSON for one question |

### 3.2 OPA / Rego policy — `policies/awcp/` (new)

| File | Purpose |
|---|---|
| `tools.rego` | package `awcp.tools` — `block` iff `input.risk_tier ∈ input.block_tiers`. Input-driven (stateless), mirrors `gate.rego`. |
| `tools_test.rego` | unit tests (`opa test policies/`). |

### 3.3 Control-plane backend — `src/awcp/` (modified)

| File | Change |
|---|---|
| `radar/api.py` | Added `AWCP_OPA_AGENT_URL` config + `_opa_tool_evaluate()` async helper, and the **OPA tier-block** in the `execution_event` handler — for every `tool_called`/`web_search` event it calls the OPA agent, and on `block` finishes the workflow blocked (same path as the policy-guard). Fail-open/closed via `AWCP_OPA_AGENT_FAIL_OPEN`. |
| `laminar/api.py` | New `POST /laminar/record` (+ `RecordRequest`) → `bridge.record_usage()`; estimates input tokens from `text` via `laminar/estimator.py` when not given. This is how tool calls land in the ledger. |
| `gateway/opa_proxy.py` (new) | Gateway proxy → the hidden OPA agent: `GET /tools/policy`, `POST /tools/policy/{name}`, `GET /opa/decisions/{task_id}`. Keeps the OPA agent unexposed while editable from the single gateway port. |
| `gateway/app.py` | Mounts `opa_proxy_router`. |
| `gateway/user.py` | `_classify_block()` recognises OPA blocks → title **"⛔ Blocked — Tool Risk Policy"**. |
| `gateway/agents_fs.py` | `discover()` skips `AWCP_USER_AGENTS_EXCLUDE` (default `opa_agent`) → the OPA agent is hidden from `/user/agents` (the user UI picker). |
| `mcp/server.py` | `_meter_tool_tokens()` + a call in `execute_tool` — logs each tool call's **real input+output token usage** to Laminar (the MCP server is the one place tools run, so it has the real I/O). Env `AWCP_METER_TOOL_TOKENS`. |

### 3.4 Control-plane UI — `ui/` (modified)

| File | Change |
|---|---|
| `src/pages/Hooks.jsx` | New **Tool Risk Policy** panel under Policy Guard: a 4-tier slider per tool (`low → severe`, `high`/`severe` flagged ⛔). Components `ToolRiskPolicy` + `ToolRiskSlider`. |
| `src/api.js` | `getToolPolicy()` / `setToolRisk()` → the gateway proxy. |

### 3.5 Launcher — `scripts/run_everything.sh` (modified)

- New **step 5b**: bootstraps the OPA agent's venv and launches it in the
  background (`SKIP_OPA=1` to skip).
- Exports `AWCP_OPA_AGENT_URL`, `AWCP_OPA_AGENT_FAIL_OPEN`, and **appends
  `opa_agent` to `AGENT_RADAR_EXCLUDE`** so the radar scanner skips it.
- Adds the OPA agent to the startup banner; cleanup kills it on Ctrl+C.

---

## 4. The 4 risk tiers & the block rule

- Tiers (ascending): `low, medium, high, severe` (`OPA_RISK_TIERS`).
- Block set: `high, severe` (`OPA_BLOCK_TIERS`) — these block the answer.
- A tool with no tier set resolves to `OPA_DEFAULT_TIER` (`low`) → allowed.

So `low`/`medium` pass; `high`/`severe` block. All of this is env-configurable.

---

## 5. How blocking reaches the user UI

A blocked tool reuses the **existing** control-plane block path — no new mechanism:

```
OPA agent /evaluate → block
   → radar execution_event handler finishes the Temporal workflow {status: "blocked", error: <reason>}
   → gateway /user/status reads the outcome → _apply_block() suppresses the answer, sets blocked_reason/title
   → user UI ResultPanel renders "⛔ Blocked — Tool Risk Policy: <reason>"
```

The block fires when the agent **reports** the tool call, so it blocks the
*answer*. (Preventing the tool from executing at all is a possible follow-up in
`mcp/server.py:execute_tool`.)

---

## 6. Token logging to Laminar (tool calls)

Tool calls don't consume LLM tokens, so their "token usage" is the size of their
**input + output**. That data only exists where the tool actually runs — the **MCP
server**. So `mcp/server.py:execute_tool` estimates and posts it to the gateway:

```
execute_tool → _meter_tool_tokens(agent_id, task_id, tool, input, output)
   → POST {gateway}/laminar/record {text: input+output}
   → bridge.record_usage() → ledger + emits a laminar.token.usage OTel span
   → Token Monitor row "tool:<name>" under the calling agent
```

- The OPA agent's own logging is **off by default** (`OPA_LAMINAR_ENABLED=false`) so
  tool calls aren't double-counted. Flip it on and set `AWCP_METER_TOOL_TOKENS=false`
  to meter from the OPA agent instead.
- The **external Laminar dashboard** (`:5667`) only fills when
  `LMNR_PROJECT_API_KEY` is set (then the spans export). Without it, tool calls
  still appear in the **local Token Monitor**.

---

## 7. Hidden from the control plane AND the user UI

Two independent surfaces, two env-driven exclusions (no hardcoding):

- **Radar / `/agents`** — the OPA agent never self-registers (no `AGENT_RADAR_URL`),
  and `run_everything.sh` appends `opa_agent` to `AGENT_RADAR_EXCLUDE`, so the
  process scanner's `is_excluded()` skips it.
- **User UI / `/user/agents`** — `agents_fs.discover()` skips
  `AWCP_USER_AGENTS_EXCLUDE` (default `opa_agent`).

---

## 8. Configuration (all env-driven)

**OPA agent** (`awcp-agents/opa_agent`)

| Var | Default | Meaning |
|---|---|---|
| `OPA_PORT` | `8105` | service port |
| `OPA_RISK_TIERS` | `low,medium,high,severe` | tier vocabulary (ascending) |
| `OPA_BLOCK_TIERS` | `high,severe` | tiers that block the answer |
| `OPA_DEFAULT_TIER` | `low` | tier for a tool with none set |
| `OPA_TOOL_TIERS` | `""` | seed map, e.g. `web_search:high,read_file:low` |
| `OPA_TOOL_POLICY_PATH` | `<tmp>/awcp-opa-tool-tiers.json` | persisted slider state |
| `AWCP_OPA_URL` | `""` | OPA server (empty ⇒ Python fallback) |
| `AWCP_OPA_TOOLS_PACKAGE` | `awcp/tools` | Rego package |
| `AWCP_OPA_TIMEOUT` | `2` | per-OPA-request timeout (s) |
| `AWCP_GATEWAY_URL` | `http://localhost:8000` | for Laminar `/laminar/record` |
| `OPA_LAMINAR_ENABLED` | `false` | OPA-side tool-token logging (off ⇒ MCP meters) |

**Control plane** (this repo / launcher)

| Var | Default | Meaning |
|---|---|---|
| `AWCP_OPA_AGENT_URL` | `""` (run script: `http://localhost:8105`) | enables the radar+gateway integration; unset ⇒ feature off |
| `AWCP_OPA_AGENT_TIMEOUT` | `3` | radar/gateway → OPA agent timeout |
| `AWCP_OPA_AGENT_FAIL_OPEN` | `true` | OPA agent unreachable ⇒ allow (`false` ⇒ block) |
| `AWCP_METER_TOOL_TOKENS` | `true` | MCP server meters tool tokens to Laminar |
| `AGENT_RADAR_EXCLUDE` | (run script appends `opa_agent`) | hide from the radar scanner |
| `AWCP_USER_AGENTS_EXCLUDE` | `opa_agent` | hide from the user-UI picker |
| `OPA_AGENT_PORT` | `8105` | port the launcher starts the agent on |
| `SKIP_OPA` | `0` | `1` ⇒ don't start the OPA agent |

---

## 9. Run it

```bash
# Whole platform (auto-starts the hidden OPA agent on :8105):
bash scripts/run_everything.sh

# Or the OPA agent standalone:
bash ../../awcp-agents/opa_agent/run.sh     # stop: pkill -f opa_agent.py
```

Wire the control plane to it by exporting `AWCP_OPA_AGENT_URL=http://localhost:8105`
before starting the gateway (run_everything.sh does this for you).

---

## 10. Verify end-to-end

1. `GET /agents` and `GET /user/agents` — the OPA agent is **not** listed (hidden).
2. `GET http://localhost:8105/health` answers; `GET /tools/policy` (gateway) returns
   the tier vocabulary + policy.
3. Control plane → **Agent Hooks → Tool Risk Policy**: drag `web_search` to `high`.
4. In the user UI, run an agent whose answer needs `web_search` → the answer is
   **blocked** with a severity message; a `read_file`-only task passes.
5. `GET /opa/decisions/{task_id}` returns the per-question JSON of tool calls + tiers.
6. **Token Monitor** shows `tool:<name>` rows with real token counts under the agent.

---

## 11. Behavior when disabled / fail-secure

- `AWCP_OPA_AGENT_URL` unset (or `SKIP_OPA=1`): the radar/gateway never call the OPA
  agent — behaviour is identical to before the feature.
- OPA agent unreachable: `AWCP_OPA_AGENT_FAIL_OPEN=true` (default) allows the tool;
  set `false` to fail closed (treat an unreachable PDP as a block).
- OPA server (`AWCP_OPA_URL`) down/unset: the OPA agent uses its deterministic
  Python fallback (block iff tier ∈ block set).
- Laminar disabled: `/laminar/record` is a no-op; tool execution is unaffected.

---

## 12. Status (what was tested)

- OPA agent: tier-set, block-on-high, allow-on-low, default-low for unknown tools,
  the per-question JSON, and policy persistence — all verified.
- `POST /laminar/record`: records a tool call and estimates real tokens
  (e.g. a `web_search` with a real result → ~460 tokens vs a flat 2 before).
- Gateway proxy routes (`/tools/policy`, `/opa/decisions/{id}`) wired; `/user/agents`
  excludes `opa_agent`; all changed Python compiles and the UI parses.

> Changes require restarting the **gateway + MCP server + OPA agent** to take effect
> (the Vite UIs hot-reload).
