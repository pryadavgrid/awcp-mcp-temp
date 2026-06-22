# AWCP Agent Hooks

A small, extensible **hook system** that fires pluggable callbacks at every point
in an agent's life that AWCP can observe — registration, each task, each
LLM/tool/web-search/synthesize step, every governance decision, and every token
event.

Use it to add **logging, metrics, audit trails, alerts**, or — the headline
feature — a **policy guard that can block any tool an agent tries to use, live,
with no restart and no hardcoding.** Add a tool to the deny-list, and the next
time any agent calls that tool the call is denied and the reason is shown to the
end user.

> **Folder name note.** The requested name was *"Agent hooks"*, but a Python
> package can't contain a space (it must import as `awcp.agent_hooks` so the
> control plane can wire it in). The folder is therefore `agent_hooks/`.

---

## 1. Why hooks live in the control plane

In AWCP the **agents are decoupled** — standalone programs in their own bundle
(`awcp-agents/`). But every agent already reports its **entire lifecycle** to the
radar (the control plane inside the gateway), and every tool it runs goes through
the **MCP server**, which asks the radar's **gate** first:

```
agent ──register───────────────▶ radar           (it exists)
agent ──/tasks/execution/start─▶ radar           (a task began)
agent ──/tasks/execution/{id}/event─▶ radar      (llm_called, tool_called, web_search, synthesize)
agent ──(tool call)──▶ MCP server ──/agents/{id}/gate──▶ radar   (may I run this tool?)
agent ──/agents/{id}/signal────▶ radar           (a task succeeded/failed)
agent ──/tasks/execution/{id}/complete─▶ radar   (the task finished)
```

So the **radar is the one place that sees all agents' all events** — and the
**gate is the one place every tool call must pass**. Wiring hooks there makes them
apply to **every agent, in every framework (LangGraph, CrewAI, PydanticAI,
arXiv…), with zero agent-side changes.**

It's wired exactly like the `awcp.laminar` token monitor: **optional and
self-contained.** Delete `src/awcp/agent_hooks/` and the radar runs unchanged (it
logs `radar.agent_hooks.unavailable` and carries on).

```
   user prompt ─▶ User UI ─▶ gateway /user/submit ─▶ agent runs
                                                        │ tool call
                                                        ▼
                                  MCP server ──/agents/{id}/gate──▶ ┌─ Radar gate ─┐
                                                                    │ base policy   │
                                                                    │   + dispatch  │
                                                                    │   GATE_EVALUATED ──▶ HookManager
                                                                    │                       └─ policy-guard? deny-listed → DENY
                                                                    └───────────────┘
                                  ◀── allow / DENY(reason) ─────────────────┘
                       tool blocked → task "blocked" → User UI shows ⛔ + reason
```

---

## 2. How a deny-listed tool gets blocked — end to end

This is the core mechanism. Say an operator deny-lists **`web_search`** and a user
asks an agent to search the web:

1. **Operator adds `web_search` to the deny-list** (dashboard → Agent Hooks →
   Policy Guard, or `POST /hooks/guard`). This registers/updates the
   `PolicyGuardHook` with `deny_tools={"web_search"}`. No restart.
2. **User sends a prompt** in the User UI → `POST /user/submit` → the agent starts
   running its task.
3. **The agent decides to call `web_search`.** Agents don't run tools locally —
   the call goes to the **MCP server's `execute_tool`**.
4. **The MCP server consults the gate for every tool** (read or write):
   `POST /agents/{id}/gate {action: "web_search", write: false, …}`.
5. **The radar gate runs base policy first.** `web_search` is a read, so base
   policy returns `allow` ("reads are not gated").
6. **The gate then dispatches `GATE_EVALUATED` to the HookManager.** The
   `PolicyGuardHook` sees `action="web_search"` is in its deny-list and returns
   `HookOutcome.deny(...)`.
7. **The manager applies the veto (tighten-only):** base said `allow`, a guard
   said `deny` → the gate's final decision becomes **`deny`, `mode="hook_guard"`**,
   with reason `"policy-guard: action 'web_search' blocked by policy-guard
   deny-list"`. The gate also fires `ACTION_BLOCKED` for the observer hooks.
8. **The MCP server returns `{status: "blocked", reason}`** to the agent — the
   tool never runs.
9. **The agent records a blocked step**, and the task settles as **`blocked`**.
10. **The User UI** polls `/user/status`, sees the blocked step, and renders a red
    **⛔ Blocked by AWCP governance** card with the reason — and stops (no answer).

Remove `web_search` from the deny-list (or disable the guard) and the exact same
prompt runs normally. **Nothing about `web_search` is hardcoded anywhere** — the
guard only ever compares the tool name to the operator's deny-list.

