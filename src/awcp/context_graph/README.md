# AWCP Context Graph

A durable, tamper-evident **trail of every governed step** an agent takes.

> Plain-English version: every time an AWCP agent does something through the
> control plane, this writes a stamped receipt and staples it to the previous
> one. Follow the staples and you can replay the whole story of a run.

---

## 1. Why this exists (the gap it closes)

The canonical schema (`observability/init-db/02-schema.sql`) defines
`evidence.ledger` with four columns built for exactly this:

| column | meaning |
|---|---|
| `context_hash`   | fingerprint of the state a step acted on |
| `resume_pointer` | opaque cursor — where to resume *after* this step |
| `prev_hash`      | the previous ledger row's hash |
| `row_hash`       | `sha256(prev_hash + this row)` → tamper chain |

But **nothing wrote them.** Worse, the radar's own insert
(`awcp.radar.db._SQL_EVIDENCE`) didn't even list `resume_pointer`. The slots
existed; the receipts were never printed. This package prints them.

It is the first, self-contained slice of the larger **AI harness** design — the
harness's `checkpoint()` method is meant to delegate here once it lands. Nothing
else has to change for this slice to add value.

---

## 2. What a node is

One **node = one governed step** (a tool call, a route decision, a generation).
Consecutive steps in the same run are connected by **edges** (`step N → step N+1`).

```
ContextNode
  ts             when it happened
  workflow_id    the run this step belongs to
  agent_id       who took the step
  task_id        the task within the run
  step           "tool:web_search" | "route" | "generate" | ...
  actor          agent id / "operator" / "gate"
  resume_pointer where to resume after this step (opaque cursor)
  context_hash   fingerprint of the inputs/state it acted on
  prev_hash      previous ledger row hash   ┐ tamper chain
  row_hash       sha256(prev_hash + body)   ┘ (also the node's stable id)
  payload        any extra detail (tool name, gate decision, risk, ...)
```

`row_hash` uses the **same formula** as `awcp.radar.db._row_hash`, so checkpoint
rows extend the *one* evidence chain instead of forking a parallel one.

---

## 3. How it's wired

Two processes are involved. The graph lives in the **radar/gateway** process
(the only one with the Postgres engine); the **MCP server** records over HTTP.

```
MCP server (:8002, separate process)        Radar / Gateway process
────────────────────────────────────        ────────────────────────────────
execute_tool()
  ├─ _radar_gate()      ── allow ──▶
  ├─ run_tool()         (tool runs)
  ├─ _meter_tool_tokens()
  └─ _record_checkpoint() ─ HTTP POST ─▶ /agents/{id}/checkpoint
                                              │  (context_graph.api.router,
                                              │   mounted into the radar router)
                                              ▼
                                         store.record_checkpoint()
                                              ├─ INSERT evidence.ledger
                                              │    (incl. resume_pointer)  ◀── the fix
                                              └─ append in-memory ring (fallback)
```

- The endpoint is mounted into the **radar `APIRouter`** (`awcp.radar.api`), so it
  is served on every surface the radar is (`:8090` standalone **and** the gateway
  `:8000` — which is the port the MCP server's `AGENT_RADAR_URL` actually points
  at).
- Writes go to **Postgres** (durable, survives restart) **and** an in-memory ring
  (instant reads, and the trail still works when Postgres is off).

---

## 4. HTTP API

| Method & path | Purpose |
|---|---|
| `POST /agents/{agent_id}/checkpoint`   | record one step (see `CheckpointRequest`) |
| `GET  /agents/{agent_id}/context-graph`| all steps for one agent |
| `GET  /context-graph/verify`           | re-hash the **whole ledger** and report any break |
| `GET  /context-graph/neo4j/status`     | Neo4j projection connection + node counts |
| `GET  /context-graph/neo4j/graph`      | node-link graph (nodes+edges) for visualization |
| `POST /context-graph/neo4j/backfill`   | mirror existing ledger checkpoints into Neo4j |
| `GET  /context-graph/{workflow_id}`    | one run's ordered nodes **+ edges** |
| `GET  /context-graph`                  | recent steps across all runs (global feed) |

