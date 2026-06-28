# OPA Agent — SLM-reasoned per-tool-call risk tiering, governance & blocking

A complete write-up of the **OPA agent** feature and every file it touches:

- `awcp-mcp-temp/`  — control plane backend + dashboard UI **and the OPA agent**
  (it now lives in this repo at `src/awcp/opa_agent/`)
- `awcp-user-ui/`   — the end-user chat UI

> Everything is **env-driven — nothing is hardcoded** (ports, tiers, the model, the
> block set, URLs, fail-open). When the OPA agent is not wired (`AWCP_OPA_AGENT_URL`
> unset / `SKIP_OPA=1`) the whole feature is a no-op and the platform behaves
> exactly as before.

---

## 1. What it does

The five worker agents (LangGraph / CrewAI / PydanticAI / arXiv / File Inspector)
call tools through the AWCP **MCP server**. The OPA agent is a **hidden, background
policy decision point** that, for every tool call:

1. **Reasons the tool's risk tier** — `low | medium | high | severe` — with a
   **small language model** (a local Ollama SLM, e.g. `gemma2:2b`). The SLM is given
   the tool name + input (+ the question) and returns the tier with a one-line
   reason. There is **no operator slider** — the SLM owns the tier. A tool's reasoned
   tier is **cached** (a tool's inherent risk doesn't change call-to-call) and
   persisted across restarts, so the model is consulted ~once per distinct tool.
2. Decides **allow / block** — block iff the tier is in the block set (default
   `high,severe`). The decision runs through **OPA / Rego** when configured, with a
   fail-secure deterministic fallback.
3. Records every call into a **per-question structured JSON** + a recent ring
   (`{agent_id, tool_name, risk_tier, decision, reasoning, ...}`) that the **Radar**
   renders as a tier bar for every tool call.
4. (Optional) logs the call to **Laminar**; off by default since the MCP server
   already meters real tool tokens at the source.

When a tool is `high`/`severe`, the **answer is blocked in the user UI** with a
severity message — reusing the exact same path as the Agent-Hooks Policy Guard.

It is **NOT a worker agent**: it answers no prompts and is hidden from the user-UI
agent picker. (It does run a *small* model, but only to classify tool risk — it never
serves a user answer.) It **does** self-register with the radar so operators can see
it running like the other agents (§8), but stays out of the process scanner.

---

## 2. End-to-end flow

```
 worker agent ── tool call ─▶ MCP execute_tool ──▶ meters REAL tokens ─▶ gateway /laminar/record ─▶ Token Monitor
        │  (kit sends agent_id + task_id)
        └─ emits tool_called event ─▶ radar execution_event handler
                                          │ (AWCP_OPA_AGENT_URL set?)
                                          ├─▶ OPA agent /evaluate
                                          │       └─ SLM reasons tier (cached) ─▶ {risk_tier, decision} + per-question JSON + recent ring
                                          │
                                          └─ decision == block (high/severe)
                                                  └─▶ finish workflow "blocked" ─▶ _apply_block (gateway/user.py)
                                                          └─▶ user UI: "⛔ Blocked — Tool Risk Tier: tool 'web_search' is HIGH risk…"

 Radar UI ── poll /opa/tiers (gateway → OPA agent /tiers) ──▶ tier bar per tool call (low/medium/high/severe)
```

The OPA agent is an **additional** decision layer; the existing radar write-action
gate still runs. Both must allow.

---

## 3. Components & files

### 3.1 The OPA agent — `src/awcp/opa_agent/` (in this repo)

A focused policy service. Runs as its **own background process on its own port**
(it is the single cross-process tier authority shared by the separate gateway + MCP
processes) on the **repo's venv**.

| File | Purpose |
|---|---|
| `opa_agent.py` | FastAPI service. SLM-decided tier cache (persisted), OPA/Rego decision + Python fallback, per-task JSON + recent ring, optional Laminar logging. |
| `slm.py` | The small-model reasoner: one Ollama `/api/chat` round-trip (JSON, deterministic) → `{tier, reason}`, validated against the tier vocabulary, fail-safe to the default tier. |
| `run.sh` | Launches in the background on the repo venv (falls back to a local venv). Does **not** export `AGENT_RADAR_URL` (so it never self-registers). |
| `requirements.txt` | `fastapi`, `uvicorn`, `httpx`, `pydantic` (the model runs in the local runtime — no model SDK here). |
| `README.md` / `about.html` | Agent-local docs / landing page served at `/`. |
| `policies/awcp/` | A local copy of the Rego policy for reference. |

