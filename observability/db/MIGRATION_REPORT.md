# JSON → Postgres Migration — Report

_Date: 2026-06-20 · scope: `awcp-mcp-temp/observability/` only · no edits under `src/awcp/`._

## 1. What this migration was
The radar registry ("memory") lived in a flat JSON file
(`agent_radar_registry.json`, written by `src/awcp/radar/store.py`). The
observability stack had a full Postgres schema staged in `init-db/` but **nothing
connected the code/data to it**. This work built the connection layer and moved
the data in.

## 2. What was added (all in `observability/db/`)
- `connection.py` — single DSN resolver (env-driven, container fallback port 55432)
- `migrate_json_to_pg.py` — idempotent JSON → `registry.agents` upsert
- `pg_store.py` — `PgRegistry`, a drop-in Postgres mirror of the JSON `Registry`
- `seed_demo.py` — tagged demo rows for governance/evidence/ops tables
- `verify.py` — port + storage + role + partition checks
- `setup_venv.sh`, `requirements.txt`, `run.sh` — isolated venv + one-shot runner
- `README.md`, `MIGRATION_REPORT.md`

One infra edit: `docker-compose.yml` postgres now also publishes host port
**55432** (canonical 5432 kept) to dodge a local Homebrew postgres on 5432.

## 3. What is working (verified)
- **awcp-postgres** up, schema auto-applied (4 schemas, 10 tables, 4 partitions, 2 roles), 0 errors.
- **Migration**: 3 agents JSON → `registry.agents`; re-run stays at 3 (idempotent).
- **JSON source untouched**: md5 identical before/after.
- **Bridge** (`pg_store.py`) reads them back with correct field mapping.
- **Demo seed**: 9 tables populated, partition routing confirmed (`token_ledger_2026_06`).
- **Durability**: row counts survive a container restart (volume-backed).
- **Ports all PASS**: 5432, 55432, 3000, 9090, 3100, 3200, 4317, 7233, 8080, 5667.

## 4. Advantages this brings
- Concurrent/atomic writes + transactions (JSON file had a single global lock).
- Real queries/indexes over agents, decisions, token spend (GIN on feature_flags, partitioned ledgers).
- Append-only **evidence/audit** trail + **partitioned** token ledger = scalable accounting.
- Shared state across processes (gateway, radar, worker) instead of one file per process.
- Same Postgres already backs Temporal → one durable store for the control plane.

## 5. Connections enhanced
- init-db schema ↔ live JSON data (was orphaned, now populated).
- Radar memory ↔ Postgres via a swappable bridge (no `src/awcp/` edits required).
- Postgres ↔ Temporal (shared instance) ↔ observability stack (one compose).

## 6. What more we can do
- Flip the live radar to `PgRegistry` (one-line `REGISTRY =` swap).
- Add a Grafana datasource on Postgres → dashboards for agents/tokens/policy.
- Auto-create next-month partitions (cron) so ledgers never hit a missing partition.
- Wire `evidence.token_ledger` to the `laminar` token monitor for real cost rows.
- Use `awcp_ro` role for read-only dashboards; keep `awcp_app` for writers.