> Route order matters: `/context-graph/verify` is declared **before**
> `/context-graph/{workflow_id}` so "verify" is a literal path, not a workflow id.

`POST` body (`CheckpointRequest`) — only `step` is required:

```json
{
  "step": "tool:web_search",
  "task_id": "task-123",
  "workflow_id": "run-abc",
  "actor": "ollama-search",
  "resume_pointer": "task-123:after:tool:web_search",
  "context": { "query": "..." },
  "payload": { "tool": "web_search", "decision": "allow", "risk": "low" }
}
```

`GET /context-graph/{workflow_id}` returns:

```json
{
  "workflow_id": "run-abc",
  "count": 3,
  "nodes": [ { "...ContextNode..." } ],
  "edges": [ { "from": "<row_hash>", "to": "<row_hash>", "step": "generate" } ]
}
```

---

## 5. Fail-open by design

Nothing here can break a run:

- No `agent_id` / no radar URL → the client is a no-op.
- Postgres down or `AGENT_RADAR_DATABASE_URL` unset → writes fall back to the
  in-memory ring; reads serve from it.
- Any internal error → logged at `debug`, the handler returns `{"ok": false}` (or
  an empty graph), never a 500.

Env knobs: `AWCP_CONTEXT_GRAPH_RING` (ring size, default `5000`).

---

## 6. Verify / demo it

**In the dashboard:** open the React UI (`:5173` / the gateway) and pick
**Context Graph** in the left menu. Runs are listed on the left; click one to see
its governed-step chain on the right — each step shows its gate decision, risk,
resume pointer, and the `context`/`row` hashes (the tamper chain). It polls live.

**From the CLI** — after triggering an agent that calls a tool:

```bash
# global feed of governed steps
curl -s localhost:8000/context-graph | jq

# one agent's trail
curl -s localhost:8000/agents/ollama-search/context-graph | jq

# one run's graph (nodes + edges)
curl -s localhost:8000/context-graph/<workflow_id> | jq

# straight from Postgres — the rows that used to be empty:
#   SELECT ts, agent_id, step, resume_pointer, left(context_hash,12), left(row_hash,12)
#   FROM evidence.ledger WHERE event_type='checkpoint' ORDER BY ts;
```

You can also record a node by hand to see the chain form:

```bash
curl -s -X POST localhost:8000/agents/demo/checkpoint \
  -H 'content-type: application/json' \
  -d '{"step":"route","task_id":"t1","context":{"q":"hi"}}' | jq
```

---

## 7. Chain verification (`GET /context-graph/verify`)

Re-derives the **whole `evidence.ledger` chain** and reports any break — a pure
read (`verify.py`). For each row, ordered by `ts`:

- **content** — `row_hash == sha256(prev_hash + canonical(payload))`. Re-hashing
  the stored payload must reproduce the recorded hash → detects an in-place edit.
- **linkage** — `prev_hash == the previous row's row_hash` → detects a deleted,
  reordered, or inserted row (the append-only property).

```json
{ "enabled": true, "intact": true, "total": 7, "content_verified": 7,
  "breaks": [], "note": "7 row(s) verified, chain intact" }
```

Because `canonical()` matches `radar.db`'s body production exactly, **both**
ordinary evidence rows and checkpoint rows verify under one function. `payload` is
`jsonb` (Postgres-normalised), so content verification assumes the payload
round-trips canonically — true for the control plane's string/scalar payloads;
linkage breaks are always definitive. Returns `{"enabled": false}` when Postgres
is off (nothing durable to verify). The DB enforces this at the privilege level
too: the app role (`awcp_app`) has `INSERT`/`SELECT` on `evidence` but **not**
`UPDATE`/`DELETE` — the ledger is append-only.

## 8. Scope & what's intentionally not here