**HTTP surface**

| Method | Path | Purpose |
|---|---|---|
| GET  | `/` | landing page |
| GET  | `/health` | status + tier vocabulary + SLM info |
| GET  | `/tiers` | `{tiers, block_tiers, default_tier, slm, by_tool, recent}` — the **Radar** feed |
| GET  | `/tools` | `{tiers, block_tiers, default_tier, policy}` — read-only per-tool tier map |
| POST | `/evaluate` | reason a tool call's tier (SLM, cached); decide; record JSON + recent ring |
| GET  | `/decisions/{task_id}` | the structured tool-risk JSON for one question |

### 3.2 OPA / Rego policy — `policies/awcp/`

| File | Purpose |
|---|---|
| `tools.rego` | package `awcp.tools` — `block` iff `input.risk_tier ∈ input.block_tiers`. Input-driven (stateless), mirrors `gate.rego`. The tier it receives is the SLM's. |
| `tools_test.rego` | unit tests (`opa test policies/`). |

### 3.3 Control-plane backend — `src/awcp/` (modified)

| File | Change |
|---|---|
| `radar/api.py` | `AWCP_OPA_AGENT_URL` config + `_opa_tool_evaluate()` async helper + the **OPA tier-block** in the `execution_event` handler — for every `tool_called`/`web_search` event it calls the OPA agent, and on `block` finishes the workflow blocked (same path as the policy-guard). Fail-open/closed via `AWCP_OPA_AGENT_FAIL_OPEN`. |
| `gateway/opa_proxy.py` | Gateway proxy → the hidden OPA agent: `GET /opa/tiers`, `GET /opa/decisions/{task_id}`. Keeps the OPA agent unexposed while readable from the single gateway port. |
| `gateway/app.py` | Mounts `opa_proxy_router`. |
| `gateway/user.py` | `_classify_block()` recognises OPA blocks → title **"⛔ Blocked — Tool Risk Tier"**. |
| `gateway/agents_fs.py` | `discover()` skips `AWCP_USER_AGENTS_EXCLUDE` (default `opa_agent`) → hidden from `/user/agents`. |
| `mcp/server.py` | `_meter_tool_tokens()` logs each tool call's **real input+output token usage** to Laminar (the MCP server is the one place tools run). Env `AWCP_METER_TOOL_TOKENS`. |

### 3.4 Control-plane UI — `ui/` (modified)

The tiers show **on the Radar only** — there are no operator controls (the SLM
decides), so the old Agent-Hooks sliders are gone. There are **two** control-plane
UIs and both show the tiers:

| File | Change |
|---|---|
| `src/awcp/radar/static/index.html` | The **primary dashboard** the gateway serves at `http://localhost:8000/`. New **Tool Risk Tiers** tab: `renderTiers()` fetches `/opa/tiers` and draws a segmented `tierBar()` meter (low→severe) for every recent tool call, with the SLM reasoning on hover. Served per-request, so a browser refresh suffices. |
| `ui/src/pages/Radar.jsx` | The React/Vite app (:5173) — same **Tool Risk Tiers** panel (`TierBar`, reads `/opa/tiers`). |
| `ui/src/pages/Hooks.jsx` | **Removed** the Tool Risk Policy sliders (`ToolRiskPolicy`/`ToolRiskSlider`). |
| `ui/src/api.js` | `getToolTiers()` → `/opa/tiers` (replaced `getToolPolicy`/`setToolRisk`). |

### 3.5 Launcher — `scripts/run_everything.sh` (modified)

- **Step 5b** launches the OPA agent from `src/awcp/opa_agent/` on the repo venv
  (`SKIP_OPA=1` to skip).
- Exports `AWCP_OPA_AGENT_URL`, `AWCP_OPA_AGENT_FAIL_OPEN`, a generous
  `AWCP_OPA_AGENT_TIMEOUT` (cold-tool SLM latency), `OPA_SLM_BASE` (the real model
  runtime) + `OPA_SLM_MODEL`, and **appends `opa_agent` to `AGENT_RADAR_EXCLUDE`**.
- Adds the OPA agent (with its tier model) to the startup banner; cleanup kills it.

---

## 4. The 4 risk tiers & the block rule

- Tiers (ascending): `low, medium, high, severe` (`OPA_RISK_TIERS`).
- Block set: `high, severe` (`OPA_BLOCK_TIERS`) — these block the answer.
- If the SLM can't decide (model down / unclear output) the tool resolves to
  `OPA_DEFAULT_TIER` (`low`) → allowed (fail-safe).

