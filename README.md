# AWCP — Agent Workforce Control Plane

A governed multi-agent platform. Agents route prompts to LLMs and call tools, while
**Temporal** orchestrates each step durably, an **MCP server** (FastMCP) executes the
governed work, an **OpenSandbox** container isolates the file/command tools, and a React
**dashboard** lets you watch and approve everything live.

This repo is the **control plane**. The agent runtimes live in a separate bundle (see
[Agents](#agents)) and report into the gateway.

---

## Prerequisites

- **Python ≥ 3.10**
- **uv / uvx** — runs the OpenSandbox runtime: `brew install uv`
- **Docker Desktop** — running (telemetry stack, Postgres, and the OpenSandbox backend)
- **Ollama** with the models:
  ```bash
  ollama pull llama3.1:8b
  ollama pull gemma2:2b
  ```
- **Temporal CLI** (optional) — `brew install temporal` (the gateway onboards inline without it)

---

## ▶ Run everything (one command)

```bash
cd awcp-mcp-temporal
bash scripts/run_everything.sh
```

That's the **single launcher**. It brings the whole stack up (gateway in the foreground,
everything else in the background) and **Ctrl+C stops all of it**. On the first run it also
creates the venv, installs `requirements.txt`, generates the sandbox config, and pulls the
container images — so the first start is slower.

It starts, in order:

1. **venv + dependencies** (first run only)
2. **Docker telemetry stack** — OTel Collector → Tempo / Prometheus / Loki / Grafana, plus the canonical **Postgres** (starts Docker itself if it isn't running)
3. **Temporal** dev server (engine `:7233`, UI `:8233`)
4. **Ollama** (`:11434`)
5. **OpenSandbox runtime** (`:8090`) — the isolated backend for the sandbox file/command tools
6. **MCP control server** (`:8002`, SSE)
7. **OPA** policy engine + the hidden OPA tool-tier agent
8. **React dashboard** (`:5173`)
9. **AWCP Gateway** (`:8000`, foreground) — mounts the registry/radar, token monitor, `/user` API, and the in-process Temporal workers

**Toggles:** `SKIP_TELEMETRY=1`, `SKIP_SANDBOX=1`, `SKIP_MCP=1`, `SKIP_OPA=1`, `SKIP_OLLAMA=1`,
`SKIP_UI=1`, `SKIP_INSTALL=1`, `DEMO=1` (seed a demo agent).

### Stopping

`Ctrl+C` in the launcher terminal stops everything it started, or:

```bash
bash scripts/stop_everything.sh
```

---

## Configuration (`.env`) — the only thing to change per machine

Everything auto-detects from the repo, so a fresh machine usually needs **no config**. To
move the sandbox workspace or change a port, drop a `.env` in the repo root. The launcher
reads it, creates the workspace folder, and writes the OpenSandbox config (`~/.sandbox.toml`)
automatically — no manual editing.

```bash
# .env  — all optional
AWCP_WORKSPACE_DIR=/abs/path/to/awcp-mcp-temporal/workspace   # default: <repo>/workspace
AWCP_SANDBOX_PORT=8090                                        # default: 8090 (:8080 is often Adminer)
AWCP_AGENTS_DIR=/abs/path/to/awcp-agents                      # the external agent bundle
GATEWAY_PORT=8000
UI_PORT=5173
GROQ_API_KEY=...                                             # optional, enables advanced_web_search
```

> Spaces/quotes around values are tolerated (`KEY = "value"` works), but `KEY=value` is cleanest.

---

## Open

| Surface | URL |
|---|---|
| **Dashboard** (give prompts, watch, approve) | http://localhost:5173 |
| **Gateway / API** | http://localhost:8000  · docs at `/docs` |
| **Token monitor** | http://localhost:8000/laminar/ui |
| **Temporal UI** (workflows) | http://localhost:8233 |
| **Grafana** (traces/metrics/logs) | http://localhost:3000  *(admin / awcp1234)* |
| **Prometheus** | http://localhost:9090 |
| **MCP server** (SSE) | http://localhost:8002 |
| **OpenSandbox runtime** | http://localhost:8090 |

The dashboard has: **Dashboard · Radar · Approvals · Workflow · Context Graph · Token Monitor ·
Agent Hooks · Operator Policy · Sandbox**. A pending **write** approval shows a live count badge
on *Approvals* and a notification toast.

---

## Agents

The agent runtimes are a **separate bundle** pointed to by `AWCP_AGENTS_DIR`. Start them from
that folder (each `run.sh` self-bootstraps its own venv on first run):

```bash
python3 "$AWCP_AGENTS_DIR/control_panel.py"   # → http://localhost:8099  start/stop each agent
# …or start one directly, e.g.:
bash "$AWCP_AGENTS_DIR/langgraph_agent/run.sh"
```

In the dashboard pick an agent, type a goal, and watch the governed step timeline. Governed
**writes** (e.g. `write_file`, `run_command`, `save_artifact`) pause for approval on the
**Approvals** page.

---

## How it fits together

```
Dashboard ─▶ Gateway (:8000) ─▶ Agent ─▶ MCP server (:8002) ─▶ run governed tool
                │  registry · radar gate · token monitor · /user API
                ▼
           Temporal (durable orchestration)        OpenSandbox (:8090)
                                                    └─ isolated container for read_file /
                                                       write_file / run_command (only the
                                                       workspace/ folder is mounted in)
```

- **Radar gate** governs every tool call (risk tier, autonomy, token budget, OPA policy).
- **Sandbox** runs the file/command tools inside a container that can only see `workspace/` —
  anything outside it is inaccessible.
- **Context Graph** records each governed step as a tamper-chained node (evidence ledger), with
  an optional Neo4j projection.

---

## Folder structure

```text
awcp-mcp-temporal/
├── scripts/
│   ├── run_everything.sh      # ONE launcher — starts the whole stack (above)
│   ├── stop_everything.sh     # stop everything the launcher started
│   └── clean_cache.sh
├── src/awcp/
│   ├── gateway/               # AWCP Gateway (:8000) — mounts radar + token monitor + /user API + /llm proxy
│   ├── radar/                 # Agent Radar: registry, write-action gate, OPA, scanner, Temporal workflows, context-graph API
│   ├── mcp/server.py          # FastMCP server (:8002) — workspace tools + governed execute_tool
│   ├── runtime/               # tool runtime, Ollama client, schemas/config, sandbox.py (OpenSandbox bridge)
│   ├── tools/                 # runtime tools: web_search, advanced_web_search, arxiv, compute, save_artifact, external_post, sandbox_tools
│   ├── context_graph/         # tamper-chained governed-step trail (evidence ledger) + Neo4j projection
│   ├── agent_hooks/           # policy-guard hooks
│   ├── opa_agent/             # hidden SLM tool-tier PDP
│   ├── laminar/               # token monitor (Laminar)
│   ├── observability/         # OpenTelemetry setup
│   ├── agents/                # built-in agent specs (Ollama search, etc.)
│   └── registry/              # agent registry store
├── ui/                        # React dashboard (Vite, :5173)
├── observability/             # docker-compose telemetry stack + Postgres + init-db
├── policies/                  # OPA Rego policies (gate.rego, tools.rego)
├── workspace/                 # host dir bind-mounted into the sandbox container
├── docs/                      # implementation notes
├── tests/
├── requirements.txt
└── README.md
```

---

## Policy engine (OPA) — optional

The write-action gate can evaluate through **OPA** (`policies/awcp/*.rego`); `src/awcp/radar/policy.py`
is the fail-secure fallback. OPA runs as a sidecar in the observability stack (`:8181`) and is
started by the launcher. With it unreachable the gate behaves exactly as `policy.py` alone — so
nothing breaks if OPA is off.