- ✅ Records a node after each governed tool call — **both successful and blocked**
  (a denial is part of the trail: `payload.outcome="blocked"`, `decision="deny"`,
  red in the UI).
- ✅ Writes the full row incl. `resume_pointer`; reuses the existing hash chain.
- ✅ Read endpoints + whole-ledger `verify` + an in-memory fallback for reads.
- ⛔ Does **not** add the `harness_tier` field or the T0–T4 harness classes — that
  is the separate harness phase. This package is the harness's `checkpoint()`
  duty, isolated and shipped first.

---

## 9. Neo4j graph projection (additive read-model)

Postgres `evidence.ledger` stays the **tamper-evident source of truth**. On top of
it, every checkpoint is also **mirrored into Neo4j** (`graph_store.py`) so the trail
can be queried and visualised as a real graph:

```
(:Agent {id})-[:PERFORMED]->(:Step {row_hash,…})<-[:INCLUDES]-(:Workflow {id})
(:Agent)-[:RAN]->(:Workflow)
(:Step)-[:NEXT]->(:Step)            # chronological lineage within one workflow
(:Step)-[:USED]->(:Tool {name})
(:Step)-[:BLOCKED_BY]->(:Policy {name})   # explainability: which gate rule denied it
(:Step)-[:RAISED]->(:Error {message})     # a failed step (per-step, no hub)
```

`Policy` and `Error` nodes are **derived from the checkpoint payload we already
mirror** (gate `mode` for denials, the error message for failures) — no new event
plumbing. They're kept **sparse** (only blocked/failed steps get them, no shared
`approved_by` hub) so the graph stays readable. This gives the "why was this
blocked?" reasoning chain: `Step ─BLOCKED_BY→ Policy{quarantined}`.

- **Additive + fail-open.** If the `neo4j` driver is missing or the DB is down,
  mirroring is a silent no-op and the graph endpoints return `{"enabled": false}` —
  the ledger and the rest of the app are unaffected. Nothing requires Neo4j.
- **Why a projection, not a move:** Neo4j doesn't give append-only / hash-chain
  tamper-evidence for free; Postgres does (and enforces it at the privilege level).
  So Postgres keeps integrity; Neo4j adds graph queries + the dashboard's **Graph**
  view (`ui/src/components/Neo4jGraph.jsx`).

Run it:

```bash
docker compose -f observability/docker-compose.yml up -d neo4j   # Browser :7474 · Bolt :7687
curl -s -X POST localhost:8000/context-graph/neo4j/backfill | jq  # project existing history (once)
# new checkpoints auto-mirror as they are recorded
```

Config (env): `AWCP_NEO4J_URL` (default `bolt://localhost:7687`), `AWCP_NEO4J_USER`
(`neo4j`), `AWCP_NEO4J_PASSWORD` (`awcpneo4j`), `AWCP_NEO4J_ENABLED` (`true`).
Explore in the Neo4j Browser (`http://localhost:7474`):

```cypher
MATCH (a:Agent)-[:PERFORMED]->(s:Step) RETURN a, s LIMIT 50;
MATCH p=(:Step)-[:NEXT*]->(:Step) RETURN p LIMIT 25;       // lineage chains
```

## 10. Files

| file | role |
|---|---|
| `hashing.py` | `context_hash`, `row_hash`, `canonical` (matches `radar.db` exactly) |
| `models.py`  | `CheckpointRequest`, `ContextNode`, `ContextGraph`, `ChainVerification` |
| `store.py`   | write (Postgres + ring) and read; the `resume_pointer` fix lives here |
| `verify.py`  | whole-ledger chain verification (`GET /context-graph/verify`) |
| `graph_store.py` | Neo4j projection — mirror checkpoints + graph queries (fail-open) |
| `api.py`     | the FastAPI `APIRouter` (mounted into the radar router) |
| `client.py`  | HTTP recorder for the MCP process |
| `__init__.py`| public exports |
