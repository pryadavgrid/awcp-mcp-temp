# AWCP Observability Stack

Local docker-compose stack for metrics, traces, logs, workflow orchestration, and LLM observability.

## Services

| Service | Purpose | UI / Port |
|---|---|---|
| `otel-collector` | Receives OTLP from Python apps, fans out to Tempo/Loki/Prometheus | gRPC `4317`, HTTP `4318` |
| `prometheus` | Metrics storage | http://localhost:9090 |
| `tempo` | Distributed traces storage | `3200` |
| `loki` | Logs storage | `3100` |
| `grafana` | Dashboards (admin / awcp1234) | http://localhost:3000 |
| `postgres` | App DB for the AWCP registry/governance/evidence/ops schema | `5432` |
| `temporal` + `temporal-ui` | Workflow orchestration | UI http://localhost:8080 |
| `lmnr-*` | Self-hosted Laminar LLM observability (clickhouse, quickwit, query-engine, app-server, frontend) | UI http://localhost:5667 |

> Note: `lmnr-postgres` is currently commented out in `docker-compose.yml`. `lmnr-app-server` / `lmnr-frontend` no longer depend on it — wire up a Postgres for Laminar yourself if you need that stack fully functional.

## Start / stop

```bash
docker compose -f observability/docker-compose.yml up -d
docker compose -f observability/docker-compose.yml down       # keep volumes
docker compose -f observability/docker-compose.yml down -v    # also wipe volumes
docker compose -f observability/docker-compose.yml logs -f
```

## Database schema (`init-db/`)

`postgres` mounts `./init-db` at `/docker-entrypoint-initdb.d`. Postgres runs every `*.sql` file there **once, in filename order, only on a brand-new (empty) data volume**:

- `01-roles.sql` — creates the `awcp_app` (read/write) and `awcp_ro` (read-only) roles referenced by the schema's GRANTs.
- `02-schema.sql` — creates the `registry`, `governance`, `evidence`, `ops` schemas, all tables/indexes, the June 2026 partitions, and grants.
- `test-schema.sql` — exercises every table with a minimal valid insert inside a transaction that ends in `ROLLBACK`, so it's safe to re-run anytime without leaving data behind.

### Applying schema changes

Because init scripts only run on a fresh volume, picking up changes to `01-roles.sql` / `02-schema.sql` means recreating the volume (**destroys existing data in it**):

```bash
docker compose rm -f -s postgres
docker volume rm observability_postgres_data
docker compose up -d postgres
```

Then confirm it applied cleanly:

```bash
docker compose logs postgres | grep -iE "error|CREATE|GRANT"
```

### Schema layout

- `registry.agents` — discovered/onboarded agents, autonomy profile, risk, capabilities.
- `registry.freeze_journal` — record of frozen agents/processes.
- `registry.gateway_agents` — agents exposed through the gateway, keyed by route.
- `governance.approval_tokens` — operator approval tokens for gated actions.
- `governance.policy_decisions` *(partitioned by `ts`)* — every policy engine decision.
- `governance.degradation_events` *(partitioned by `ts`)* — autonomy profile downgrades.
- `evidence.token_ledger` *(partitioned by `ts`)* — per-call token/cost accounting.
- `evidence.ledger` *(partitioned by `ts`)* — append-only audit trail of workflow events.
- `ops.onboarding_runs` — onboarding workflow state per agent.
- `ops.artifacts` — stored artifact references (logs, outputs, etc).

Partitioned tables only have a `2026_06` partition so far — add new monthly partitions as needed, e.g.:

```sql
CREATE TABLE evidence.ledger_2026_07 PARTITION OF evidence.ledger
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
```

### Testing the schema

Run the full smoke test (inserts + selects against all 10 tables, then rolls back):

```bash
docker exec -i awcp-postgres psql -U awcp -d awcp < observability/init-db/test-schema.sql
```

Or connect interactively:

```bash
docker exec -it awcp-postgres psql -U awcp -d awcp
\dt registry.*  \dt governance.*  \dt evidence.*  \dt ops.*
\d registry.agents
```

To test a single table, copy its `INSERT`/`SELECT` block out of `test-schema.sql` and run with `COMMIT` instead of `ROLLBACK` if you want the row to persist.
