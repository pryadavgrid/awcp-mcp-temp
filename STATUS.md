# AWCP — Project Status: what's built vs. still concept

The concept brochure ([`docs/Agent-Workforce-Control-Plane-Magazine.html`](docs/Agent-Workforce-Control-Plane-Magazine.html))
describes **10 components**. It labels itself *"Concept architecture, not a production
claim."* This document records how much of that vision is actually implemented in this
repo. **Short version: the governance spine — detect, gate, approve, record — is built
and runs; the self-healing and big-vendor-integration layers are still concept.**

---

## What it is, in one paragraph

You have a fleet of **AI agents** (small programs that take a goal, call an LLM, and use
tools like web search). Normally they run wild — nobody knows they exist, what they may
do, or what they did. **AWCP is the "control tower"** above those agents: it discovers
them, catalogs them, checks every risky action against policy, slows them down when they
misbehave, and writes a tamper-proof receipt for everything.

---

## The folder, in plain terms

| Folder | What it is |
|---|---|
| [`src/awcp/radar/`](src/awcp/radar/) | **The brain.** Detects agents, the registry, the policy gate, approval tokens, degradation ladder, Temporal wiring (~6,000 lines — the bulk of the project) |
| [`src/awcp/opa_agent/`](src/awcp/opa_agent/) | **The hidden risk-judge.** A small local AI that scores how dangerous each tool call is (low→severe) and blocks the bad ones |
| [`src/awcp/context_graph/`](src/awcp/context_graph/) | **The receipt book + smart memory.** Tamper-evident chain of every governed step (+ Neo4j view), plus the **Manager** (relevance / staleness / token-budget) and **Letta** long-term recall |
| [`src/awcp/laminar/`](src/awcp/laminar/) | **The meter.** Tracks token usage/budget per agent |
| [`src/awcp/agent_hooks/`](src/awcp/agent_hooks/) | **The live kill-switch.** Pluggable callbacks; add a tool to a deny-list and it's blocked instantly, no restart |
| [`src/awcp/agents/`](src/awcp/agents/) + [`tools/`](src/awcp/tools/) | The actual worker agents + their tools (web search, arXiv, compute, etc.) |
| [`src/awcp/mcp/`](src/awcp/mcp/) | The "hands" — the FastMCP server that runs each tool call |
| [`src/awcp/gateway/`](src/awcp/gateway/) | Single front door on port `:8000` that mounts everything |
| [`ui/`](ui/) | React dashboard (Radar, Policy, Hooks, Token Monitor, Context Graph, Workflows) |
| [`observability/`](observability/) | Grafana, Prometheus, Tempo, Loki, Postgres (Docker) |
| [`Unsuded_files_folder/`](Unsuded_files_folder/) | Abandoned earlier attempt (old flat Temporal layout) — ignore it |

---

## ✅ Done — the working loop