So `low`/`medium` pass; `high`/`severe` block. All of this is env-configurable.

---

## 5. How the SLM reasons the tier

`slm.py` sends one deterministic, JSON-formatted Ollama `/api/chat` request:

- **system**: defines the (env-injected) tier vocabulary and what each end of the
  scale means (read-only/local = least severe … destructive/secrets/external =
  most severe), and requires strict JSON `{"tier", "reason"}`.
- **user**: the tool name, the tool input (truncated), and the question being served.

The returned tier is validated against `OPA_RISK_TIERS`; anything invalid falls back
to `OPA_DEFAULT_TIER`. The `{tier, reason}` is cached per tool (`OPA_SLM_CACHE`,
persisted to `OPA_TOOL_POLICY_PATH`) so the first call of a tool pays the model cost
and the rest are instant — which is why `AWCP_OPA_AGENT_TIMEOUT` is generous.

---

## 6. How blocking reaches the user UI

A blocked tool reuses the **existing** control-plane block path — no new mechanism:

```
OPA agent /evaluate → block
   → radar execution_event handler finishes the Temporal workflow {status: "blocked", error: <reason>}
   → gateway /user/status reads the outcome → _apply_block() suppresses the answer, sets blocked_reason/title
   → user UI ResultPanel renders "⛔ Blocked — Tool Risk Tier: <reason>"
```

The block fires when the agent **reports** the tool call, so it blocks the *answer*.

---

## 7. Token logging to Laminar (tool calls)

Unchanged: `mcp/server.py:execute_tool → _meter_tool_tokens(...)` estimates a tool
call's real input+output tokens and posts to `{gateway}/laminar/record`, so each
call shows as a `tool:<name>` row in the Token Monitor. The OPA agent's own logging
stays **off** (`OPA_LAMINAR_ENABLED=false`) to avoid double-counting.

---

## 8. Visible on the radar, hidden from the user UI

- **Radar / `/agents`** — the OPA agent **self-registers and heartbeats** so it shows
  up as a running agent, like the worker agents (`src/awcp/opa_agent/radar_register.py`,
  `RadarPresence`): POST `/agents/register` once → onboards **active**, then POST
  `/agents/{id}/signal {ok:true}` every `OPA_RADAR_HEARTBEAT`s to refresh liveness
  (re-registers if the radar pruned/restarted). It appears as **name "OPA Agent", id
  `agent-opa`, kind `agent_framework`, framework `opa`, active/done**. It is still kept
  out of the process **scanner** (`AGENT_RADAR_EXCLUDE` appends `opa_agent`) so there is
  no duplicate `proc-<pid>` row. Set `OPA_RADAR_REGISTER=false` to make it fully hidden
  again. The agent table is shown by **both** UIs (static `:8000` Inventory + React
  `:5173` Radar) from `GET /agents`.
- **User UI / `/user/agents`** — still hidden: `agents_fs.discover()` skips
  `AWCP_USER_AGENTS_EXCLUDE` (default `opa_agent`). It's infra, not a user-selectable
  worker agent.
- **Token Monitor / `/laminar/usage`** — still hidden: it spends no metered tokens, so
  `laminar/bridge.all_usage()` skips ids in `LMNR_USAGE_EXCLUDE` (default `agent-opa`,
  kept in sync with `OPA_RADAR_AGENT_ID`). The OPA agent shows on the **radar only**.

---

## 9. Configuration (all env-driven)

**OPA agent** (`src/awcp/opa_agent`)

