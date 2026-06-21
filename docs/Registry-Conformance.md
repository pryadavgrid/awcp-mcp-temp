# AWCP Registry — Conformance to the Canonical Schema

Living document for aligning the temp3 Agent Radar with the **absolute** AWCP
control-plane schema shipped in `observability/init-db` (the same governance model
described in [`Agent-Workforce-Control-Plane-Magazine.html`](./Agent-Workforce-Control-Plane-Magazine.html)).

The magazine defines the durable governance surface as an **Evidence Ledger**
("workflow identity, actor, policy result, approval token, tool call, context
snapshot hash, degradation state, replay trace, rollback pointer"), with
**Approval Tokens** for gated writes and **Graceful Degradation** for autonomy
reduction. The canonical Postgres schema is the concrete form of exactly that, so
conformance = routing the radar's state onto these tables rather than ad-hoc ones.

## Canonical schema (source of truth: `observability/init-db/02-schema.sql`)

| Schema | Tables |
|---|---|
| `registry` | `agents`, `freeze_journal`, `gateway_agents` |
| `governance` | `approval_tokens`, `policy_decisions` (partitioned), `degradation_events` (partitioned) |
| `evidence` | `token_ledger` (partitioned), `ledger` (partitioned, **hash-chained**) |
| `ops` | `onboarding_runs`, `artifacts` |

The schema is owned by `init-db` (psql init scripts run once on a fresh volume) —
**not** by Alembic and **not** by the application. App code only reads/writes; it
never creates or alters tables.

## Progress

### Done — registry persistence → `registry.agents`
`src/awcp/radar/store.py` mirrors the live registry to `registry.agents`. Field
mapping is handled (`AgentEntry.user` → `os_user`, epoch floats ↔ `timestamptz`,
`text[]` / `jsonb`). Persistence is **Postgres-exclusive** — the JSON fallback was
removed: if Postgres is unreachable the registry runs in memory only and writes
nothing to disk (it never falls back to JSON). Only `source='self'` entries
survive a restart.

### Done — Step 1: durable governance events → canonical tables
`src/awcp/radar/db.py` no longer uses a flat `governance_events` table (removed,
along with `alembic.ini` and `migrations/`). The audit-worthy subset of events is
now routed, by kind, to the canonical tables:

| Magazine concept | Event kinds | Canonical table |
|---|---|---|
| Gate Write Actions (denial) | `gate_denied` | `governance.policy_decisions` |
| Graceful Degradation | `degraded`, `autonomy` | `governance.degradation_events` |
| Evidence Ledger | `approved`, `scope_added`, `scope_removed`, `registered`, `announced`, `removed`, `hook_stale`, `telemetry_observed`, `policy_observed`, `flags_observed`, `token_hard_stop`, `token_remote_stop`, `token_recover`, `gateway_bypass` | `evidence.ledger` |

Details:
- **Routing is data-driven** (`_ROUTE` dict + a default table) — adding a route is
  one entry, no new branches. Nothing about the connection (host/port/creds) or
  table names is hardcoded outside `db.py`; the DB URL comes from
  `AGENT_RADAR_DATABASE_URL`.
- **Evidence ledger is hash-chained** per the magazine: each row stores
  `prev_hash` (previous row's hash) and `row_hash` (sha256 over `prev_hash` + the
  canonical row body), giving a tamper-evident append-only trail.
- **NOT NULL columns honored:** `evidence.ledger.workflow_id` falls back to the
  agent id when an event carries no workflow; `policy_decisions.tool_call` /
  `decision` are always supplied. `policy_decisions.risk` / `degradation_events.
  trace_sampling` are numeric columns and are left `NULL` (the risk *tier* string
  lives in the jsonb payload, not the numeric column).
- **Full context is always preserved** in each row's `payload` jsonb; the typed
  columns are populated best-effort (e.g. `to_profile` parsed from the degraded
  detail). The original event kind is stashed under `payload._kind` so the unified
  audit read can recover it.
- **`GET /events/audit`** reads a `UNION ALL` across the three tables and returns
  the same `{ts, agent_id, event_type, payload}` shape as before — the public API
  is unchanged.
- **Fail-open** unchanged: if `AGENT_RADAR_DATABASE_URL` is unset or the canonical
  schema is unreachable, durable logging switches off and the radar runs on the
  in-memory ring (`GET /events`) exactly as before.

### Done — Step 2: approval gate → `governance.approval_tokens`
The operator gate for scope expansion is now durably backed by the canonical
`approval_tokens` table (magazine: *"New write scopes … Human approval"*).

- **Scope add** (`/agents/register` with new `write_scopes`) creates a `pending`
  token: `action_class='scope_expansion'`, `write_scopes`, `context_diff` =
  `{added, previous, new}`, `expires_at = now() + AGENT_RADAR_APPROVAL_TTL`
  (default 7d, env-configurable). Inserted *after* the agent is persisted so the
  `agent_id` FK to `registry.agents` holds.
- **Approve** (`POST /agents/{id}/approve`) sets the open token(s)
  `status='approved'` (`decided_by`, `decided_at`) so the gate does not re-arm.
- **Restart rehydrate** — `registry.agents` has no approval column, so on startup
  the radar re-arms the in-memory gate from any unexpired `pending` tokens
  (`open_approval_agent_ids()`), restoring the cross-restart durability that was
  lost when the registry moved off JSON.

Temp3 tailoring: `AgentEntry.approval_state` is **kept** as the live runtime gate
(so the gate also works with no DB), with the token table as its durable backing
and restart source of truth. Canonical `decision`/`status` CHECK values honored
(`approved`/`denied`); numeric `risk` left NULL (tier string stays in payload).

### Done — Step 3: closed-loop hooks (Gap 3) & TTL reconciler (Gap 4) verified
Now that `last_*_ts` persist in `registry.agents`, the closed loop was verified
end-to-end against the canonical schema, and autonomy degradation was completed
into `governance.degradation_events`.

- **Gap 3 — observation flips** (the bit is earned, not declared):
  - policy ← a `/agents/{id}/gate` call (`_observe_policy`)
  - telemetry ← an execution `signal` / `tasks/execution` event (`_observe_telemetry`)
  - flags ← an execution event carrying flag state (`_observe_flags`)
  - Defaults `AGENT_RADAR_REQUIRE_OBSERVED_TELEMETRY/POLICY=true` (flags `false`) —
    stricter than the README's all-off dev default, but this is what the magazine's
    onboarding gate calls for ("declared **and** observed in execution"). Verified:
    a fresh agent is quarantined until both gate + signal are seen, then goes
    active; `last_policy_ts`/`last_telemetry_ts` persist in `registry.agents`.
- **Gap 4 — TTL reconciler**: `_telemetry_reconciler` drops a proven hook that
  goes silent past `AGENT_RADAR_TELEMETRY_TTL` and re-quarantines, emitting
  `hook_stale` → `evidence.ledger`. Verified end-to-end.
- **Degradation → `degradation_events`** (Step-3 conformance fix): the autonomy
  ladder downgrades now record full transitions. The three emit sites pass
  `from_profile` / `to_profile` / `trigger` (`token_budget`, `failure_budget`,
  `operator`) so rows are complete per the magazine's from→to model. Verified:
  `active → trace_boost`, `trigger=failure_budget`.

Notes / deliberate temp3 simplifications (not changed — flagged):
- One `AGENT_RADAR_TELEMETRY_TTL` governs all three hooks (the README split it into
  per-hook TTLs); a single TTL is sufficient and simpler.
- `hook_stale` (a re-quarantine = trust/status change) routes to `evidence.ledger`,
  not `degradation_events` — the latter is reserved for autonomy-profile downgrades.

### Done — Step 4: token monitor → `evidence.token_ledger`
The laminar `TokenLedger` keeps the live sliding-window / lifetime view in memory
(the monitoring half, unchanged); each call is now also mirrored to the canonical
`evidence.token_ledger` (the magazine's Evidence Ledger token trail).

- `TokenLedger.record()` calls a new `_append_db()` (alongside the existing JSONL
  seam) → `db.record_token_usage()` inserts `{ts, agent_id, task_id, step, model,
  input_tokens, output_tokens, cost}` with the real call timestamp.
- Lazy import (`awcp.radar.db` from `awcp.laminar.ledger`) keeps laminar decoupled
  from the radar DB layer — no import cycle.
- `evidence` is append-only by GRANT, so this is INSERT-only. Fail-open: a no-op
  when no DB is reachable; the in-memory view and budgets are unaffected.
- Verified: two calls land in `evidence.token_ledger` with model/tokens/cost while
  the in-memory lifetime view still reports them; no-DB path still records.

### Done — Step 5: freeze journal & onboarding runs → canonical tables
- **`registry.freeze_journal`** — `_journal_set`/`_journal_clear` persist to the
  canonical table (`record_freeze`/`clear_freeze`, `kind ∈ process|remote`).
  Crash-recovery (`_recover_orphaned_freezes`) reads the journal back from Postgres
  (`load_freezes`) — there is **no on-disk JSON journal** (Postgres-exclusive).
- **`ops.onboarding_runs`** — `record_onboarding_run` upserts run state keyed by
  `workflow_id`: Temporal start → `running` (real wf id); inline completion → `done`
  (stamps `finished_at`), keyed by the real wf id or a stable `inline-<agent>` key.
  `state ∈ pending|running|done` (canonical CHECK).
- Verified: freeze rows upsert + clear; an onboarding run transitions
  `running → done` with `finished_at`; fail-open no-ops without a DB.

Known gap (documented, not a deviation): the Temporal onboarding **worker** marks
its run `done` elsewhere; this file records the `running` start, so a Temporal run
shows `running` until that worker path is wired. The common inline path is fully
recorded.

### Done — Step 6: least privilege + partition maintenance
- **Least privilege**: the app now connects as **`awcp_app`** (DML role from
  `init-db/01-roles.sql`), not the superuser. This makes the canonical guarantees
  real — verified that `awcp_app` is *denied* `DELETE`/`UPDATE` on `evidence.*`
  (append-only) and *denied* DDL. A separate `AGENT_RADAR_DB_ADMIN_URL` (the owner)
  is used ONLY for partition DDL at startup. Both URLs default to the init-db creds
  and are fully env-overridable (`run_everything.sh`).
- **Partition maintenance**: `ensure_partitions()` runs at `init()` via the admin
  connection and creates the current + `AGENT_RADAR_PARTITION_MONTHS_AHEAD` (default
  2) monthly partitions `IF NOT EXISTS` for all four partitioned tables — so inserts
  never hit a missing partition across a month boundary. Dates/identifiers are
  computed from a fixed internal list (not user input). Best-effort: a warning, not
  a crash, if the admin role can't issue DDL.
- Verified: app runs fully as `awcp_app`; partitions `2026_06/07/08` auto-created.

### Done — Step 7: launch wiring + cleanup
- `run_everything.sh` now starts the canonical **`postgres`** service with the rest
  of the stack and **waits for it to be ready** (`pg_isready`, up to 30s) before the
  gateway — so the registry persists to `registry.agents` from the first request
  instead of falling back to JSON. Non-fatal: if Postgres never comes up, the radar
  still runs fail-open.
- The stale ad-hoc `awcp-postgres` container (`:5434`, no schema) and its orphaned
  volume were removed; the compose now owns a single `postgres` on `:5432` with the
  init-db schema.
- Verified end-to-end against the real compose instance: schema auto-applied (4
  schemas), app connects as `awcp_app`, registry persists, partitions present. Test
  volume wiped so the first real launch gets a clean init-db apply.

### Done — Step 8: tests
`tests/radar/` covers the durable conformance layer in two tiers:

- **Pure / fail-open** (`test_db_conformance.py`, `test_store_persistence.py`) — run
  with no database and even without SQLAlchemy installed: routing map, partition
  month-range maths (incl. year rollover), the durable-event whitelist, every write
  as a no-op when no DB, enum guards, and the Postgres-exclusive registry store
  (with no DB it persists nothing — never writes JSON). **11 pass / 11 skip** with no DB.
- **Integration** (skipped unless `AGENT_RADAR_TEST_DATABASE_URL` points at a
  Postgres with the init-db schema) — event routing into the three canonical tables,
  `decision='denied'`, the evidence **hash chain**, audit filters, approval token
  request→decide, `evidence.token_ledger`, freeze upsert/clear, onboarding
  `running→done`, and partition creation. **22 pass** against a throwaway Postgres.

Run: `PYTHONPATH=src pytest tests/radar/` (set `AGENT_RADAR_TEST_DATABASE_URL` to
include the integration tier).

### Done — risk-tier alignment
The Gap-1 `RISK_ORDER` default is now `low,medium,high` (matching the
`registry.agents.risk` CHECK and the magazine). `authoritative_risk` additionally
**clamps** any out-of-set result (e.g. a self-declared `critical` with no magazine
opinion) to the most restrictive known tier — fail-secure — so a resolved tier can
never violate the canonical CHECK. Verified: a declared-`critical` agent resolves to
`high` and persists to `registry.agents` cleanly.

### Done — Temporal onboarding completion
The `admit` activity now records its run `done` in `ops.onboarding_runs` (keyed by
the manager-assigned `workflow_id`, `finished_at` stamped), closing the gap where a
Temporal run previously stayed `running`. Verified end-to-end.

## Remaining
_None — all conformance steps and follow-ups are complete._
