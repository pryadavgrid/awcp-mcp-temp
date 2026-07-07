# AWCP SDK — a Separate Package for Building and Registering Agents

> **Goal of this document.** Propose `awcp-sdk`: a standalone, pip-installable
> Python package that lets a developer create an agent, expose its tools, and
> register the whole thing with a running AWCP — in a few lines of code, from
> any machine, **with no checkout of the control-plane repo**.
>
> The SDK is a *client* of AWCP's existing HTTP + MCP surfaces. Agent
> registration needs **zero** control-plane changes. Governed remote **tools**
> need four small, contained changes on the AWCP side, listed in §5.
>
> Three parts: **The Idea → The SDK → The AWCP-side Prerequisites.**

---

## 1. The Idea

Today, writing an agent that participates in AWCP governance means reading the
radar's API by hand: craft a `RegisterRequest`, serve an AgentCard at
`/.well-known/agent.json`, emit execution events with the right shapes, consult
the gate before writes. All the surfaces exist — but every agent author
re-implements the client side from scratch.

The SDK packages that client side once:

```python
import awcp_sdk

app = awcp_sdk.Agent(
    name="pdf-analyst",
    version="1.0.0",
    awcp_url="http://localhost:8000",
    risk="medium",
    feature_flags={"beta_summaries": True},
)

@app.tool(risk="medium", description="Summarize a PDF by URL.")
def summarize_pdf(url: str) -> str:
    ...

@app.skill(id="pdf-analysis", description="Answers questions about PDFs")
async def handle(task: awcp_sdk.Task) -> str:
    text = await task.llm("...")          # emits llm_called automatically
    return text                            # completion reported automatically

app.run(port=9400)
```

`app.run()` does everything the governance plane expects of a well-behaved
agent:

1. serves the A2A **AgentCard** at `/.well-known/agent.json` (skills from the
   decorators);
2. serves the agent's tools as a small **FastMCP SSE server** (this *is* tool
   registration — see §4);
3. **announces** to the radar (`POST /agents/announce`) with pid, endpoint,
   feature flags, and policy-callback declarations;
4. emits **execution events** (`POST /tasks/execution/...`) so every step
   appears as a Temporal activity and the observed-hook quarantine gate can be
   satisfied;
5. **consults the gate** before governed actions (which proves the policy hook);
6. **deregisters** cleanly on shutdown.

In other words: the SDK's job is to make an agent *earn its way out of
quarantine by construction* — telemetry, flags, and policy hooks observed in
execution, per `decide_status` (`src/awcp/radar/onboarding.py:61`) — without
the author knowing that machinery exists.

### Non-goals

- **Not** a repackaging of the control plane. AWCP keeps running exactly as it
  does today (`scripts/run_everything.sh`); the SDK points at its URL.
- **Not** a launcher. No `awcp.run()`-style stack orchestration; Temporal,
  OTel, Ollama, Postgres are server-side concerns the SDK never touches.
- **Not** a bypass. Every governance invariant (risk tightening, scope
  approval, observed hooks, SSRF guards) is preserved — several are
  *strengthened* for remote tools (§5.2).

### Naming

The package is **`awcp-sdk`**, importable as **`awcp_sdk`** — deliberately not
`awcp`. The control-plane repo already occupies the `awcp` namespace (via
`PYTHONPATH=src`); an installed package with the same name would shadow it on
any machine running both.

---

## 2. The Surface the SDK Wraps (all existing, verified)

| Capability | AWCP surface | Where |
|---|---|---|
| Register / announce | `POST /agents/register`, `POST /agents/announce` | `src/awcp/radar/api.py:1485`, `:1599` |
| Card-first registration | `POST /agents/register-card` | `src/awcp/radar/api.py:1701` |
| Identity manifest | AgentCard at `/.well-known/agent.json` (A2A) | schema in `src/awcp/radar/card.py` |
| Tool enumeration | onboarding `link_mcp` connects to the agent's MCP SSE endpoint and stores tool names in `entry.capabilities` | `src/awcp/radar/onboarding.py:116`, `src/awcp/radar/models.py:102` |
| Execution reporting | `POST /tasks/execution/start` / `{id}/event` / `{id}/complete` | `src/awcp/radar/api.py:2489+` |
| Policy gate | `POST /agents/{id}/gate` | `src/awcp/radar/api.py:1820` |
| Deregister | `POST /agents/{id}/deregister` | `src/awcp/radar/api.py:2436` |
| Onboarding visibility | `announce` response carries `onboarding_workflow_id` + Temporal deep link | `src/awcp/radar/api.py:1599` |

The `announce` path is the SDK's default: the entry is visible immediately, the
onboarding workflow starts synchronously inside the request, and the response
lets the SDK log a Temporal URL where the developer can watch their own agent
onboard.

---

## 3. The SDK

### 3.1 Package layout

