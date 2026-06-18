# MCP-as-the-Governance-Plane

The MCP server in **`/Users/pryadav/Desktop/awcp-mcp-temporal`** is now the
**write-action firewall** for these agents. Tools no longer run inside the agent
process: an agent sends `(tool, args, identity, trace-context)` to the MCP
server, the server runs the **radar write-action gate** *before* the tool, traces
the run, and returns the result. Governance becomes structural — an agent cannot
bypass the gate because the only way to run a governed tool is through the server.

```
agent (MCP client)                 MCP server :8002                 radar :8090
──────────────────                 ────────────────                 ───────────
governed_action(external_post)
  │  high-risk → operator approval
  │  (kept in the agent task UI)
  └─ execute_tool  ──MCP/SSE──▶ resolve risk (dynamic)
        + agent_id                 is_write? → POST /agents/{id}/gate ──▶ allow/deny
        + task_id                  child OTel span (parent = agent trace)
        + trace ctx                run registered tool (web_search /
                                   save_artifact / external_post / …)
   result / "BLOCKED" ◀────────── JSON envelope {status,output,decision,…}
```

## What changed

### Temporal repo (`awcp-mcp-temporal`)
- **`src/awcp/runtime/tool_runtime.py`** — the `@tool` decorator now takes optional
  `risk` / `scope`. `get_tool_risk()` resolves a tool's risk dynamically:
  `AWCP_TOOL_RISK` env override → the tool's declared risk → `AWCP_DEFAULT_TOOL_RISK`
  (default `low`). `is_write_risk()` decides which tiers are gated
  (`AWCP_WRITE_RISK_TIERS`, default `medium,high,critical`). Nothing is keyed to a
  specific tool name.
- **`src/awcp/tools/save_artifact.py`** (risk `medium`) and
  **`src/awcp/tools/external_post.py`** (risk `high`) — new governed write tools,
  auto-discovered by `discover_tools()`.
- **`src/awcp/mcp/server.py`** — `execute_tool` is now the governance chokepoint:
  it resolves risk/scope, calls the radar gate for writes (`_radar_gate`, fail-open
  per `AWCP_GATE_FAIL_OPEN`), opens a child span from the caller's W3C trace
  context, runs the tool, and returns a JSON envelope
  `{status, output, decision, mode, reason, risk}`.
- **`src/awcp/temporal/activities/mcp_gateway.py`** — internal Temporal callers
  unwrap that envelope (`_unwrap_execute_tool`) so the existing orchestrator path
  is unchanged.

### Agents repo (this folder)
- **`*/awcp_kit.py`** — each agent is now an **MCP client**:
  - `mcp_execute()` opens an SSE session to the server and calls `execute_tool`,
    passing `agent_id`, `task_id`, `risk`, `scope`, and the current trace context.
  - `governed_action()` keeps **high-risk operator approval in the agent UI**, then
    executes through the MCP server (server runs the gate). If the server is
    unreachable it **falls back to the local gate + local execution** — a missing
    control plane never hard-breaks the agent.
  - `web_search`, `save_artifact`, `external_post` route through the server;
    each has a local fallback.
- **`*/requirements.txt`** — add `mcp>=1.0.0`.

## Configuration (all env-driven, nothing hardcoded)

| Var | Default | Where | Meaning |
|-----|---------|-------|---------|
| `AWCP_MCP_ENABLED` | `true` | agents | route tools through the MCP server |
| `AWCP_MCP_URL` | `http://localhost:8002/sse` | agents | MCP server SSE endpoint |
| `AWCP_MCP_TIMEOUT` | `30` | agents | per-call timeout (seconds) |
| `AGENT_RADAR_URL` | `http://localhost:8090` | both | radar base URL |
| `AWCP_GATE_TIMEOUT` | `3` | server | radar gate timeout |
| `AWCP_GATE_FAIL_OPEN` | `true` | server | radar down → allow (`false` = deny) |
| `AWCP_TOOL_RISK` | _(empty)_ | server | per-tool risk override, e.g. `external_post:high,save_artifact:medium` |
| `AWCP_DEFAULT_TOOL_RISK` | `low` | server | risk for tools that declare none |
| `AWCP_WRITE_RISK_TIERS` | `medium,high,critical` | server | which tiers are gated |
| `AWCP_SAVE_ARTIFACT_RISK` | `medium` | server | risk of the save_artifact tool |
| `AWCP_EXTERNAL_POST_RISK` | `high` | server | risk of the external_post tool |
| `AWCP_ARTIFACT_DIR` | `<cwd>/artifacts` | server | server-side artifact store |
| `AGENT_EXTERNAL_WRITE_URL` | `https://httpbin.org/post` | both | external_post destination |

## How to run

1. **Observability (optional but recommended)** — in `awcp-mcp-temporal`:
   `docker compose -f observability/docker-compose.yml up -d`
2. **Radar (the gate) on :8090** — in `awcp-mcp-temporal`:
   `./scripts/run_radar.sh` (or `run_all.sh`). Temporal optional; onboarding/exec
   fall back to inline when Temporal is absent.
3. **MCP governance server on :8002** — in `awcp-mcp-temporal`:
   `./scripts/start_mcp.sh`
4. **Agents** — in this folder, start each (or use `python3 control_panel.py`):
   `./langgraph_agent/run.sh`, `./crewai_agent/run.sh`,
   `./pydanticai_agent/run.sh`, `./arxiv_agent/run.sh`
   (first launch creates each venv and installs `mcp`).

Each agent self-registers with the radar on startup. Submit a goal in an agent's
task console; governed writes (`save_artifact`, `external_post`) now flow through
the MCP server's gate. Check `GET /info` on an agent to see `mcp_enabled` /
`mcp_url`; the server logs `mcp.execute.ok` / `mcp.execute.blocked`.

### Quick check
- With the MCP server **up**: a `save_artifact` lands in the server's
  `AWCP_ARTIFACT_DIR`, and the radar `/events` log shows a `gate` decision.
- Set an agent over its autonomy ladder to `recommendation_only` (radar
  `/agents/{id}/autonomy`) → the next governed write returns `BLOCKED` from the
  server gate.
- Stop the MCP server → the agent transparently falls back to local execution
  (span attribute `govern.via=local`).