Every box below is backed by code and runs today via `bash scripts/run_everything.sh`.

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │  AN AGENT RUNS ON ITS OWN  (LangGraph / CrewAI / PydanticAI / arXiv)  │
   │  It does NOT call AWCP. It just exists and does its job.              │
   └───────────────────────────────┬─────────────────────────────────────┘
                                    │
                  ┌─────────────────▼──────────────────┐
                  │  1. RADAR DETECTS IT  (scanner.py)  │   ← scans running
                  │     "a new agent appeared"          │      processes, finds it
                  └─────────────────┬──────────────────┘
                                    │
                  ┌─────────────────▼──────────────────────────┐
                  │  2. REGISTRY CATALOGS IT  (registry/, db)   │
                  │     owner, risk tier, write scopes...       │
                  │     ⚠ under-instrumented? → QUARANTINE       │  ← can't do risky
                  │       (registered but not trusted yet)      │     writes yet
                  └─────────────────┬───────────────────────────┘
                                    │  agent starts a task, calls a tool
                                    ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  3. EVERY TOOL CALL PASSES THROUGH THE GATE                            │
   │                                                                        │
   │   MCP server ──"may I run this?"──▶ RADAR GATE (policy.py / opa.py)     │
   │                                          │                             │
   │                  ┌───────────────────────┼───────────────────┐        │
   │                  ▼                        ▼                   ▼        │
   │        OPA AGENT (hidden SLM)     OPA / Rego rules    failure budget   │
   │        scores risk tier           (policies/...)      / autonomy       │
   │        low│med│high│severe                            ladder check     │
   │                  └───────────────────────┬───────────────────┘        │
   │                                          ▼                             │
   │      DECISION:  allow │ await-token │ await-operator │ DENY            │
   └──────────────────────────────────────────┬───────────────────────────┘
                                    │
              ┌─────────────────────┼──────────────────────────┐
       (allowed)             (high-risk write)              (denied)
              │                     │                            │
              ▼                     ▼                            ▼
   ┌──────────────────┐  ┌──────────────────────┐   ┌────────────────────┐
   │ 4. TOOL RUNS     │  │ EXPIRING APPROVAL     │   │ blocked + reason    │
   │   (MCP server)   │  │ TOKEN issued →        │   │ shown to user       │
   │                  │  │ operator approves →   │   │                     │
   │                  │  │ single-use, consumed  │   │                     │
   └────────┬─────────┘  └───────────┬──────────┘   └─────────┬──────────┘
            │                        │                          │
            └────────────────────────┼──────────────────────────┘
                                     ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  5. METER + RECORD                                                      │
   │   • laminar  → counts tokens / budget                                  │
   │   • context_graph → writes a STAMPED RECEIPT into evidence.ledger      │
   │     (hash-chained, tamper-proof, append-only, with resume pointer)     │
   │     ...and mirrors it into a Neo4j graph                               │
   └──────────────────────────────────┬─────────────────────────────────────┘
                                       ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  6. OPERATOR SEES EVERYTHING                                            │
   │   React dashboard: Radar · Policy · Hooks · Token Monitor ·            │
   │   Context Graph · Workflows      +    Grafana traces/metrics/logs      │
   └──────────────────────────────────────────────────────────────────────┘