```
awcp-sdk/                     (its own repo)
├── pyproject.toml            name="awcp-sdk", deps: httpx, pydantic, mcp, fastapi, uvicorn
└── src/awcp_sdk/
    ├── __init__.py           Agent, Task, tool, skill — the whole public API
    ├── card.py               AgentCard / AgentSkill models (mirrors radar/card.py)
    ├── client.py             RadarClient: announce, gate, events, deregister, relink
    ├── agent.py              Agent class: decorators, lifecycle, event plumbing
    └── serve.py              ASGI app: /.well-known/agent.json + FastMCP SSE mount
```

Dependencies stay thin on purpose: `httpx` + `pydantic` for the client,
`mcp` for the tool server, `fastapi`/`uvicorn` for serving. No `temporalio`,
no `opentelemetry-*`, no DB drivers.

### 3.2 Lifecycle in detail

```
app.run(port=9400)
  │
  ├─ 1. mount /.well-known/agent.json          (card built from decorators)
  ├─ 2. mount /sse                             (FastMCP server: the @app.tool fns)
  ├─ 3. POST {awcp}/agents/announce            → entry visible, onboarding starts
  │        {name, pid, endpoint=http://host:9400, feature_flags,
  │         policy_callbacks=[...], risk, write_scopes}
  │
  │      AWCP side (existing, unchanged):
  │        fetch_card → map_identity → quarantine_check → link_mcp → admit
  │        link_mcp enumerates the SDK's MCP tools into entry.capabilities
  │
  ├─ 4. on each task:  POST /tasks/execution/start
  │        each llm/tool/search step → POST .../event   (proves telemetry hook)
  │        governed writes → POST /agents/{id}/gate      (proves policy hook)
  │        flag state reported in events                 (proves flags hook)
  │        → agent auto-promotes: quarantined → active
  │
  └─ 5. atexit / SIGTERM:  POST /agents/{id}/deregister
```

Note what the SDK does **not** decide: its declared `risk` may only *tighten*
the magazine-assigned tier (`policy.authoritative_risk`), its card governance
fields are advisory by design (`card.py` governance boundary), and added
`write_scopes` on re-announce still trigger the operator re-approval hold.
The SDK inherits all of that for free by using the real endpoints.

### 3.3 Auth

AWCP now fronts IAM (Keycloak + OpenFGA). The SDK takes a token/credentials
parameter and attaches it to every radar call. Exact flow (client-credentials
vs. API key) should match whatever `AWCP_AUTH_MODE` settles on; the SDK treats
it as a pluggable header provider.

---

## 4. Tool Registration — the MCP-native Design

**Decision: remote tools are exposed as MCP tools on the agent's own SSE
endpoint, and AWCP dispatches to them through its existing governed executor.
There is no separate tool-registration API, no callback protocol, no new DB
table.** The agent's announce + card + MCP link *is* the registration.

Why this over an HTTP-callback design: onboarding already connects to the
agent's MCP endpoint and persists its tool list (`link_mcp` →
`entry.capabilities`); the governed executor already resolves per-tool risk and
scope dynamically with nothing hardcoded per tool. Reusing both means the
control plane's *single choke point* for tool execution — the governed
`execute_tool` in `src/awcp/mcp/server.py:443`, which wraps every call in the
radar gate, OPA policy guard, token metering, context-graph checkpoint, and
Temporal execution event — stays the only place tools run.

### 4.1 Call flow

```
caller (any governed agent, or the control plane's reason→tool loop)
  │
  ▼
execute_tool(tool_name="reg-pdf-analyst/summarize_pdf", tool_input={...},
             agent_id=<caller>, task_id=...)
  │
  ├─ 1. gate the CALLER            (unchanged: _radar_gate → quarantine /
  │                                  autonomy / token / operator deny-list)
  ├─ 2. resolve the OWNER          "reg-pdf-analyst" → GET /agents/{id}
  │       require: alive ∧ status=="active" ∧ "summarize_pdf" ∈ capabilities
  │       re-assert SSRF guard on entry.endpoint
  ├─ 3. dispatch over MCP          sse_client(endpoint) → call_tool(...)
  │       (same client code link_mcp already uses; ephemeral session)
  └─ 4. record                     checkpoint + Temporal event + token metering,
                                   marked origin="remote", owner="reg-pdf-analyst"
```

Properties that fall out of step 2 for free:

- **Lifecycle coupling.** A quarantined or dead owner's tools are uncallable
  instantly — the check reads live registry status at call time.
- **Operator veto with zero new machinery.** Tool scope defaults to the
  namespaced name (existing `get_tool_scope` convention), so the policy guard
  can deny-list `reg-pdf-analyst/summarize_pdf` by name today.
- **No namespace collisions.** Namespaced names can never shadow built-in
  tools (`web_search`, `run_command`, …).

