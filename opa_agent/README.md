# OPA Agent — tool-call policy decision point (hidden, background)

A governance service, **not** a worker agent. It runs no LLM and answers no user
prompts. The AWCP control plane calls it once per tool call the five worker agents
make, and it answers:

1. **Risk tier** of the tool — `low | medium | high | severe` (the 4-tier
   vocabulary, env-configurable). The tier is set by an operator on the control
   plane (Agent Hooks → **Tool Risk Policy** sliders) and stored here — this
   service is the single cross-process authority for per-tool tiers.
2. **Allow or block** — block iff the tier is in the block set (default
   `high,severe`). The decision is delegated to **OPA / Rego** when `AWCP_OPA_URL`
   is set (package `awcp/tools`), with a fail-secure deterministic fallback.
3. It records every call into a **per-question structured JSON**
   (`{tool_name, risk_tier, decision}`) and logs the call **+ its tokens to
   Laminar** (via the gateway's `/laminar/record`).

When the OPA agent blocks a high/severe tool, the radar finishes that task
**blocked**, so the **user UI shows the answer blocked** with the severity reason —
reusing the same path as the Policy Guard.

## Hidden from the control plane
It never self-registers (no `AGENT_RADAR_URL`), and `scripts/run_everything.sh`
adds it to `AGENT_RADAR_EXCLUDE` so the process scanner skips it. It does **not**
appear in `/agents` or the radar dashboard.

## Run
```bash
bash run.sh        # first run bootstraps a venv; starts on :8105 (background)
# stop: pkill -f opa_agent.py
```
Wire the control plane to it by exporting `AWCP_OPA_AGENT_URL=http://localhost:8105`
before starting the gateway (run_everything.sh does this automatically).

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | service status + tier vocabulary |
| GET  | `/tools` | tier vocabulary, block set, per-tool tier map (the slider feed) |
| POST | `/tools/{name}/risk` | set one tool's tier (the slider) — body `{ "tier": "high" }` |
| POST | `/evaluate` | decide one tool call; records JSON + logs Laminar |
| GET  | `/decisions/{task_id}` | the structured JSON of all tool calls for a question |

## Config (env — nothing hardcoded)
`OPA_PORT` · `OPA_RISK_TIERS` · `OPA_BLOCK_TIERS` · `OPA_DEFAULT_TIER` ·
`OPA_TOOL_TIERS` (seed map) · `OPA_TOOL_POLICY_PATH` (persisted slider state) ·
`AWCP_OPA_URL` · `AWCP_OPA_TOOLS_PACKAGE` · `AWCP_OPA_TIMEOUT` · `AWCP_GATEWAY_URL` ·
`OPA_LAMINAR_ENABLED`.
