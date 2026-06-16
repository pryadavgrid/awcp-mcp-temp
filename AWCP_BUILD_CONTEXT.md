# AWCP — Build Context & Handoff

> Resume-from-any-chat context for the Agent Workforce Control Plane work.
> Primary project: `telemetry-included/awcp-mcp-temp2` (the radar / control plane).
> Reference docs: `docs/AWCP_Magazine_vs_temp2.html` (status audit + diagrams),
> `docs/AWCP_Architecture_Flow.html`, `docs/Agent-Workforce-Control-Plane-Magazine.html` (the spec).

---

## 1. What AWCP is

A control plane that sits **above** existing agent runtimes. It registers/onboards agents,
gates their write actions, accounts for tokens, degrades autonomy gracefully on failure,
hard-stops agents that exceed their token budget, and records everything as telemetry.
The design target is the **AWCP Magazine** 6-step Operating Model.

## 2. Folder map (siblings under `Downloads/files/`)

| Folder | Role | State |
|---|---|---|
| `telemetry-included/awcp-mcp-temp2` | **MAIN** — radar/control plane (the magazine build) | active |
| `awcp_agents` | Earlier build; has an in-process token *instant-block* guard (`runtime/laminar_guard.py`) | separate |
| `awcp-opa` | Standalone OPA prototype (Rego policies + 11 passing `opa test` + a partial FastAPI PDP) | **parked** |
| `awcp-llm-stack` | docker-compose: Ollama + OTel Collector + Langfuse + Laminar (self-host) + a Langfuse SDK shim | **scaffolded, not run** |