**Accepted trade-off:** one MCP session handshake per remote call (ephemeral
sessions, like `link_mcp`). If latency ever matters, a per-endpoint session
cache with a short TTL is a contained optimization inside the dispatch helper.

---

## 5. AWCP-side Prerequisites (the only control-plane changes)

Four changes, two files (`src/awcp/mcp/server.py`, `src/awcp/radar/api.py`).
Everything else in this proposal is client-side.

### 5.1 Namespaced remote dispatch in the governed executor

In `execute_tool` (`src/awcp/mcp/server.py:443`): if `tool_name` contains
`/`, split into `(owner_id, tool)` and take the remote branch of §4.1 instead
of the local `TOOL_REGISTRY` lookup. Add an `_invoke_remote_mcp(endpoint,
tool, input)` helper mirroring `onboarding.link_mcp`'s client code, with a
timeout, response-size cap, and the `netguard.assert_safe_url` check re-run at
call time (the endpoint may have changed since link).

*~80 lines. The gate, metering, checkpoint, and Temporal-event wrapping are
untouched — the remote branch sits between gate and record.*

### 5.2 Risk resolution for remote tools: default `medium`, tighten-only

`get_tool_risk` (`src/awcp/runtime/tool_runtime.py:77`) defaults undeclared
tools to `low` — correct for audited in-repo tools, **backwards for foreign
code**, because `low` bypasses the write gate entirely. For namespaced tools:

- default risk = `AWCP_DEFAULT_REMOTE_TOOL_RISK`, itself defaulting to
  **`medium`** (write-gated);
- a risk declared by the agent (card skill metadata) may only **tighten**,
  never relax — reuse `policy.more_restrictive`, the same rule agents get;
- the operator's `AWCP_TOOL_RISK` env override map still wins outright — it is
  name-keyed and works on namespaced names unchanged.

*~20 lines plus one env var.*

### 5.3 A re-link trigger for runtime tool changes

`capabilities` are only populated during onboarding, so a tool added after
admission is invisible. Extend the existing `POST /agents/{id}/card/refresh`
(`src/awcp/radar/api.py:1452`) to also re-run `link_mcp`, or add a sibling
`POST /agents/{id}/relink`. The SDK calls it whenever a tool is registered
after `app.run()`. Guard: only the authenticated owner (or an operator) may
trigger a re-link.

*~30 lines, reusing the existing activity.*

### 5.4 Provenance marking in evidence

Pass `origin="remote"` and the owner id through `_record_checkpoint` and
`_emit_exec_event` so the audit trail distinguishes agent-supplied tool output
from in-repo tool output. Today a tool result is produced by audited code; a
remote result is foreign data, and the evidence ledger should say so.

*A few lines; the payloads are already dicts.*

### Explicitly NOT required (vs. the HTTP-callback alternative)

- ✗ a tool-registration API (`POST /tools/register`)
- ✗ a `registry.tools` DB table + cross-process hydration loop
- ✗ a proxy-callable layer in `tool_runtime`
- ✗ a bespoke callback protocol (+ its auth, retries, versioning)

---

## 6. Governance Invariants (before / after)

| Invariant | Today (agents) | After (remote tools) |
|---|---|---|
| Risk may only tighten | `authoritative_risk` at register | same rule via `more_restrictive`, default `medium` (§5.2) |
| Scope creep → operator approval | `write_scopes` drift guard on re-register | unchanged; tool scope = namespaced name, deny-listable |
| Active status is earned | observed telemetry / flags / policy hooks | owner must be `active` at **every** call (§4.1 step 2) |
| Card fields are advisory | card never patches enforced fields | tool risk from card may only tighten (§5.2) |
| SSRF guards on agent URLs | register / card fetch / MCP link | re-asserted at every remote dispatch (§5.1) |
| One choke point for tool runs | governed `execute_tool` | unchanged — remote branch lives inside it |

---

## 7. Plan

| Phase | Deliverable | Where |
|---|---|---|
| **1. SDK core** | `awcp-sdk` repo: card + client + Agent lifecycle; announce → quarantine → auto-promote to active, end-to-end against a running AWCP | new repo |
| **2. AWCP prerequisites** | §5.1–§5.4 behind `AWCP_REMOTE_TOOLS=true` (default off until exercised) | this repo, 2 files |
| **3. SDK tools** | `@app.tool` → embedded FastMCP server; re-link call; namespaced invocation demo (agent A calls agent B's tool through the governed executor) | both |
| **4. Polish** | session-cache optimization if needed; auth hardening to match `AWCP_AUTH_MODE`; publish `awcp-sdk` internally | both |

Phases 1 and 2 are independent and can proceed in parallel; Phase 3 is the
integration point.

**Bottom line.** The SDK is a thin, honest client of surfaces AWCP already
exposes — registration costs the control plane nothing, and governed remote
tools cost it four contained changes in two files, each of which *tightens*
rather than relaxes the governance model.
