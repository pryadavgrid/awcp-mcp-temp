# AWCP Registry — JSON → Postgres migration toolkit

Migrates the radar's old **JSON memory** (`agent_radar_registry.json`) into the
**Postgres** schema defined in `../init-db/` (`registry` / `governance` /
`evidence` / `ops`). Fully self-contained: it has its **own venv** and does
**not** modify any code under `src/awcp/` — wiring the live radar to Postgres is
left as a one-line opt-in (see *Cut over* below).

## Files

| File | Purpose |
|---|---|
| `connection.py` | One place that resolves the Postgres DSN from env (`AWCP_PG_*` / `POSTGRES_*` / `DATABASE_URL`). Defaults to the container on host port **55432**. |
| `migrate_json_to_pg.py` | Reads the JSON memory, maps fields, **UPSERTs** into `registry.agents`. Idempotent, read-only against the JSON. |
| `pg_store.py` | `PgRegistry` — a Postgres-backed drop-in mirror of `radar/store.py:Registry` (`all/get/patch/register/remove/reconcile_scan`). The actual JSON→PG bridge. |
| `seed_demo.py` | Seeds clearly-tagged demo rows for the tables JSON never had (governance/evidence/ops). All tagged `payload.demo=true`. |
| `verify.py` | Checks service ports + every table + roles + partition routing. PASS/WARN/FAIL. |
| `setup_venv.sh` / `requirements.txt` | Isolated venv with `psycopg[binary]`. |
| `run.sh` | `migrate` / `seed` / `verify` / `store` / `all`. |

## Quick start

```bash
# 0. postgres must be up (auto-applies init-db schema on a fresh volume)
docker compose -f observability/docker-compose.yml up -d postgres

# 1. one-shot: venv + migrate + seed + verify
bash observability/db/run.sh all
```

## The port-55432 thing

`docker-compose.yml` publishes awcp-postgres on **both** `5432` (canonical) and
`55432` (fallback). On macOS a local/Homebrew postgres often already owns
`localhost:5432`, which would shadow the container. The toolkit defaults to
`55432` so it always talks to the container. To use the canonical `5432`
instead, stop the local one (`brew services stop postgresql@16`) and set
`AWCP_PG_PORT=5432`.

## Field alignment (JSON → SQL)

- JSON `user` → column **`os_user`** (the only true rename)
- epoch floats → `timestamptz` (`first_seen`, `last_seen`, `last_*_ts`)
- `list[str]` → `text[]`; `feature_flags` dict → `jsonb`
- `created_at` / `updated_at` → schema defaults (not in JSON)

## Cut over the live radar (optional, not done here)

`pg_store.py` exposes the same API as `radar/store.py:Registry`. To make the
running radar use Postgres, point its singleton at `PgRegistry` — a one-line
swap, no other edits. Left opt-in so nothing under `src/awcp/` changes until you
choose to flip it.

## Remove demo rows

```sql
DELETE FROM governance.policy_decisions   WHERE payload->>'demo' = 'true';
DELETE FROM governance.degradation_events WHERE payload->>'demo' = 'true';
DELETE FROM evidence.ledger               WHERE payload->>'demo' = 'true';
DELETE FROM evidence.token_ledger         WHERE task_id LIKE 'demo-%';
```