```

In one line: *agents are auto-discovered, cataloged, and quarantined if
under-instrumented; every tool call is risk-scored by a small AI and policy-gated;
high-risk writes need a single-use expiring approval token; everything is metered and
written to a tamper-proof ledger; an operator watches it all live.*

### Mapped to the brochure's components

| Magazine component | Status | Where |
|---|---|---|
| Agent Registry & Control Hooks (Step 01) | ✅ Built | [`radar/`](src/awcp/radar/), [`registry/`](src/awcp/registry/) |
| Orchestrate Workflows / Workflow Engine (Step 02) | ✅ Built (Temporal real) | [`radar/temporal/`](src/awcp/radar/temporal/) |
| Gate Write Actions / Approval Gate (Step 03) | ✅ Built | [`radar/opa.py`](src/awcp/radar/opa.py), [`radar/tokens.py`](src/awcp/radar/tokens.py), [`opa_agent/`](src/awcp/opa_agent/), [`policies/`](policies/) |
| Degrade Autonomy Gracefully (Step 04) | 🟡 Partial | [`radar/policy.py`](src/awcp/radar/policy.py) (ladder exists) |
| Replay & Recover / Evidence Ledger (Step 05) | ✅ Built | [`context_graph/`](src/awcp/context_graph/) |
| Context Graph **Manager** (relevance / staleness / token-budget) | ✅ Built | [`context_graph/manager.py`](src/awcp/context_graph/manager.py) |
| Letta long-term memory | ✅ Built (fail-open) | [`context_graph/memory.py`](src/awcp/context_graph/memory.py) |
| Observability hooks | ✅ Built | [`observability/`](observability/), [`radar/telemetry.py`](src/awcp/radar/telemetry.py) |
| LLM Gateway | 🟡 Partial | [`radar/llm_gateway.py`](src/awcp/radar/llm_gateway.py) |
| Token metering (extra, not in brochure) | ✅ Built | [`laminar/`](src/awcp/laminar/) |
| Live hook / kill-switch (extra) | ✅ Built | [`agent_hooks/`](src/awcp/agent_hooks/) |

---

## 🆕 Recently built (closing the gaps)

- **Context Graph Manager — the smart-memory layer** ([`context_graph/manager.py`](src/awcp/context_graph/manager.py)).
  The trail used to only *record* context; now it *reasons* about it:
  - **Relevance scoring** — `GET /context-graph/{wf}/relevance?focus=` scores every
    step 0–1 from explainable components (recency · step kind · outcome · focus overlap).
  - **Stale-context detection** — `GET /context-graph/{wf}/stale` flags context that
    must not seed recovery: **aged**, **superseded** by a newer snapshot, or a
    **dead/blocked branch**.
  - **Token-budget management** — `GET /context-graph/{wf}/working-set?budget=&focus=`
    returns the relevance-ranked, staleness-filtered slice of context that **fits a
    context window**, plus the resume anchor (tiktoken counts, chars/4 fallback).
- **Letta long-term memory** ([`context_graph/memory.py`](src/awcp/context_graph/memory.py)).
  Durable, cross-run recall over Letta's REST API (fail-open, config-driven). Every
  checkpoint is *remembered*; `working-set` and `POST /context-graph/memory/recall`
  *recall* relevant past knowledge from earlier runs. No-op unless a Letta server is
  configured (`AWCP_LETTA_AGENT_ID`), so nothing else changes until you opt in.

---

## ⛔ Not done — still concept

These appear in the brochure but are **not** (or only partially) implemented.

1. **Generate Instrumentation Patches (Step 06) — not started.** The promise: when AWCP
   finds an under-instrumented agent, auto-write a code patch / PR (Codex CLI or Claude
   Code) adding the missing telemetry + policy hooks so it can leave quarantine. **No code
   exists.** Today AWCP *detects* and *quarantines* a sloppy agent, but a human fixes it
   by hand.

2. **CodeAct Sandbox / Modal — not started.** No sandboxed, throwaway-container code
   execution. Agents call pre-written tools; they don't generate-and-run code in a jail.

3. **Handoff Coordinator — essentially not done.** Safe agent-to-agent context transfer
   with session continuity. A `triage.py` agent mentions "handoff," but the dedicated
   coordinator (preserve continuity, isolate failing branches) doesn't exist.

4. **Degrade Autonomy Gracefully — logic exists, not fully alive.** The *ladder* is coded
   ([`radar/policy.py`](src/awcp/radar/policy.py): `active → recommendation_only →
   suspended`, with per-risk failure budgets). The decision *rules* are real; the
   **automatic trigger off live signals** (rising latency, stale context, budget
   breaches → bump sampling, tighten retries, pin a safer model) is thin. *(Note: the
   Context Graph Manager above now supplies the "stale context" signal this would consume.)*

5. **LLM Gateway with real multi-provider failover — partial.** The brochure promises a
   unified gateway over **Anthropic + OpenAI** with failover, rate-limiting, cost-tracking,
   and safer-profile routing during degradation. The real
   [`llm_gateway.py`](src/awcp/radar/llm_gateway.py) works with **local Ollama + NVIDIA
   cloud + Groq** and records which model each step used — but **no Anthropic/OpenAI
   failover and no automatic safer-model switch on degradation.**

6. **Feature flags (OpenFeature / Flipt) — not integrated.** Autonomy modes go through the
   policy ladder instead; no real feature-flag provider is wired in.

7. **"Workflow Intake Proxy" reality is narrower than the pitch.** The brochure describes
   ingesting events from external runtimes via **runtime adapters**. The real mechanism is
   simpler: AWCP **scans OS processes** ([`radar/scanner.py`](src/awcp/radar/scanner.py))
   to discover agents — "find what's running on this machine," not "adapt to any external
   runtime's event stream."

---

> **One-line summary:** the **core governance loop is built and runs** (discover →
> catalog/quarantine → risk-score → policy-gate → expiring approval tokens → tamper-proof
> ledger → **smart context/relevance management + long-term memory** → live dashboard).
> What's **not built** is the **self-healing and enterprise-integration layer** —
> auto-generating instrumentation patches, sandboxed code execution, agent-to-agent
> handoff, fully-automatic autonomy degradation off live signals, and big-vendor
> integrations (Anthropic/OpenAI failover, OpenFeature, Modal).