**Sibling detail (so you don't re-derive it):**
- `awcp_agents` — `runtime/laminar_guard.py` is an *instant-block* token guard: it raises `TokenBudgetExceeded`
  before/after each `ask_ollama` call and hard-blocks **in-process**. A different model from temp2's radar
  (which *observes* and *acts* from outside). Wired into `service.py`, `mcp/server.py`, the Temporal workflow.
- `awcp-opa` — `policy/awcp/{authz,tokens}.rego` (allow/deny + JWT approval-token verify via `io.jwt`),
  `authz_test.rego` = **11 passing `opa test`**, `src/awcp_opa/{config,models}.py` done. Still to write:
  `service.py` (FastAPI PDP), `tokens.py` (JWT mint), `opa_client.py`, scripts, README. Bundled `bin/opa` 1.17.1.
- `awcp-llm-stack` — `docker-compose.yml` (≈13 containers), `otel-collector/config.yaml` (OTLP fan-out to
  Langfuse + Laminar), `.env.example`, `integration/langfuse_tracing.py` (SDK shim) + `example_traced_call.py`.
  Compose **syntax-validated** (`docker compose config`); never brought up (heavy). DS side ≈half (emission done,
  Langfuse SDK enrichment left); the Laminar path is essentially done on the DS side.

## 3. Magazine status — overall ≈59% (mean of 6 steps)

| Step | Status | % | Note |
|---|---|---|---|
| 01 Register & attach hooks | ✅ Complete | 100 | registry + quarantine + **3 hooks observed-in-execution** + declared-scope gate |
| 02 Orchestrate workflows | 🟡 Partial | 65 | Temporal driving done; ingest/normalize *existing* foreign workflows not done |
| 03 Gate write actions | 🟡 Partial | 45 | gate + scope done; **OPA + expiring approval tokens MISSING** |
| 04 Degrade autonomy gracefully | ✅ Complete | 100 | full 6-rung ladder + directives + live delivery, +forceful hard-stop extension |
| 05 Replay & recover | 🟡 Partial | 40 | Temporal history + events ring; **durable Evidence Ledger + checkpoints MISSING** |
| 06 Generate instrumentation patches | 🔴 Not built | 0 | only the trigger (quarantine) exists |

## 4. What was built this session (temp2)

### A. Onboarding hooks "observed in execution" (Step 01)
The magazine requires telemetry, policy callbacks, and flag wiring **observed in execution**
(not merely declared). All three now use the same closed loop:
- `_observe_telemetry` (execution events/signals arriving), `_observe_policy` (agent calls the
  gate = consults policy), `_observe_flags` (an event carries `extra.feature_flags`).
- Each sets an `*_observed` flag + `last_*_ts`, then re-runs `decide_status` to auto-promote out
  of quarantine; the hook reconciler re-quarantines if a *required* hook goes silent past the TTL.
- Per-hook env toggles: `AGENT_RADAR_REQUIRE_OBSERVED_{TELEMETRY,POLICY,FLAGS}`.
  Defaults: telemetry=true, policy=true, **flags=false** (flags need the agent to report state, so
  trust-on-declare by default to avoid penalising agents that don't report flags yet).

### B. Declared-write-scope gate (Step 01)
`evaluate_action(... scope=...)` denies a write whose scope is not in the agent's declared
`write_scopes` (`out_of_scope`), regardless of autonomy. `GateRequest.scope` is optional →
no scope supplied = no check (backward-compatible). Quarantine still takes precedence.

### C. Full degradation ladder (Step 04)
`DEFAULT_PROFILE_LADDER` = `active → trace_boost → throttled → safe_profile → recommendation_only → suspended`.
- `STAGE_SPECS` gives each rung its directives: `trace_sampling / max_retries / max_concurrency /
  profile / writes / hard_stop`.
- The **graceful rungs (trace_boost/throttled/safe_profile) stay write-capable**; writes are blocked
  only from `recommendation_only` (`_write_block_index`); last rung = hard stop.
- Directives delivered **live** on the gate / `/signal` / `/tasks/execution/*/event` responses and on
  `GET /agents` (`effective_stage`); degraded agents' steps are captured as `degraded_step` evidence
  ("increase trace sampling"). Backward-compatible with legacy/custom ladders.

### D. Token control + the layered hard stop (Step 04 + extension)
Two coexisting layers:
- **Graceful (durable):** a token breach steps autonomy one rung down (`_on_token_breach`).
- **Hard stop (live, self-healing):** while over budget, the control plane blocks execution:
  1. **Gate deny** — `_token_blocked` denies ALL gate actions for an exhausted agent.
  2. **Governed-exec block** — `/tasks/execution/start` refuses; mid-flight `/event` terminates the workflow.
  3. **Process SIGSTOP** — `_token_suspend_process` freezes a local agent's OS process; `_token_resume_process` SIGCONTs it on recovery.
  4. **Remote webhook** — `_token_stop_remote` POSTs `suspend` to a registered `control_endpoint`.
  5. **LLM gateway** — `radar/llm_gateway.py` (`/llm`) proxies model calls and **429s an over-budget agent at the call**.
  6. **Crash-recovery journal** — every freeze written to disk; orphaned freezes resumed on radar startup.
- Dispatcher `_token_enforce_stop` picks local-PID → remote-endpoint → else flags `token_uncontrolled`.

### E. PID recovery (the real-world fix)
`_proc_for_entry` recovers the agent's real PID from the `proc-<pid>-<ts>` id when the `pid` field is
empty, verifying process start-time against the id timestamp (PID-reuse safe), never signalling the
radar's own PID. This is what made SIGSTOP actually bite the live `arxiv_agent` (which had `pid: null`).

### F. Laminar additions
`budget_state(agent_id)` + `is_exhausted(agent_id)` = authoritative LIVE budget query (no stored flag).
`record_usage(...)` = meter one model call at the source (used by the gateway) and run the same
breach chain. Laminar stays independent; the radar injects `get_agent / on_breach / record_event`.

### G. Agent lifecycle (end to end)
1. **Detected** by the scanner (psutil/lsof, framework-agnostic) **or self-registers** (`POST /agents/register`)
   → lands `quarantined`, fail-closed.
2. **Onboarding** (Temporal or inline fallback): `map_identity_patch` → `quarantine_check` (`decide_status`)
   → `link_mcp` (enumerate tools) → admit.
3. **Quarantined but visible**: reads allowed, writes blocked. Leaves quarantine only once all three hooks are
   *observed in execution* — telemetry arriving, the agent calling the gate (policy), the agent reporting flag
   state (flags; default trusts declared).
4. **Active**: governed writes allowed, but only within declared `write_scopes`.
5. **Failure/token signals** step it down the 6-rung ladder gracefully, delivering stage directives each step.
6. **Over budget** → live hard stop (gate-deny → SIGSTOP / remote webhook / gateway 429) **plus** a one-rung
   graceful degrade — the two layers coexist.
7. **Recovery**: window clears or operator resets (`POST /laminar/reset/{id}` + `POST /agents/{id}/autonomy`)
   → SIGCONT / un-block; the reconcilers keep state in sync (~10–15s).
8. **Radar restart**: orphaned freezes resumed from the journal; persisted self-registered agents reload
   (scanned ones re-detected). NOTE: code changes need a restart (see §9).

### H. Gate decision order
The `/agents/{id}/gate` endpoint then `evaluate_action` check, in order, for a **write**:
**token hard stop** (exhausted ⇒ deny ALL, even reads) → **quarantine** (deny) → **declared scope**
(`out_of_scope` ⇒ deny) → **hard-stop rung** (last ladder rung ⇒ deny) → **write-block rung**
(`recommendation_only`+ ⇒ deny) → **graceful write-capable rung** (allow + directives) → **active** (allow).
Reads are allowed except under the token hard stop. Calling the gate also fires `_observe_policy`.

## 5. Key files (temp2)

| File | What |
|---|---|
| `src/awcp/radar/api.py` | radar app: register, gate, signal, `/tasks/execution/*`, observe_* hooks, hook reconciler, token hard-stop helpers, SIGSTOP/remote/journal, lifespan, gateway mount |
| `src/awcp/radar/policy.py` | ladder (`STAGE_SPECS`, `stage_effects`, `_write_block_index`), `evaluate_action` (scope + write-capable rungs), `apply_signal`, budgets by risk |
| `src/awcp/radar/onboarding.py` | `decide_status` (3 hooks declared+observed), `map_identity_patch`, `link_mcp` |
| `src/awcp/radar/models.py` | `AgentEntry` (telemetry/policy/flags `_observed` + `_ts`, `control_endpoint`, scopes, ladder…) |
| `src/awcp/radar/llm_gateway.py` | token-aware Ollama-compatible proxy (`/llm`), fail-open/fail-safe |
| `src/awcp/radar/store.py` | registry: in-memory + JSON persist, scan reconcile, prune |
| `src/awcp/laminar/bridge.py` | ledger tap + budget eval + breach; `budget_state` / `is_exhausted` / `record_usage` |
| `src/awcp/laminar/{ledger,budget,exporter,api}.py` | sliding-window ledger, positional budget, OTel/Laminar export, `/laminar/*` UI |

### Radar API surface (`:8090`)
- `GET /agents`, `GET /agents/{id}` — each row carries `effective_budget`, `effective_ladder`, `effective_stage`.
- `POST /agents/register` — body: name, kind, runtime, owner, write_scopes, feature_flags, policy_callbacks,
  telemetry_enabled, risk, endpoint/transport, `control_endpoint`, autonomy_ladder, failure_budget.
- `POST /agents/{id}/gate` — `{action, write, scope}` → `{decision, mode, reason, stage, status, autonomy_profile}`.
- `POST /agents/{id}/signal` — `{ok, reason}` → degrades on failure; returns `effective_stage`.
- `POST /agents/{id}/autonomy` `{profile}` · `POST /agents/{id}/risk` `{risk, token_budget}` · `DELETE /agents/{id}`.
- `POST /tasks/execution/start` · `/tasks/execution/{id}/event` · `/tasks/execution/{id}/complete`.
- `GET /events` (audit ring) · `GET /healthz`.
- `/{...}` under `/llm` — the token-aware model gateway. `/laminar/{status,usage,budgets,policy,reset,ui}`.

### Audit event kinds (`GET /events`)
`registered`, `onboarded`, `gate`, `degraded`, `signal`, `autonomy`, `risk`, `removed`,
`telemetry_observed`, `policy_observed`, `flags_observed`, `hook_stale`, `degraded_step`,
`token_hard_stop`, `token_process_stop`, `token_process_resume`, `token_remote_stop`,
`token_remote_stop_failed`, `token_remote_resume`, `token_uncontrolled`, `token_recover`
(+ laminar's `token_warn` / `token_exhausted` / `token_reset`).

## 6. Configuration (env — nothing hardcoded)

- Hooks: `AGENT_RADAR_REQUIRE_OBSERVED_TELEMETRY` (t), `_POLICY` (t), `_FLAGS` (f);
  `AGENT_RADAR_TELEMETRY_TTL` (300), `AGENT_RADAR_TELEMETRY_RECONCILE_INTERVAL` (15).
- Ladder/budget: `AGENT_RADAR_LADDER` (the 6-rung default), `AGENT_RADAR_FAILURE_BUDGET` (3),
  `AGENT_RADAR_RISK_BUDGET` (`low:5,medium:3,high:1`).
- Hard stop: `AGENT_RADAR_TOKEN_PROCESS_STOP` (true), `AGENT_RADAR_TOKEN_PROCESS_INTERVAL` (10),
  `AGENT_RADAR_TOKEN_CONTROL_TIMEOUT` (2), `AGENT_RADAR_FREEZE_JOURNAL` (path).
- Gateway: `AWCP_GATEWAY_UPSTREAM`/`OLLAMA_BASE`, `AWCP_GATEWAY_REQUIRE_AGENT` (false),
  `AWCP_GATEWAY_TIMEOUT` (300), `AWCP_GATEWAY_CONN_TTL` (2).
- Laminar: `LMNR_TOKEN_BUDGET`, `LMNR_RISK_TOKEN_BUDGET`, `LMNR_BUDGET_WINDOW_S`, `LMNR_WARN_RATIO`,
  `LMNR_ENABLED`, `LMNR_OTLP_ENDPOINT`, `LMNR_PROJECT_API_KEY`, `LMNR_LEDGER_PATH`.
- OTel: `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`.

## 7. Known limitations / honest boundaries

- **Token enforcement is post-hoc at call granularity** — a single model call's token count is only
  known after it returns, so the call that crosses the budget completes; the NEXT call is blocked
  (gateway 429) or the process is frozen. Overshoot ≈ one model-call's worth of tokens.
- **SIGSTOP works for LOCAL agents** with a resolvable PID; remote/containerized agents need the
  gateway (point their model URL at `/llm`) or a `control_endpoint`. Source-PID identity in the
  gateway is local + needs psutil privilege.
- **Cooperative boundary** — the gate, directive honouring, and webhook suspend require the agent to
  cooperate (call the gate / honour responses / expose an endpoint), inherent to a control plane that
  doesn't run the agent's loop. The gateway + SIGSTOP are the non-cooperative fallbacks.
- **Feature-flag observation defaults OFF** (needs the agent to report flag state).

## 8. What's left to build (roadmap to full magazine)

1. **Step 03 — OPA policy-as-code + expiring approval tokens.** Biggest gap. The `awcp-opa/`
   prototype (Rego + tests done; PDP service partial) is the seed; not integrated into the radar gate.
2. **Step 05 — durable Evidence Ledger + checkpoints/rollback.** Today: Temporal history + in-memory
   events ring + optional laminar JSONL = fragments, not the unified replayable ledger.
3. **Step 06 — instrumentation-patch generation** (auto telemetry/flag/policy PR for quarantined agents).
4. **Step 02 — ingest/normalize existing foreign-runtime workflows** + branch checkpoints.
5. (Optional) enforce feature-flag observation; runtime *honouring* of ladder directives
   (live OTel sampler / Temporal retry-concurrency caps).

## 9. Operational notes (IMPORTANT)

- **The radar must be RESTARTED to load code changes** — it runs `uvicorn awcp.radar.api:app`
  WITHOUT `--reload` (via `scripts/run_radar.sh` it has `--reload`; via `run_all.sh` it does not).
  Several "it still doesn't work" moments this session were a stale radar process running old code.
- Live radar on `:8090`. Useful: `GET /agents`, `/agents/{id}/gate`, `/agents/{id}/signal`,
  `/tasks/execution/*`, `/laminar/usage`, `/laminar/ui`, `/llm/...`, `/events`, `/healthz`.
- Restart cleanly: `PYTHONPATH=src ./.venv/bin/uvicorn awcp.radar.api:app --host 0.0.0.0 --port 8090`.
- **Verify changes** without standing up the stack: `python -m py_compile src/awcp/radar/*.py`, then drive the
  app via `fastapi.testclient.TestClient(awcp.radar.api.app)` with `OTEL_ENABLED=false` and a temp
  `AGENT_RADAR_DB` — this is how every feature this session was tested (gate/scope, observed hooks, ladder,
  token hard-stop, SIGSTOP against a real `/bin/sleep` subprocess, crash recovery). Use the project `.venv`.
- **Enforcement is post-hoc at call granularity**: a model call's token cost is known only after it returns,
  so the call that crosses the budget completes; the *next* call is blocked (gateway 429) or the process is
  frozen. The clean pre-spend wall is the LLM gateway (`/llm`); SIGSTOP is the non-cooperative fallback.

## 10. Reference docs in this repo

- `docs/AWCP_Magazine_vs_temp2.html` — the status audit: overall %, step-by-step, whole-platform
  architecture diagram (built vs left), per-step detail, core-stack grid, extensions.
- `docs/AWCP_Architecture_Flow.html` — the layered runtime architecture + flows.
- `docs/Agent-Workforce-Control-Plane-Magazine.html` — the spec (the 6-step Operating Model).