| Var | Default | Meaning |
|---|---|---|
| `OPA_PORT` | `8105` | service port |
| `OPA_RISK_TIERS` | `low,medium,high,severe` | tier vocabulary (ascending) |
| `OPA_BLOCK_TIERS` | `high,severe` | tiers that block the answer |
| `OPA_DEFAULT_TIER` | `low` | tier used when the SLM can't decide |
| `OPA_SLM_ENABLED` | `true` | reason the tier with the SLM |
| `OPA_SLM_BASE` | local Ollama runtime | Ollama-compatible base URL |
| `OPA_SLM_MODEL` | `gemma2:2b` | the small model that reasons the tier |
| `OPA_SLM_TIMEOUT` | `30` | per-classification timeout (s) |
| `OPA_SLM_TEMPERATURE` | `0` | deterministic classification |
| `OPA_SLM_CACHE` | `true` | cache the tier per tool (persisted) |
| `OPA_TOOL_POLICY_PATH` | `<tmp>/awcp-opa-tool-tiers.json` | tier cache file |
| `OPA_RECENT_MAX` | `200` | tool-call ring shown on the Radar |
| `AWCP_OPA_URL` | `""` | OPA server (empty ⇒ Python fallback) |
| `AWCP_OPA_TOOLS_PACKAGE` | `awcp/tools` | Rego package |
| `AWCP_OPA_TIMEOUT` | `2` | per-OPA-request timeout (s) |
| `AWCP_GATEWAY_URL` | `http://localhost:8000` | for optional Laminar `/laminar/record` |
| `OPA_LAMINAR_ENABLED` | `false` | OPA-side tool-token logging (off ⇒ MCP meters) |
| `OPA_RADAR_REGISTER` | `true` | self-register on the radar so it shows as a running agent (`false` ⇒ hidden) |
| `OPA_RADAR_URL` | `AWCP_GATEWAY_URL` | radar/gateway to register + heartbeat against |
| `OPA_RADAR_AGENT_ID` / `OPA_RADAR_NAME` | `agent-opa` / `OPA Agent` | its radar identity |
| `OPA_RADAR_FRAMEWORK` | `opa` | framework label in the radar table |
| `OPA_RADAR_HEARTBEAT` | `30` | liveness heartbeat interval (s) |

**Control plane** (this repo / launcher)

| Var | Default | Meaning |
|---|---|---|
| `AWCP_OPA_AGENT_URL` | `""` (run script: `http://localhost:8105`) | enables the radar+gateway integration; unset ⇒ feature off |
| `AWCP_OPA_AGENT_TIMEOUT` | `30` | radar/gateway → OPA agent timeout (covers cold-tool SLM latency) |
| `AWCP_OPA_AGENT_FAIL_OPEN` | `true` | OPA agent unreachable ⇒ allow (`false` ⇒ block) |
| `AWCP_METER_TOOL_TOKENS` | `true` | MCP server meters tool tokens to Laminar |
| `AGENT_RADAR_EXCLUDE` | (run script appends `opa_agent`) | hide from the radar scanner |
| `AWCP_USER_AGENTS_EXCLUDE` | `opa_agent` | hide from the user-UI picker |
| `OPA_AGENT_PORT` | `8105` | port the launcher starts the agent on |
| `SKIP_OPA` | `0` | `1` ⇒ don't start the OPA agent |

---

## 10. Run it

```bash
# Whole platform (auto-starts the hidden OPA agent on :8105 with its tier model):
bash scripts/run_everything.sh

# Or the OPA agent standalone:
bash src/awcp/opa_agent/run.sh     # stop: pkill -f opa_agent.py
```

The OPA agent needs the local model runtime (Ollama) reachable with the tier model
pulled — e.g. `ollama pull gemma2:2b` (or set `OPA_SLM_MODEL` to one you have).

---

## 11. Verify end-to-end

1. `GET /agents` and `GET /user/agents` — the OPA agent is **not** listed (hidden).
2. `GET http://localhost:8105/health` answers and shows the SLM model;
   `GET /opa/tiers` (gateway) returns the tier vocabulary + recent tool calls.
3. In the user UI, ask a question that drives several tool calls.
4. Control plane → **Radar → Tool Risk Tiers**: every tool call shows a tier bar
   (low/medium/high/severe) with the SLM's reasoning on hover; `high`/`severe` calls
   are flagged blocked, and the answer is blocked in the user UI with a severity msg.
5. `GET /opa/decisions/{task_id}` returns the per-question JSON of tool calls + tiers.
6. **Token Monitor** still shows `tool:<name>` rows with real token counts.

---

## 12. Behavior when disabled / fail-secure

- `AWCP_OPA_AGENT_URL` unset (or `SKIP_OPA=1`): the radar/gateway never call the OPA
  agent — behaviour is identical to before the feature.
- OPA agent unreachable: `AWCP_OPA_AGENT_FAIL_OPEN=true` (default) allows the tool;
  set `false` to fail closed.
- SLM unreachable / `OPA_SLM_ENABLED=false`: the tier falls back to `OPA_DEFAULT_TIER`
  (`low` ⇒ allowed) — a model hiccup never crashes a task.
- OPA server (`AWCP_OPA_URL`) down/unset: the OPA agent uses its deterministic Python
  fallback (block iff tier ∈ block set).

> Changes require restarting the **gateway + MCP server + OPA agent** to take effect
> (the Vite UIs hot-reload).
