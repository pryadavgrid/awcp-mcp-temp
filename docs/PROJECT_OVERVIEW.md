# AWCP — Agent Workforce Control Plane

**Project Overview, Tech Stack & End-to-End Walkthrough**

> AWCP is a **governed multi-agent platform**: a control plane that discovers, onboards,
> governs, meters, and audits a fleet of autonomous AI agents. Agents route prompts to
> LLMs and call tools — but every tool call passes through a policy gate, every step is
> recorded on a tamper-evident ledger, risky writes pause for human approval, token
> spend is budgeted and enforced, and everything is observable live in a React dashboard.

This repository is the **control plane**. The agent runtimes themselves (LangGraph,
CrewAI, PydanticAI, arXiv, etc.) live in a **separate bundle** (pointed to by
`AWCP_AGENTS_DIR`) and report into the gateway.

---

## Table of Contents

1. [What the Project Does](#1-what-the-project-does)
2. [Tech Stack](#2-tech-stack)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Component Deep-Dive](#4-component-deep-dive)
   - [AWCP Gateway](#41-awcp-gateway-8000)
   - [Agent Radar](#42-agent-radar-registry--governance)
   - [Write-Action Gate & Autonomy Ladder](#43-write-action-gate--autonomy-ladder)
   - [OPA Policy Engine](#44-opa-policy-engine-rego)
   - [The Hidden OPA Agent (SLM PDP)](#45-the-hidden-opa-agent-slm-tool-tier-pdp)
   - [Approval Tokens](#46-approval-tokens)
   - [Operator Policy](#47-operator-policy)
   - [Temporal Orchestration](#48-temporal-orchestration)
   - [MCP Server & Tools](#49-mcp-server--governed-tools-8002)
   - [OpenSandbox Isolation](#410-opensandbox-isolation-8090)
   - [Context Graph (Evidence Ledger)](#411-context-graph--tamper-evident-evidence-ledger)
   - [Laminar Token Monitor](#412-laminar--token-monitoring--budget-control)
   - [Token-Aware LLM Gateway](#413-token-aware-llm-gateway-llm)
   - [Agent Hooks](#414-agent-hooks-lifecycle-callbacks--live-policy-guard)
   - [A2A AgentCards](#415-a2a-agentcards)
   - [IAM — Keycloak + OpenFGA](#416-iam--keycloak-authn--openfga-authz)
   - [Observability Stack](#417-observability-stack)
   - [React Dashboard](#418-react-dashboard-5173)
   - [Built-in Agents & External Agent Bundle](#419-built-in-agents--the-external-agent-bundle)
   - [Chat Store & Context Memory](#420-chat-store--per-session-context-memory)
   - [Security Hardening](#421-security-hardening)
5. [How It Works — Step by Step](#5-how-it-works--step-by-step)
6. [Ports & Surfaces](#6-ports--surfaces)
7. [Repository Layout](#7-repository-layout)
8. [Design Philosophy](#8-design-philosophy)
9. [Running & Testing](#9-running--testing)

---

## 1. What the Project Does

Modern AI agents can call tools, write files, run commands, and spend money on LLM
tokens — largely unsupervised. AWCP closes that gap. It is the **management and
governance layer for an "agent workforce"**:

| Capability | What AWCP provides |
|---|---|
| **Discovery** | A background *radar scanner* auto-detects running agent frameworks, MCP servers, LLM runtimes, and orchestrators on the host; agents can also self-register. |
| **Onboarding** | Every new agent goes through a durable Temporal workflow: map identity → quarantine check → link MCP → admit. Uninstrumented agents stay *quarantined*. |
| **Governance** | Every tool call is gated: risk tier + autonomy profile + token budget + OPA policy + operator overrides + live hook guards decide allow / deny / hold-for-approval. |
| **Human-in-the-loop** | High-risk writes pause and appear on an **Approvals** page; an operator approves or denies with an expiring, branch-scoped token. |
| **Token economics** | Per-agent sliding-window token budgets. Overspend triggers the degradation ladder; a budget-gated LLM reverse proxy can physically refuse the model call. |
| **Graceful degradation** | Failures/breaches step an agent down a ladder: `active → trace_boost → throttled → safe_profile → recommendation_only → suspended`. |
| **Evidence** | Every governed step lands on a **hash-chained, append-only ledger** in Postgres (tamper-evident), mirrored to Neo4j for graph queries and to Letta for cross-run memory. |
| **Agent memory** | Agents can *offload* context into the graph and *recall* it later — a spill-over memory for long tasks with small context windows. |
| **Isolation** | File/command tools run inside an OpenSandbox container that can only see the `workspace/` folder. |
| **Identity** | Keycloak (authentication) + OpenFGA (authorization) protect the human control surface; agents use service identity. |
| **Observability** | Full OpenTelemetry pipeline → Tempo (traces), Prometheus (metrics), Loki (logs), Grafana (dashboards), plus self-hosted Laminar for LLM-native observability. |

---

## 2. Tech Stack

### Backend (Python ≥ 3.10)

| Technology | Role |
|---|---|
| **FastAPI + Starlette + Uvicorn** | The gateway app, radar API, laminar API, context-graph API, hooks API — all one ASGI process on `:8000`. |
| **Pydantic v2** | All request/response and registry models. |
| **FastMCP (`mcp`)** | The MCP control server on `:8002` (SSE) — exposes the governed tools to agents. |
| **Temporal (`temporalio`)** | Durable orchestration: per-agent onboarding workflows and per-task execution workflows (in-process workers). |
| **OpenSandbox (`opensandbox`)** | Container isolation for `read_file` / `write_file` / `run_command`. |
| **SQLAlchemy + psycopg** | Canonical Postgres control-plane DB (`registry` / `governance` / `evidence` / `ops` schemas). |
| **Open Policy Agent (OPA / Rego)** | Externalized write-gate policy (`policies/awcp/*.rego`), with a fail-secure Python fallback. |
| **httpx / requests / aiohttp** | HTTP clients (Ollama, agents, OPA, Keycloak, Letta). |
| **psutil** | Radar process discovery. |
| **OpenTelemetry SDK** | Traces / metrics / logs → OTel Collector. |
| **lmnr (Laminar SDK)** | LLM-native observability export (optional). |
| **neo4j driver** | Context-graph projection (optional, fail-open). |
| **tiktoken** | Token estimation for budget pre-checks and context budgeting (falls back to chars÷4). |
| **PyJWT (`jwt`)** | Keycloak JWT validation in the IAM middleware. |
| **ddgs, arxiv, openai, Pillow** | Tool/agent backends (DuckDuckGo search, arXiv, Groq/NVIDIA-compatible clients, vision). |

### Models / LLM Runtimes

| Technology | Role |
|---|---|
| **Ollama** (`:11434`) | Local model runtime — `llama3.1:8b` (workers), `gemma2:2b` (SLM tier reasoner). |
| **Groq** (optional) | "Compound" agentic web search inside `advanced_web_search`. |
| **NVIDIA DeepSeek** (optional) | The `deepseek_chat` agent (OpenAI-compatible endpoint). |

### Frontend

| Technology | Role |
|---|---|
| **React 18 + Vite 5** | The dashboard SPA (`ui/`, `:5173`). |
| **Tailwind CSS 3** | Styling. |
| **@xyflow/react** | The Context Graph / flow visualizations. |
| **keycloak-js** | Real login (Authorization Code + PKCE) against the AWCP realm. |

### Infrastructure (docker-compose, `observability/`)

| Service | Role |
|---|---|
| **Postgres 16** (`awcp-postgres`, `:5432`) | Canonical DB: `registry`, `governance`, `evidence`, `ops` schemas (+ `keycloak`, `openfga` DBs). |
| **Keycloak** (`:8083`) | Authentication — the `AWCP` realm, `awcp-ui` (PKCE) + `awcp-api` clients + agent service accounts. |
| **OpenFGA** (`:8081`) | Fine-grained authorization — role→action tuples checked by the gateway. |
| **OPA** (`:8181`) | Rego policy sidecar for the write gate. |
| **Neo4j** (`:7474` / `:7687`) | Graph projection of the evidence ledger. |
| **OTel Collector** (`:4317` gRPC / `:4318` HTTP) | Receives OTLP, fans out to Tempo/Loki/Prometheus. |
| **Tempo / Prometheus / Loki** | Traces / metrics / logs storage. |
| **Grafana** (`:3000`) | Dashboards (provisioned AWCP overview). |
| **Temporal + Temporal UI** | Workflow engine (`:7233`) and UI (`:8233` dev / `:8080` compose). |
| **Laminar stack** (`lmnr-*`, UI `:5667`) | Self-hosted LLM observability (ClickHouse, Quickwit, query engine, app server, frontend). |

---

## 3. High-Level Architecture

```
                       ┌──────────────────────────── humans ────────────────────────────┐
                       │  React Dashboard (:5173)  ·  Grafana (:3000)  ·  Temporal UI    │
                       └───────────────┬─────────────────────────────────────────────────┘
                                       │ Keycloak login (JWT) + OpenFGA authz
                                       ▼
        ┌──────────────────────  AWCP Gateway (:8000)  ──────────────────────┐
        │  /user/*  human entry point (ask/submit/status/approve/upload)     │
        │  radar router (root): /agents /approvals /policy /events /gate ... │
        │  /laminar/*  token monitor  ·  /llm/*  budget-gated LLM proxy      │
        │  /context-graph/*  evidence trail  ·  /hooks/*  agent hooks        │
        │  /opa/*  tool-tier proxy  ·  in-process Temporal workers           │
        └──────┬──────────────────┬───────────────────┬──────────────────────┘
               │ launches/proxies │ gate + events     │ durable state
               ▼                  ▼                   ▼
     External agent bundle   MCP server (:8002)   Postgres · Temporal · OPA (:8181)
     (LangGraph / CrewAI /   governed execute_tool     │
      PydanticAI / arXiv…)        │                    ├─ evidence.ledger (hash chain)
      each agent = its own        ▼                    ├─ governance.* (tokens, decisions)
      FastAPI task worker    OpenSandbox (:8090)       └─ registry.agents
               │             container — only          
               ▼             workspace/ mounted    Neo4j (graph view) · Letta (memory)
        Ollama (:11434)                            OTel → Tempo/Prometheus/Loki/Grafana
        (or via the :8000/llm                      Laminar (:5667) LLM observability
         budget-gated proxy)
```

**One port to rule them all:** the gateway mounts the radar, laminar, context graph,
hooks, OPA proxy, and user API on `:8000`, so the UI and the agents talk to a single
surface.

---

## 4. Component Deep-Dive

### 4.1 AWCP Gateway (`:8000`)

`src/awcp/gateway/` — one FastAPI app (`gateway/app.py`) composing everything:

- **`user.py`** — the human entry point, fully dynamic over the external agent bundle:
  - `GET /user/agents` — list bundle agents + live state (folder scan, live process/port inspection — never an assumed port)
  - `POST /user/ask` — run an agent, block, return the result
  - `POST /user/submit` — fire-and-poll variant (returns task ids immediately)
  - `GET /user/status/{agent}/{task_id}` — live task state + dynamic step timeline (read back from the Temporal workflow history)
  - `POST /user/approve/{agent}/{task_id}` — approve/deny a paused high-risk write
  - `POST /user/upload`, `POST /user/stop/...`, `GET /user/workflows`
- **`agents_fs.py`** — discovery + lifecycle of the external agents: every sub-folder of `AWCP_AGENTS_DIR` containing a `run.sh` is one agent. Drop in a folder → it appears; nothing hardcoded. Launch wiring injects `AGENT_RADAR_URL` + OTel env so a started agent reports into this gateway. Process lookups run under a hard timeout so a wedged OS process table can't freeze discovery.
- **`auth.py`** — the IAM middleware (see §4.16).
- **`signup.py`** — self-service account creation (Keycloak user + OpenFGA role tuple, default `viewer`).
- **`opa_proxy.py`** — `/opa/*` routes proxying the hidden OPA agent's tool-risk tiers so the UI only ever talks to `:8000`.
- **`chat_store.py`** — durable per-chat history (see §4.20).

The gateway also starts the radar's **Temporal workers in-process** (onboarding +
execution task queues) during its lifespan.

### 4.2 Agent Radar (registry + governance)

`src/awcp/radar/` — the heart of the control plane, mounted at the gateway root.

- **Scanner** (`scanner.py`, `detectors/`) — a background loop (every `AGENT_RADAR_SCAN_INTERVAL`, default 30 s) that detects running **agent frameworks, MCP servers, LLM runtimes, and orchestrators** via psutil + port probes. The psutil sweep runs in a **subprocess with a hard timeout** so a stuck OS syscall can never freeze the gateway.
- **Registry** (`store.py`, `models.py`, `registry/`) — the in-memory + Postgres (`registry.agents`) record of every agent: kind, framework, risk tier, autonomy profile, write scopes, failure budget, tokens, A2A card, skills.
- **Self-registration** — agents `POST /agents/register` (or `/agents/announce`, `/agents/register-card`); the OPA agent registers itself as visible infra too.
- **Onboarding** (`onboarding.py`) — per-agent pipeline: *map identity → quarantine check → link MCP → admit*, run as a Temporal workflow when a server is reachable, else inline with identical logic. Identity mapping consults the **magazine** (`awcp_magazine.json`) for the assigned governance profile; risk resolves to the **more restrictive** of declared vs. assigned (a self-declared "low" can't escape an assigned "high"). Magazine unreadable ⇒ fail closed to `high`.
- **Quarantine** — detected-but-uninstrumented agents stay `quarantined`: visible, but blocked from writes until they have telemetry + policy hooks.
- **Execution telemetry intake** — agents push per-step events (`llm_called`, `tool_called`, `web_search`, `synthesize`) to `POST /tasks/execution/{id}/event`; the radar converts each into its own Temporal activity and feeds laminar + hooks.
- **Controls** — `POST /agents/{id}/signal` (suspend/resume + task outcomes feeding the failure budget), `/autonomy`, `/risk`, `DELETE /agents/{id}`, plus agent **self-deregistration**.

### 4.3 Write-Action Gate & Autonomy Ladder

`radar/policy.py` — the governance primitives:

- **The gate** — `POST /agents/{id}/gate {action, scope, write}`: an external agent (via the MCP server) asks *before* doing anything. The answer combines quarantine status, autonomy profile, declared write scopes, risk tier, token budget headroom, OPA decision, operator policy, and hook-guard vetoes.
- **The ladder** — env-tunable graceful-degradation stages (defaults):

  ```
  active → trace_boost → throttled → safe_profile → recommendation_only → suspended
  ```

  Early rungs are still write-capable but tighten operations (more trace sampling, tighter retries/concurrency, safer model profile); from `recommendation_only` onward writes are blocked; `suspended` is a hard stop.
- **Failure budget** — N failures (default 3, per-agent overridable) steps the agent down a rung. Token-budget exhaustion (laminar) drives the **same** ladder — one governance mechanism, two inputs.
- The decision vocabulary is 4-valued: `auto_authorized | awaiting_token | awaiting_operator | denied`, recorded in `governance.policy_decisions`.

### 4.4 OPA Policy Engine (Rego)

`radar/opa.py` + `policies/awcp/{gate,tools}.rego` (+ tests):

- When `AWCP_OPA_URL` is set the gate decision is delegated to OPA (`data.awcp.gate`); Python `policy.py` remains the source of the decision **facts** and the **fail-secure fallback** — an OPA error/timeout never fails open.
- `AWCP_OPA_SHADOW=true` runs OPA in shadow mode: the Python result is enforced and disagreements are logged, so the Rego can be proven faithful before it takes over.
- `AWCP_OPA_TOKEN_RISK_TIERS=high` turns on the approval-token requirement for high-risk writes; `AWCP_OPA_OPERATOR_ACTION_CLASSES` forces operator approval for named action classes.

### 4.5 The Hidden OPA Agent (SLM tool-tier PDP)

`src/awcp/opa_agent/` — a standalone, deliberately hidden service:

- A **small language model** (`gemma2:2b` via Ollama) *reasons* about each tool call and assigns a **risk tier** plus a one-line reason (`slm.py`). Constrained JSON output; any failure falls back to a default tier — the PDP never crashes a task.
- Tiers and decisions persist in Postgres (`governance.tool_tiers`, `governance.tool_call_evaluations`) so restarts don't lose history (`db.py`).
- It self-registers on the radar so operators can *see* it (`radar_register.py`) but is excluded from the user-facing agent picker.
- The UI reads its tiers through the gateway's `/opa/*` proxy; a threshold (`POST /opa/threshold`) controls which tiers block.

### 4.6 Approval Tokens

`radar/tokens.py` + `governance.approval_tokens`:

- When the PDP returns `awaiting_token` / `awaiting_operator`, the gate issues a **pending, expiring, branch-scoped token** for that one action class, and holds the write.
- The operator approves/denies on the dashboard (`POST /agents/{id}/tokens/{tid}/approve|deny`). The token authorizes **only** the approved action class, for one branch, within an expiry window; everything else stays blocked, logged, replayable.
- **Fail-secure**: governance DB unreachable ⇒ no token can be issued or verified ⇒ the write is denied (never an unauditable grant).

### 4.7 Operator Policy

`radar/operator_policy.py` — the Radar **Policy** tab:

- One operator-authored JSON document declaring which detected **agents** are recognised (and at what risk) and which **tools** are allowed (and at what tier), plus defaults.
- Stored **append-only + versioned** in `governance.operator_policy`; the active policy is the latest row.
- Sits *after* the SLM baseline as an operator **override/allowlist**; the human may relabel risk up **or** down (unlike self-declaring agents, which may only tighten). Inert by default — no policy row ⇒ no opinion ⇒ behaviour unchanged.

### 4.8 Temporal Orchestration

`radar/temporal/` — durable workflows with in-process workers:

- **`AgentOnboardingWorkflow`** — activities: `fetch_card`, `map_identity`, `quarantine_check`, `link_mcp`, `admit`.
- **`AgentExecutionWorkflow`** — one workflow per agent task; each pushed execution event becomes its own activity (`execution_llm_call`, `execution_web_search`, `execution_tool_call`, `execution_synthesize_answer`, …), so the Temporal UI shows the *actual* step timeline, dynamically, per run.
- No Temporal server? Onboarding falls back inline with identical logic; token accounting still works because laminar taps events *before* Temporal handling.

### 4.9 MCP Server & Governed Tools (`:8002`)

`src/awcp/mcp/server.py` — a FastMCP server (SSE) that is the **single execution path
for tools**:

- **`execute_tool`** — the governed entry: for **every** tool (read or write) it (1) asks the radar gate, (2) runs the tool via the runtime registry, (3) meters tokens, (4) records a checkpoint on the context graph. A denial returns `{status:"blocked", reason}` — the tool never runs.
- **Runtime tool registry** (`runtime/tool_runtime.py`) — tools self-declare risk/scope via the `@tool` decorator; discovery is automatic (`discover_tools()` imports everything in `awcp/tools/`). Operator can retune any tool's risk at deploy time via `AWCP_TOOL_RISK`.
- **The tools** (`src/awcp/tools/`):
  | Tool | Risk | What it does |
  |---|---|---|
  | `web_search` | low | DuckDuckGo search (keyless). |
  | `advanced_web_search` | low | DuckDuckGo + Groq agentic web search, compiled into one result. |
  | `search_arxiv` | low | arXiv paper search. |
  | `compute` utilities | low | date/time & generic helpers. |
  | `save_artifact` | **medium** | persists a result artifact — gated write. |
  | `external_post` | **high** | POSTs a summary to an external system — gated + approval. |
  | `read_file` / `write_file` / `run_command` | read/write | sandbox workspace tools (dual-registered: static `@mcp.tool` + governed `@tool`). |
  | `context_offload` / `context_recall` | — | park/retrieve context in the context graph (§4.11). |
- Also exposes Ollama access (`ask_ollama`, `SEARCH_MODEL`) for agents that ask the control plane to reason.

### 4.10 OpenSandbox Isolation (`:8090`)

`runtime/sandbox.py`:

- All file/command tools execute inside **one long-lived OpenSandbox container** with only the host `workspace/` directory bind-mounted. Everything else on the host is invisible.
- Sandbox lifecycle + every tool call is recorded to an in-memory ring **and** Postgres (`ops.sandbox_events`) so the dashboard's **Sandbox** timeline survives restarts (fail-open without a DB).

### 4.11 Context Graph — Tamper-Evident Evidence Ledger

`src/awcp/context_graph/` — the "receipt book" plus a smart-memory layer on top:

- **One node = one governed step** (tool call, route, generation). Each node carries `context_hash`, `resume_pointer`, `prev_hash`, and `row_hash = sha256(prev_hash + body)` — extending the **single** `evidence.ledger` hash chain (same formula as `radar.db`). Blocked steps are recorded too (`decision="deny"`).
- **Write path**: the MCP server POSTs `/agents/{id}/checkpoint` after every governed tool call → Postgres (durable) + an in-memory ring (fast reads, DB-off fallback).
- **Chain verification** — `GET /context-graph/verify` re-hashes the whole ledger: content check (in-place edits) + linkage check (deleted/reordered/inserted rows). The DB enforces append-only at the privilege level (`awcp_app` has no `UPDATE`/`DELETE` on `evidence`).
- **Neo4j projection** (`graph_store.py`, additive & fail-open) — mirrors checkpoints as a real graph: `(:Agent)-[:PERFORMED]->(:Step)-[:NEXT]->(:Step)`, `[:USED]->(:Tool)`, `[:BLOCKED_BY]->(:Policy)`, `[:RAISED]->(:Error)`, `(:Agent)-[:HAS_SKILL]->(:Skill)` (A2A discovery: "who can do X?").
- **Context Graph Manager** (`manager.py`) — *reasons* over the trail:
  1. **Relevance scoring** — recency decay + step weight + outcome + focus overlap, per-node explainable components;
  2. **Stale detection** — aged / superseded / dead-branch nodes flagged with reasons;
  3. **Token-budget working set** — the relevance-ranked, staleness-filtered slice that *fits a context window*, plus the resume anchor (`GET /context-graph/{wf}/working-set?budget=&focus=`).
- **Offload / recall** — two MCP tools letting a live agent use the graph as spill-over memory: `context_offload(content, label)` parks content verbatim on a chained node and returns a tiny ref; `context_recall(ref | focus+budget)` brings back the exact chunk or the best-fitting working set. Re-offloading a label supersedes the old snapshot. Both actions are themselves recorded — the trail audits the agent's memory use.
- **Letta long-term memory** (`memory.py`, optional) — checkpoints also push to Letta archival memory; recalls fold cross-run memories into the working set. REST-based, fail-open, path-configurable.

### 4.12 Laminar — Token Monitoring & Budget Control

`src/awcp/laminar/` — a self-contained subpackage (never imports `awcp.radar`; wired via
injected callbacks in `bridge.init_laminar()`):

- **Monitoring** — every execution event carrying token counts (taxonomy-free key matching: `input_tokens` / `prompt_tokens` / `gen_ai.usage.*`) is appended to a per-agent **sliding-window ledger** (default 1 h window) + lifetime totals, emitted as OTel spans/metrics, optionally JSONL-persisted, and dual-exported to **Laminar** when a key is set.
- **Control** — per-agent **token budgets** resolved positionally: operator override → agent-declared budget → risk tier map → system default. States: `ok / warn / exhausted`. On the upward transition to `exhausted`, the injected `on_breach` fires **once** and the radar maps it onto the **existing degradation ladder** — no second enforcement mechanism.
- **Cost** — env-driven price table with longest-prefix model matching ($/1M tokens); defaults to 0.0 for local Ollama (the honest number).
- **Pre-checks** (`estimator.py`) — tiktoken (or chars÷4) input estimation before a call executes.
- Serves `/laminar/*` API + its own board at `/laminar/ui`.

### 4.13 Token-Aware LLM Gateway (`/llm`)

`radar/llm_gateway.py` — "enforcement way #5", the hard wall:

- A thin **Ollama-compatible reverse proxy** in front of the model runtime. Every call is metered; if the calling agent is **over budget the proxy returns 429 and never forwards the call** — the agent physically cannot spend another token, whatever its topology or cooperation level.
- Agent identity per request: `X-AWCP-Agent-Id` header → `?awcp_agent=` query → best-effort pid→registry mapping. Optional fail-closed mode for unidentified callers.
- Point any agent's model base URL at `<gateway>/llm` to put it behind the wall.

### 4.14 Agent Hooks (lifecycle callbacks + live policy guard)

`src/awcp/agent_hooks/` — pluggable callbacks at every observable point (~18
`HookType`s: registration, task start/step/complete, `llm_call`, `tool_call`,
`gate_evaluated`, `action_blocked`, `budget_warn`, `autonomy_degraded`, …):

- **Observers** (can't change behaviour): `LoggingHook`, `AuditHook` (JSONL trail), `MetricsHook` (OTel), `TimingHook`, `NotifyHook` (webhook).
- **Guards** (may veto at the guard point `gate_evaluated`): the **`PolicyGuardHook`** — an operator deny-list of tool names, configurable **live with no restart** (`POST /hooks/guard`, or the dashboard's Agent Hooks page). Because the MCP consults the gate for *every* tool, the guard can block reads too.
- **Veto rules**: tighten-only (a guard can turn `allow`→`deny`, never `deny`→`allow`); a hook that raises is skipped (a buggy guard can't take the fleet down); hooks can never break a radar request.
- Fully removable: delete the folder and the radar runs unchanged.

### 4.15 A2A AgentCards

`radar/card.py`:

- Agents publish an **A2A-protocol AgentCard** (`/.well-known/agent.json`): name, version, endpoint, auth, and a typed **skills** list. The registry stores it as the *description* layer next to the governance layer.
- **Governance boundary**: the card's AWCP extension fields (`write_scopes`, `risk`, …) are **advisory only** — a self-published JSON can never widen its own grants; onboarding remains the only route to enforced governance.
- Skills project into the Neo4j graph (`HAS_SKILL`) for capability discovery.

### 4.16 IAM — Keycloak (authn) + OpenFGA (authz)

`gateway/auth.py` + `observability/keycloak/AWCP-realm.json` + `observability/openfga/`
(model, tuples, bootstrap) — plan in `IAM.md`:

- **Two identity planes**: humans (browser → Keycloak Authorization Code + PKCE → JWT → OpenFGA `check(user, permission, resource)`) and agents/services (shared service token or Keycloak client-credentials JWT via `src/awcp/agent_auth.py` — no interactive login).
- **Mode-gated rollout**: `AWCP_AUTH_MODE = off | shadow | enforce` (mirrors the OPA shadow pattern) — ships dark, can be proven in shadow, then enforced.
- **Roles**: Super Admin / Platform Admin / Operator / Auditor / Viewer, mapped to real endpoints (e.g. only Operators+ decide approvals; only Admins `PUT /policy` / `DELETE /agents/{id}`).
- **Signup** — self-service account creation (Keycloak user + OpenFGA tuple, least-privilege `viewer` default).
- **Audit** — allow/deny/login events extend the existing evidence pattern (`iam.audit` alongside the hash-chained `evidence.ledger`).

### 4.17 Observability Stack

`observability/` (docker-compose) + `src/awcp/observability/` (OTel setup + FastAPI
middleware):

- Python apps emit OTLP → **OTel Collector** → **Tempo** (traces), **Prometheus** (metrics), **Loki** (logs) → **Grafana** (provisioned AWCP overview dashboard, `admin / awcp1234`).
- Self-hosted **Laminar** stack (ClickHouse + Quickwit + query engine + app server + frontend, UI `:5667`) for LLM-native trace rendering (per-step token/cost trees).
- **Postgres init** (`init-db/`): `01-roles.sql` (roles `awcp_app` / `awcp_ro`), `02-schema.sql` (schemas `registry`, `governance`, `evidence`, `ops`; partitioned tables for decisions/ledgers), `test-schema.sql` (rollback smoke test).

### 4.18 React Dashboard (`:5173`)

`ui/` — a self-contained SPA (only talks HTTP to the gateway; deleting it leaves the
backend untouched). Flow: **Landing → Login (Keycloak) → App**. Pages:

| Page | What it shows |
|---|---|
| **Dashboard** | Fleet overview: stat cards, charts, draggable grid. |
| **Radar** | Every detected/registered agent: kind, framework, risk, autonomy, tokens; suspend/resume; tool-tier bar from the OPA agent. |
| **Approvals** | Pending write approvals + token approvals — live count badge + toast; approve/deny. |
| **Workflows** | Temporal task runs and their dynamic step timelines. |
| **Context Graph** | Runs on the left, governed-step chain on the right: gate decision, risk, resume pointer, tamper-chain hashes; offload (📤) / recall (📥) nodes; live polling. |
| **Token Monitor** | Per-agent token usage vs. budgets, breach states, policy editing. |
| **Agent Hooks** | Registered hooks with live call/error/deny stats, recent-events feed, the Policy Guard deny-list (chips from the real tool catalog), gate test. |
| **Policy** | The operator-policy JSON document (versioned). |
| **Sandbox** | The OpenSandbox lifecycle + tool-call timeline. |

Plus `Login`/`Landing` pages, theme system, polling hooks, and the `api.js` single fetch
chokepoint (where the Bearer token attaches).

### 4.19 Built-in Agents & the External Agent Bundle

- **Built-in specs** (`src/awcp/agents/`): `ollama_chat`, `ollama_search`, `ollama_advanced_search`, `deepseek_chat` (NVIDIA), `llama_vision` (image understanding), `triage` — Ollama/OpenAI-compatible workers used by the control plane.
- **External bundle** (`AWCP_AGENTS_DIR`, separate repo/folder): LangGraph / CrewAI / PydanticAI / arXiv agents, each a standalone FastAPI task worker with a `run.sh` and a shared `awcp_kit.py` that self-registers, asks the gate, streams execution events, and emits OTel. A `control_panel.py` (`:8099`) starts/stops each. The set is discovered at request time — 4 agents or 400, zero code change.

### 4.20 Chat Store & Per-Session Context Memory

`gateway/chat_store.py` — `ops.chat_turns` records every user turn + agent answer per
`session_id`:

- **Context memory** — before an agent runs, prior turns of the same session are read back so it can reference earlier context.
- **Context-window meter** — the task console shows Σ session tokens against `AWCP_CONTEXT_WINDOW_TOKENS` (default 128k).
- Fail-open like everything non-governance: DB down ⇒ chat works, just without memory.

### 4.21 Security Hardening

- **SSRF guard** (`radar/netguard.py`) — any registrant-supplied URL the radar itself fetches (MCP link, control endpoint) is resolved to its actual IPs first; private/loopback/link-local ranges (incl. the cloud-metadata `169.254.169.254` and IPv4-mapped-IPv6 tricks) are refused. Loopback opt-in for local dev.
- **Self-declared risk can only tighten** — the magazine's assigned tier wins downward.
- **Advisory cards** — a self-published AgentCard can't widen enforced governance.
- **Append-only ledger at the privilege level** — the app DB role cannot `UPDATE`/`DELETE` evidence.
- **Fail-secure governance** — OPA down ⇒ Python policy decides; governance DB down ⇒ no tokens ⇒ writes denied.
- **Sandbox** — file/command tools can only touch `workspace/`.

---

## 5. How It Works — Step by Step

### 5.1 Boot (one command)

```bash
bash scripts/run_everything.sh
```

Starts in order: (1) venv + deps (first run), (2) the Docker stack — OTel Collector,
Tempo/Prometheus/Loki/Grafana, Postgres (+ Keycloak, OpenFGA, OPA, Neo4j, Laminar),
(3) Temporal dev server, (4) Ollama, (5) OpenSandbox runtime, (6) MCP control server,
(7) OPA engine + the hidden OPA tool-tier agent, (8) the React dashboard, (9) the AWCP
Gateway in the foreground (mounting radar, laminar, context graph, hooks, `/user` API,
and the in-process Temporal workers). `Ctrl+C` (or `scripts/stop_everything.sh`) stops
it all. Skip toggles: `SKIP_TELEMETRY/SANDBOX/MCP/OPA/OLLAMA/UI/INSTALL=1`, `DEMO=1`.

### 5.2 An agent joins the fleet

1. An agent starts (from the bundle, or anything agent-shaped on the host).
2. Either it **self-registers** (`POST /agents/register` via `awcp_kit`, presenting service identity when IAM is on), or the **radar scanner detects** it within a scan cycle.
3. The **onboarding workflow** runs (Temporal, else inline): fetch its A2A card → map identity against the magazine (owner/runtime/version; risk = more-restrictive-of declared vs. assigned) → **quarantine check** (telemetry + policy hooks present?) → link its MCP endpoint (SSRF-checked) → **admit**.
4. Admitted agents show `active` on the Radar page; uninstrumented ones stay `quarantined` (visible, write-blocked).

### 5.3 A user runs a task (the happy path)

1. **Login** — the browser hits the Landing page → Login → Keycloak (PKCE) → JWT attached to every `api.js` call; the gateway middleware validates it and OpenFGA checks the role (in `enforce` mode).
2. **Prompt** — the user picks an agent on the dashboard and submits a goal → `POST /user/submit` → the gateway (launching the agent if needed) forwards the goal to the agent's `POST /tasks`.
3. **Execution starts** — the agent reports `POST /tasks/execution/start`; the radar opens an `AgentExecutionWorkflow` in Temporal; hooks fire `task_started`; prior session turns are fed in as context memory.
4. **The agent reasons** — it calls its model (directly at Ollama or through the budget-gated `/llm` proxy). Each step is pushed as an execution event → a Temporal activity + laminar token accounting + hooks (`llm_call`, `web_search`, …).
5. **A tool call** — the agent never runs tools locally. It calls the **MCP server's `execute_tool`**, which:
   1. asks the **radar gate** — `POST /agents/{id}/gate {action, scope, write}`;
   2. the gate checks: quarantine? autonomy rung allows writes? scope declared? token budget headroom? → **OPA** (or Python fallback) decides `auto_authorized / awaiting_token / awaiting_operator / denied` → operator policy override → **hook guards** may tighten allow→deny;
   3. on **allow**: the tool runs (sandbox tools inside the OpenSandbox container), tokens are metered, and a **checkpoint** is recorded — one hash-chained node on `evidence.ledger`, mirrored to Neo4j and Letta;
   4. on **deny**: `{status:"blocked", reason}` returns, the blocked step is recorded on the chain too, and the UI renders ⛔ with the reason.
6. **High-risk write** — decision `awaiting_token`/`awaiting_operator`: the gate issues a pending expiring token and the action **pauses**. The **Approvals** page badges + toasts; the operator approves or denies; the token authorizes only that action class, one branch, one expiry window; the write proceeds or dies.
7. **Synthesis** — the agent emits `synthesize`, completes via `POST /tasks/execution/{id}/complete`; the workflow closes; hooks fire `task_completed`; the turn (input, output, tools, tokens, timing) is appended to `ops.chat_turns`.
8. **The user watches it all live** — the step timeline on the dashboard (read from Temporal history), the governed-step chain on the Context Graph page, tokens on the Token Monitor, traces in Grafana/Tempo and Laminar.

### 5.4 When things go wrong (the governance loops)

- **Failures** — each failed task decrements the failure budget; at zero the agent steps **down the ladder** (`trace_boost → throttled → safe_profile → recommendation_only → suspended`). Early rungs tighten operations; later rungs block writes; `suspended` is a hard stop. Degradations land in `governance.degradation_events` + hooks (`autonomy_degraded`).
- **Token overspend** — the sliding-window ledger crosses `warn` then `exhausted`; the breach callback drives the *same* ladder, and the `/llm` proxy starts returning 429 — the model call itself is refused.
- **Live kill-switch** — an operator adds a tool to the Policy Guard deny-list (no restart); the very next gate check for that tool, by any agent, in any framework, is denied with a shown reason.
- **Tampering** — `GET /context-graph/verify` re-derives the whole hash chain; any in-place edit, deletion, or reordering of the evidence ledger is reported with the exact break.
- **Long tasks vs. small context windows** — the agent `context_offload`s bulky findings into the graph (keeping a tiny ref) and later `context_recall`s the exact chunk or a budget-fitted, relevance-ranked working set — with cross-run Letta memories folded in.

---

## 6. Ports & Surfaces

| Surface | URL / Port |
|---|---|
| React Dashboard | http://localhost:5173 |
| AWCP Gateway / API (docs at `/docs`) | http://localhost:8000 |
| Token monitor board | http://localhost:8000/laminar/ui |
| Budget-gated LLM proxy | http://localhost:8000/llm |
| MCP control server (SSE) | http://localhost:8002 |
| OpenSandbox runtime | http://localhost:8090 |
| Temporal (engine / UI) | :7233 / http://localhost:8233 |
| Ollama | http://localhost:11434 |
| Grafana (`admin` / `awcp1234`) | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| OTel Collector | :4317 (gRPC) / :4318 (HTTP) |
| Postgres (`awcp` / `awcppassword` / `awcp`) | :5432 |
| Keycloak (realm `AWCP`) | http://localhost:8083 |
| OpenFGA | http://localhost:8081 |
| OPA | http://localhost:8181 |
| Neo4j (Browser / Bolt) | http://localhost:7474 / :7687 |
| Laminar UI | http://localhost:5667 |
| Agent bundle control panel | http://localhost:8099 |

---

## 7. Repository Layout

```text
awcp-mcp-temp/
├── scripts/
│   ├── run_everything.sh        # ONE launcher for the whole stack
│   ├── stop_everything.sh
│   └── clean_cache.sh
├── src/awcp/
│   ├── gateway/                 # FastAPI app (:8000): user API, agent discovery/launch,
│   │                            #   IAM middleware, signup, OPA proxy, chat store
│   ├── radar/                   # registry, scanner+detectors, write gate, policy ladder,
│   │                            #   OPA adapter, approval tokens, operator policy, SSRF
│   │                            #   guard, A2A cards, LLM gateway, Temporal workflows
│   ├── mcp/server.py            # FastMCP server (:8002) — governed execute_tool
│   ├── runtime/                 # tool runtime/registry, Ollama client, OpenSandbox bridge,
│   │                            #   sandbox event store, schemas/config
│   ├── tools/                   # web_search, advanced_web_search, arxiv, compute,
│   │                            #   save_artifact, external_post, sandbox_tools
│   ├── context_graph/           # hash-chained evidence trail, verify, Neo4j projection,
│   │                            #   smart-memory manager, offload/recall, Letta
│   ├── laminar/                 # token ledger, budgets, estimator, OTel/Laminar export
│   ├── agent_hooks/             # lifecycle hooks + live policy guard
│   ├── opa_agent/               # hidden SLM tool-tier PDP (standalone service)
│   ├── observability/           # OTel setup + FastAPI middleware
│   ├── agents/                  # built-in agent specs (ollama_*, deepseek, vision, triage)
│   ├── registry/                # agent registry store/service/discovery
│   └── agent_auth.py            # service identity for agents under IAM
├── ui/                          # React + Vite + Tailwind dashboard (:5173)
├── observability/               # docker-compose stack, init-db SQL, Keycloak realm,
│   │                            #   OpenFGA model/tuples, Grafana provisioning
├── policies/awcp/               # OPA Rego: gate.rego, tools.rego (+ tests)
├── workspace/                   # the ONLY host dir mounted into the sandbox
├── artifacts/                   # saved result artifacts
├── docs/                        # this file + the AWCP magazine (design brief)
├── tests/                       # laminar, context_graph, radar test suites
├── requirements.txt
├── README.md                    # quickstart
└── IAM.md                       # the IAM implementation plan
```

---

## 8. Design Philosophy

A few rules repeat across every module:

1. **Fail-open observability, fail-secure governance.** Telemetry, hooks, Neo4j, Letta, chat memory, the sandbox event store — all degrade silently and never break a run. The gate, approval tokens, and risk mapping fail **closed**: if the policy engine or governance DB is unreachable, the write is denied.
2. **Nothing hardcoded.** Ladders, budgets, price tables, risk tiers, deny-lists, ports, model names, paths — all env-driven with documented defaults. Agents and tools are discovered, never enumerated in code.
3. **One enforcement mechanism, many inputs.** Failures, token breaches, and operator signals all drive the *same* degradation ladder and the *same* write gate — no parallel governance paths.
4. **Tighten-only for the untrusted.** Self-declared risk, agent cards, and hook guards can add restriction, never remove it. Only the human operator may loosen.
5. **One evidence chain.** Checkpoints, gate decisions, and denials extend a single hash-chained, append-only ledger; Neo4j and Letta are read-model projections, not sources of truth.
6. **Removable modules.** Laminar, hooks, context graph, the UI — each is self-contained and wired through narrow injected seams; deleting one leaves the rest running.
7. **Shadow-first rollouts.** Both OPA (`AWCP_OPA_SHADOW`) and IAM (`AWCP_AUTH_MODE=shadow`) can observe-and-log before they enforce.

---

## 9. Running & Testing

```bash
# everything
bash scripts/run_everything.sh

# tests
pytest tests/                      # laminar pre-check/estimator, context-graph
                                   # offload/recall, radar store/db/OPA-gate
opa test policies/awcp/            # Rego policy unit tests

# poke the control plane
curl -s localhost:8000/agents | jq                 # the fleet
curl -s localhost:8000/context-graph | jq          # governed-step feed
curl -s localhost:8000/context-graph/verify | jq   # tamper check
curl -s localhost:8000/laminar/status | jq         # token budgets
curl -s localhost:8000/hooks | jq                  # hook system
```

See `README.md` for prerequisites (Python ≥ 3.10, uv, Docker Desktop, Ollama models,
optional Temporal CLI) and per-machine `.env` configuration.
