# OPA Agent — hidden, SLM-reasoned tool-call PDP

A background governance service for the AWCP control plane. It is **not** one of the
five worker agents: it answers no user prompts and never appears in `/agents` or the
Radar's agent table. The control plane (radar) calls it once per tool call the worker
agents make, and it:

1. **reasons the risk tier** of the tool call with a **small language model** (a local
   Ollama SLM, e.g. `gemma2:2b`) — `low | medium | high | severe`;
2. **decides allow / block** (block iff the tier is in the block set, default
   `high,severe`) via OPA / Rego when `AWCP_OPA_URL` is set, else a fail-secure
   Python fallback;
3. **records a per-question JSON** of every tool call + its tier + decision, which the
   **Radar UI** renders as a tier bar for each tool call the agents make.

The SLM owns the tier — there is no operator slider. A tool's reasoned tier is cached
(a tool's inherent risk doesn't change call-to-call) and persisted across restarts, so
the model is consulted roughly once per distinct tool.

## Why it lives here

It now ships inside the main repo at `src/awcp/opa_agent/`, but still runs as its **own
background process on its own port** because it is the single cross-process tier
authority shared by the (separate) gateway and MCP processes, and it must stay invisible
to the control plane.

## Run

```bash
bash src/awcp/opa_agent/run.sh         # :8105 by default
```

`scripts/run_everything.sh` starts it automatically and points the radar/gateway at it
via `AWCP_OPA_AGENT_URL`; set `SKIP_OPA=1` to leave it off.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | status + tier vocabulary + SLM info |
| `GET` | `/tiers` | tiers, block set, per-tool SLM tiers, recent calls (the **Radar** feed) |
| `GET` | `/tools` | tiers, block set, per-tool tier map (read-only) |
| `POST` | `/evaluate` | reason a tool call's tier · decide · record JSON |
| `GET` | `/decisions/{task_id}` | the per-question tool-risk JSON |

## Configuration (all env-driven — nothing hardcoded)

| Env | Default | Meaning |
| --- | --- | --- |
| `OPA_PORT` | `8105` | this service's port |
| `OPA_RISK_TIERS` | `low,medium,high,severe` | the tier vocabulary (ascending) |
| `OPA_BLOCK_TIERS` | `high,severe` | tiers that BLOCK the answer |
| `OPA_DEFAULT_TIER` | `low` | tier used when the SLM can't decide |
| `OPA_SLM_ENABLED` | `true` | reason the tier with the SLM |
| `OPA_SLM_BASE` | local Ollama runtime | Ollama-compatible base URL |
| `OPA_SLM_MODEL` | `gemma2:2b` | the small model that reasons the tier |
| `OPA_SLM_TIMEOUT` | `30` | per-classification timeout (s) |
| `OPA_SLM_TEMPERATURE` | `0` | deterministic classification |
| `OPA_SLM_CACHE` | `true` | cache the tier per tool (persisted) |
| `OPA_TOOL_POLICY_PATH` | `<tmp>/awcp-opa-tool-tiers.json` | where the tier cache persists |
| `OPA_RECENT_MAX` | `200` | tool-call ring shown on the Radar |
| `AWCP_OPA_URL` | _(empty)_ | OPA server base (empty ⇒ Python fallback) |
| `AWCP_OPA_TOOLS_PACKAGE` | `awcp/tools` | Rego package under `/v1/data` |
| `AWCP_GATEWAY_URL` | `http://localhost:8000` | gateway (optional Laminar logging) |
| `OPA_LAMINAR_ENABLED` | `false` | log every evaluated tool call to Laminar |