> Why step 4 matters: originally the MCP server only consulted the gate for
> *write* tools, so deny-listing a *read* tool did nothing. It now consults the
> gate for **every** tool (`src/awcp/mcp/server.py`), which is what lets the guard
> block any tool. Base policy still treats reads as reads (it allows them); the
> guard is what tightens an allow into a deny.

---

## 3. The lifecycle hook points (`HookType`)

Every point the radar fires a hook on. A hook subscribes to one or more.

| HookType | Fires when | Key `ctx.data` fields |
|---|---|---|
| **Agent lifecycle** | | |
| `agent_registered` | an agent self-registers / is admitted | `name, kind, framework, risk` |
| `agent_deregistered` | an agent is removed from the registry | `reason` |
| **Task lifecycle** | | |
| `task_started` | a governed execution begins | `goal, framework` |
| `task_completed` | a task finishes successfully | `status, tools_used, result_len` |
| `task_failed` | a task fails or is blocked | `status, error, tools_used` |
| **Per-step** | | |
| `step` | **every** execution step (generic) | `step, tool_name, model` |
| `llm_call` | the agent called its model | `model, call_n` |
| `tool_call` | the agent invoked a tool | `tool_name, risk, gate` |
| `web_search` | the agent ran a web search | `query` |
| `synthesize` | the agent built its final answer | `result_len` |
| **Governance** | | |
| `gate_evaluated` | a tool call hit the gate **(GUARD point)** | `action, scope, write, decision, mode, risk` |
| `action_blocked` | an action was denied (guard / quarantine / token) | `action, reason, risk` |
| `approval_required` | a high-risk action needs a human | `action, risk` |
| `signal_received` | an agent reported a task outcome | `ok, reason, failure_count` |
| `autonomy_degraded` | the agent was stepped down its ladder | `to, reason, trigger` |
| **Token economy** | | |
| `token_usage` | real token usage was metered | `tokens, model` |
| `budget_warn` | the agent crossed its warn threshold | `used, budget` |
| `budget_exhausted` | the agent hit/exceeded its budget | `used, budget` |

Every hook receives a `HookContext` with: `hook_type`, `agent_id`, `task_id`,
`ts` (timestamp), and `data` (the table's fields). Convenience accessors:
`ctx.action`, `ctx.risk`, `ctx.decision`, `ctx.tool_name`, `ctx.get(key, default)`.

---

## 4. Two kinds of hooks: observer vs guard

| Category | Can it change behaviour? | Examples |
|---|---|---|
| `OBSERVER` | No — watches only. Return value ignored. | logging, audit, metrics, timing, notify |
| `GUARD` | Yes — may **veto** at a *guard point*. | policy-guard |

**Veto rules (important & safe by design):**

1. A `deny` is **only honoured at a guard point** — currently `gate_evaluated`.
   Returning `deny` from, say, `tool_call` is recorded but ignored (that event is
   a *report* that the step already happened; there's nothing left to stop). The
   gate is the real choke point, and the MCP consults it for every tool — so a
   guard at `gate_evaluated` can block any tool.
2. Guards are **tighten-only**: the manager turns an `allow` into a `deny` if any
   guard denies, but it **never** turns the radar's `deny` into an `allow`. A hook
   can only *add* restriction, never remove it.
3. A guard that **raises** is skipped (fail-open on hook errors, so a buggy guard
   can't take the fleet down). If you need fail-closed, encode it in your hook.

---

## 5. Built-in hooks

| Hook | Category | Subscribes to | Default | What it does |
|---|---|---|---|---|
| `LoggingHook` | observer | all points | **on** | one structured log line per event (also lands in Loki/Grafana) |
| `AuditHook` | observer | governance points | **on** | append-only JSONL trail that survives restarts |
| `MetricsHook` | observer | all points | **on** | OTel counters/histograms → Prometheus/Grafana |
| `TimingHook` | observer | task start/step/end | **on** | logs per-task duration + step count |
| `NotifyHook` | observer | approval / blocked / breach | off | webhook (Slack/Discord) alert, sent off-thread |
| `PolicyGuardHook` | **guard** | `gate_evaluated` | off | **vetoes ANY tool (read or write) on a deny-list** — the operator's live kill-switch for a capability |

Each is a **reference example** for a *category* of hook — copy one as a starting
point for your own.

---

## 6. The Policy Guard in depth

The `PolicyGuardHook` is the one that blocks tools. Key facts:

- It subscribes to `gate_evaluated` (the only guard point) and **denies when the
  tool name is in its deny-list** — otherwise it allows. That's the whole logic
  ([`builtin/policy_hook.py`](builtin/policy_hook.py)).
- The deny-list is **operator-supplied** (env var or, better, the runtime API /
  dashboard). No tool name is hardcoded.
- It works for **any tool** because the MCP gates every tool call (see §2).
- It is **tighten-only and demo-safe**: it can only turn a base `allow` into a
  `deny`; if it errors, the call is not blocked by a broken guard.

**Configure it live (no restart):**

```bash
# enable + set the deny-list
curl -s -X POST localhost:8000/hooks/guard -H 'content-type: application/json' \
  -d '{"deny_tools":["web_search","external_post"],"enabled":true}'

# see the current config
curl -s localhost:8000/hooks/guard

# one-click deterministic veto test (doesn't need a real agent run)
curl -s -X POST localhost:8000/hooks/guard/test -H 'content-type: application/json' \
  -d '{"agent_id":"agent-x","action":"web_search"}'
# → {"decision":"deny","mode":"hook_guard","reason":"policy-guard: action 'web_search' …"}

# turn it off
curl -s -X POST localhost:8000/hooks/guard -H 'content-type: application/json' -d '{"enabled":false}'
```

> The guard only *decides* deny. For an agent to actually be **stopped**, the
> agent must reach the gate (i.e. it must be `active`, not quarantined, and have
> autonomy/token headroom) — otherwise those other governance layers would block
> the write first and mask the guard. `run_everything.sh` ships defaults that keep
> agents active so the guard is the clean on/off lever (see that script's
> "toggleable-guard demo defaults" block).

---

## 7. Configuration (all env-driven)

These seed the **built-in** hooks at startup. The Policy Guard can additionally be
configured at runtime via `POST /hooks/guard` (above), which overrides the env
deny-list without a restart.

| Env var | Default | Effect |
|---|---|---|
| `AWCP_HOOKS_ENABLED` | `true` | master switch for the whole system |
| `AWCP_HOOKS_LOGGING` | `true` | load `LoggingHook` |
| `AWCP_HOOKS_AUDIT` | `true` | load `AuditHook` |
| `AWCP_HOOKS_METRICS` | `true` | load `MetricsHook` |
| `AWCP_HOOKS_TIMING` | `true` | load `TimingHook` |
| `AWCP_HOOKS_NOTIFY` | `false` | load `NotifyHook` |
| `AWCP_HOOKS_POLICY_GUARD` | `false` | load `PolicyGuardHook` at boot (also auto-registered on first `POST /hooks/guard`) |
| `AWCP_HOOKS_AUDIT_PATH` | `/tmp/awcp-agent-hooks-audit.jsonl` | audit file path |
| `AWCP_HOOKS_NOTIFY_WEBHOOK` | (unset) | webhook URL for `NotifyHook` |
| `AWCP_HOOKS_DENY_TOOLS` | (unset) | comma list of tools `PolicyGuardHook` vetoes at boot |

---

## 8. HTTP API (mounted on the gateway at `:8000`)

| Method & path | Purpose |
|---|---|
| `GET /hooks` | registered hooks + per-hook stats (calls/errors/denies) + status |
| `GET /hooks/recent?limit=50` | ring buffer of recently dispatched events (newest first) |
| `POST /hooks/{name}/enable` | turn a hook on at runtime |
| `POST /hooks/{name}/disable` | turn a hook off without unregistering |
| `GET /hooks/guard` | current policy-guard config `{enabled, deny_tools}` |
| `POST /hooks/guard` | enable/configure the guard live `{deny_tools[], enabled}` |
| `POST /hooks/guard/test` | deterministic veto test `{agent_id, action}` → decision |

---

## 9. The UIs

Two React UIs drive this with no curl needed (both pure frontends — delete them
and the backend is unaffected):

- **Dashboard** (`ui/`, default `:5174`) → **Agent Hooks** page:
  - stat cards (system on/off, hooks loaded, event types, recent events),
  - the **registered-hooks table** with live calls/errors/denies + enable/disable
    toggles,
  - a **recent-events feed** (⛔ marks a guard veto),
  - a **Policy Guard** card (deny-list built from the agents' real tool catalog as
    clickable chips) and a **Test the gate** card.
- **User UI** (`awcp-user-ui/`, default `:5173`): when a prompt's tool call is
  blocked, [`ResultPanel.jsx`](../../../../UI/awcp-user-ui/src/components/ResultPanel.jsx)
  detects it (blocked status / blocked step / denied gate) and shows a red
  **⛔ Blocked by AWCP governance** card with the reason, suppressing the answer.

---

## 10. Writing your own hook

### Option A — subclass `Hook` (recommended)

```python
from awcp.agent_hooks import Hook, HookType, HookCategory, register

class SlowTaskAlert(Hook):
    name = "slow-task-alert"
    category = HookCategory.OBSERVER
    subscriptions = (HookType.TASK_COMPLETED,)
    priority = 60

    def handle(self, ctx):
        print("task done for", ctx.agent_id)   # observers return None

register(SlowTaskAlert())
```

### Option B — register a bare function

```python
from awcp.agent_hooks import register_fn, HookType

def on_block(ctx):
    print("BLOCKED:", ctx.agent_id, ctx.action)

register_fn(on_block, types=[HookType.ACTION_BLOCKED], name="on-block")
```

### A guard that vetoes

```python
from awcp.agent_hooks import Hook, HookType, HookCategory, HookOutcome, register

class BusinessHoursGuard(Hook):
    name = "business-hours"
    category = HookCategory.GUARD
    subscriptions = (HookType.GATE_EVALUATED,)   # the only guard point
    priority = 5                                  # run before observers

    def handle(self, ctx):
        import datetime as dt
        if ctx.risk == "high" and dt.datetime.now().hour not in range(9, 18):
            return HookOutcome.deny("high-risk writes only allowed 09:00–18:00")
        return HookOutcome.allow()

register(BusinessHoursGuard())
```

> Register custom hooks at startup — add them inside `init_hooks()` in
> `__init__.py`, or `register(...)` from a module the radar imports.

---

## 11. How it's wired into the radar

`src/awcp/radar/api.py` imports the package once (in a `try/except`, like
laminar), then calls `_hook(HookType.X, …)` at each lifecycle point. The `_hook()`
helper is a no-op when the package is absent and swallows any hook error, so
**hooks can never break a radar request.**

| Radar endpoint / callback | Hook(s) fired |
|---|---|
| `POST /agents/register` | `agent_registered` |
| `DELETE /agents/{id}` | `agent_deregistered` |
| `POST /agents/{id}/gate` | `gate_evaluated` (guard) → maybe `action_blocked` |
| `POST /agents/{id}/signal` | `signal_received`, `autonomy_degraded` |
| `POST /tasks/execution/start` | `task_started` |
| `POST /tasks/execution/{id}/event` | `step` + `llm_call`/`tool_call`/`web_search`/`synthesize`, `approval_required`, `token_usage`, `budget_warn`, `budget_exhausted` |
| `POST /tasks/execution/{id}/complete` | `task_completed` / `task_failed` |
| token-breach callback (laminar) | `budget_exhausted`, `autonomy_degraded` |

The **enforcement** for tool blocking is one extra spot: `src/awcp/mcp/server.py`
calls `/agents/{id}/gate` for **every** tool (not just writes), so the
`gate_evaluated` guard reaches reads too.

---

## 12. Guarantees

- **Safe** — every hook runs in a `try/except`; a hook that raises is counted and
  skipped, never propagated to the agent or the radar.
- **Ordered** — hooks fire in ascending `priority` (guards first).
- **Idempotent registration** — re-registering a hook by `name` replaces it, so a
  dev `--reload` (or a re-`POST /hooks/guard`) never stacks duplicates.
- **Observable** — per-hook call/error/deny counts + a recent-events ring buffer
  back the `/hooks` API and the dashboard.
- **No hardcoding** — the guard only compares tool names to the operator's
  deny-list; the deny-list chips come from each agent's real tool catalog.
- **Removable** — deleting this folder disables the whole feature with no other
  edits required.

---

## 13. File map

```
src/awcp/agent_hooks/
├── __init__.py        # public API: init_hooks, dispatch, register, register_fn, router
├── types.py           # HookType (all points), HookContext, HookOutcome, categories
├── base.py            # Hook base class + function adapter
├── manager.py         # HookManager: registry, ordered+isolated dispatch, veto, stats,
│                       #   + configure_guard / guard_config / guard_test (runtime guard)
├── config.py          # env-driven toggles
├── api.py             # FastAPI router: /hooks, /hooks/recent, enable/disable, /hooks/guard[/test]
├── builtin/
│   ├── logging_hook.py    # observer — log every event
│   ├── audit_hook.py      # observer — persistent JSONL audit trail
│   ├── metrics_hook.py    # observer — OTel metrics
│   ├── timing_hook.py     # observer — per-task durations
│   ├── notify_hook.py     # observer — webhook alerts (off by default)
│   └── policy_hook.py     # GUARD — veto ANY tool on a deny-list (off by default)
└── README.md          # this file
```

Related (outside this folder):
- `src/awcp/mcp/server.py` — gates every tool so the guard reaches reads.
- `src/awcp/radar/api.py` — dispatches the lifecycle hooks + the guard veto.
- `ui/src/pages/Hooks.jsx` — dashboard Agent Hooks page.
- `awcp-user-ui/src/components/ResultPanel.jsx` — shows the ⛔ block to the user.
