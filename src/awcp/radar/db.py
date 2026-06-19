"""Durable governance events → the canonical AWCP schema (Postgres) — fail-open.

The recent-decisions ring buffer in api.py is live but VOLATILE: every governance
decision is lost on restart. This module mirrors the audit-worthy subset into the
*canonical* control-plane schema defined in observability/init-db — the same
"evidence ledger / policy decisions / degradation events" the AWCP magazine
describes — so approvals, scope changes, demotions and gate denials survive a
restart and are queryable for replay.

Routing (magazine vocabulary → canonical table):
  * gate DENY                        -> governance.policy_decisions   (decision)
  * autonomy reduction / change      -> governance.degradation_events (from→to)
  * everything else durable          -> evidence.ledger               (append-only,
                                        hash-chained prev_hash/row_hash)

The schema is owned by init-db (NOT this module and NOT Alembic) — init() only
*verifies* the tables exist; it never creates or alters them. Fail-open: if
AGENT_RADAR_DATABASE_URL is unset, the driver is missing, or the DB / schema is
unreachable, record() becomes a no-op and query() returns [] — the radar keeps
running on the in-memory ring exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any

log = logging.getLogger("awcp.radar")

DATABASE_URL = os.getenv("AGENT_RADAR_DATABASE_URL", "").strip()
# Admin connection used ONLY for DDL the least-privileged app role can't do —
# namely creating monthly partitions. Falls back to DATABASE_URL (fine when the
# app connects as a superuser; in least-privilege mode point it at the owner).
DB_ADMIN_URL = os.getenv("AGENT_RADAR_DB_ADMIN_URL", "").strip() or DATABASE_URL
# Partitioned canonical tables (schema, parent). Monthly partitions are created
# ahead of time so inserts never hit a missing partition across a month boundary.
PARTITIONED_TABLES: tuple[tuple[str, str], ...] = (
    ("evidence", "ledger"),
    ("evidence", "token_ledger"),
    ("governance", "policy_decisions"),
    ("governance", "degradation_events"),
)
PARTITION_MONTHS_AHEAD = int(os.getenv("AGENT_RADAR_PARTITION_MONTHS_AHEAD", "2"))

# The audit-worthy subset of governance event kinds. Anything else (per-step
# telemetry chatter, "gate allow", liveness) stays in the in-memory ring only.
DURABLE_EVENT_TYPES: frozenset[str] = frozenset({
    "approved", "registered", "announced", "removed",
    "scope_added", "scope_removed",
    "degraded", "autonomy",          # autonomy reduction / operator change
    "hook_stale",                    # a hook went silent
    "telemetry_observed", "policy_observed", "flags_observed",
    "gate_denied",
    "token_hard_stop", "token_remote_stop", "token_recover",
    "gateway_bypass",
})

# event kind -> canonical table. Anything not listed falls to the evidence ledger.
# Data-driven so a new routing is one dict entry, not new code.
_ROUTE: dict[str, str] = {
    "gate_denied": "policy_decisions",
    "degraded": "degradation_events",
    "autonomy": "degradation_events",
}
_DEFAULT_TABLE = "evidence_ledger"

# A row's original event kind is stashed under this payload key so the unified
# audit read can recover it for tables that have no dedicated event_type column.
_KIND_KEY = "_kind"

_lock = threading.Lock()
_enabled = False
_engine = None
_text = None
_initialized = False

# Inserts, parameterized; arrays/jsonb handled by the driver. No table/column is
# hardcoded anywhere else — these are the single source of truth for writes.
_SQL_EVIDENCE = (
    "INSERT INTO evidence.ledger "
    "(workflow_id, agent_id, event_type, actor, policy_result, step, "
    " context_hash, degradation_state, prev_hash, row_hash, payload) "
    "VALUES (:workflow_id, :agent_id, :event_type, :actor, :policy_result, :step, "
    " :context_hash, :degradation_state, :prev_hash, :row_hash, CAST(:payload AS jsonb))"
)
_SQL_POLICY = (
    "INSERT INTO governance.policy_decisions "
    "(agent_id, workflow_id, tool_call, risk, decision, scope, reason, token_id, payload) "
    "VALUES (:agent_id, :workflow_id, :tool_call, :risk, :decision, :scope, :reason, "
    " :token_id, CAST(:payload AS jsonb))"
)
_SQL_DEGRADE = (
    "INSERT INTO governance.degradation_events "
    "(agent_id, workflow_id, from_profile, to_profile, trigger, trace_sampling, reason, payload) "
    "VALUES (:agent_id, :workflow_id, :from_profile, :to_profile, :trigger, "
    " :trace_sampling, :reason, CAST(:payload AS jsonb))"
)


def _build() -> bool:
    """Construct the engine and verify the canonical tables exist. Returns True
    on success; any failure leaves durable logging disabled (logged once)."""
    global _engine, _text, _enabled

    if not DATABASE_URL:
        log.info("radar.db disabled (AGENT_RADAR_DATABASE_URL unset) — events stay in-memory only")
        return False
    try:
        from sqlalchemy import create_engine, text
    except Exception as exc:  # noqa: BLE001 — SQLAlchemy not installed
        log.info("radar.db disabled (sqlalchemy unavailable: %r)", exc)
        return False
    try:
        engine = create_engine(
            DATABASE_URL, pool_pre_ping=True, pool_recycle=300,
            connect_args={"connect_timeout": 3}
            if DATABASE_URL.startswith(("postgresql", "postgres")) else {},
        )
        with engine.connect() as c:           # require the canonical schema (init-db)
            c.execute(text("SELECT 1 FROM evidence.ledger LIMIT 1"))
            c.execute(text("SELECT 1 FROM governance.policy_decisions LIMIT 1"))
            c.execute(text("SELECT 1 FROM governance.degradation_events LIMIT 1"))
        _engine, _text, _enabled = engine, text, True
        log.info("radar.db enabled — durable governance events -> canonical schema at %s",
                 DATABASE_URL.split("@")[-1])
        return True
    except Exception as exc:  # noqa: BLE001 — DB down / missing schema / driver — fail open
        log.warning("radar.db init failed (%r) — durable events disabled, in-memory only", exc)
        return False


def _month_ranges(months_ahead: int) -> list[tuple[str, str, str]]:
    """[(suffix 'YYYY_MM', start 'YYYY-MM-01', end 'next-month-01'), ...] for the
    current month plus `months_ahead` following months. Computed, not hardcoded."""
    from datetime import date
    out: list[tuple[str, str, str]] = []
    y, m = date.today().year, date.today().month
    for _ in range(max(0, months_ahead) + 1):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append((f"{y:04d}_{m:02d}", f"{y:04d}-{m:02d}-01", f"{ny:04d}-{nm:02d}-01"))
        y, m = ny, nm
    return out


def ensure_partitions(months_ahead: int | None = None) -> None:
    """Create the current + upcoming monthly partitions for every partitioned table
    (IF NOT EXISTS), using the admin connection. The least-privileged app role can't
    issue DDL, so this is the one place that needs owner/superuser rights. No-op /
    warning on failure — operators can also create partitions by hand (see README).
    Identifiers and bounds come from a fixed internal list + computed dates, so the
    interpolation below is not user-controlled."""
    url = DB_ADMIN_URL
    if not url:
        return
    n = PARTITION_MONTHS_AHEAD if months_ahead is None else months_ahead
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(url, connect_args={"connect_timeout": 3}
                            if url.startswith(("postgresql", "postgres")) else {})
        with eng.begin() as c:
            for schema, table in PARTITIONED_TABLES:
                for suffix, start, end in _month_ranges(n):
                    c.execute(text(
                        f"CREATE TABLE IF NOT EXISTS {schema}.{table}_{suffix} "
                        f"PARTITION OF {schema}.{table} "
                        f"FOR VALUES FROM ('{start}') TO ('{end}')"
                    ))
        eng.dispose()
        log.info("radar.db partitions ensured (current + %d month(s) ahead)", n)
    except Exception as exc:  # noqa: BLE001 — maintenance is best-effort
        log.warning("radar.db ensure_partitions failed (%r) — create monthly "
                    "partitions manually if inserts start failing", exc)


def init() -> bool:
    """Idempotently initialize the durable store. Safe to call many times."""
    global _initialized
    with _lock:
        if _initialized:
            return _enabled
        _initialized = True
        ok = _build()
        if ok:
            ensure_partitions()
        return ok


def enabled() -> bool:
    return _enabled


def _workflow_id(payload: dict, agent_id: str) -> str:
    """evidence.ledger.workflow_id is NOT NULL. Use the payload's workflow id when
    the event carries one, else fall back to the agent id (a per-agent stream)."""
    return (payload.get("workflow_id") or payload.get("onboarding_workflow_id")
            or agent_id or "radar")


def _row_hash(prev: str | None, body: str) -> str:
    """Tamper-evident chain link: sha256 over the previous row hash + this row's
    canonical body (the magazine's hash-chained evidence ledger)."""
    return hashlib.sha256(((prev or "") + body).encode("utf-8")).hexdigest()


def record(event_type: str, agent_id: str = "", payload: dict | None = None) -> None:
    """Best-effort durable append, routed to the canonical table for this event
    kind. No-op when disabled or on any error — must never raise into a request
    handler or a laminar callback."""
    if not _enabled or _engine is None:
        return
    data = dict(payload or {})
    data[_KIND_KEY] = event_type                 # so the audit read can recover it
    table = _ROUTE.get(event_type, _DEFAULT_TABLE)
    body = json.dumps(data, sort_keys=True, default=str)
    try:
        with _engine.begin() as c:
            if table == "policy_decisions":
                c.execute(_text(_SQL_POLICY), {
                    "agent_id": agent_id or None,
                    "workflow_id": data.get("workflow_id"),
                    "tool_call": data.get("action") or data.get("detail") or event_type,
                    "risk": None,                # tier string ≠ numeric(4,3) column
                    # canonical CHECK: auto_authorized | awaiting_token |
                    # awaiting_operator | denied. A durable gate event is a denial.
                    "decision": data.get("decision") or "denied",
                    "scope": data.get("scope"),
                    "reason": data.get("reason") or data.get("detail"),
                    "token_id": data.get("token_id"),
                    "payload": body,
                })
            elif table == "degradation_events":
                c.execute(_text(_SQL_DEGRADE), {
                    "agent_id": agent_id or None,
                    "workflow_id": data.get("workflow_id"),
                    "from_profile": data.get("from_profile"),
                    "to_profile": data.get("to_profile") or _to_profile(data.get("detail")),
                    "trigger": data.get("trigger") or ("operator" if event_type == "autonomy"
                                                       else data.get("mode")),
                    "trace_sampling": None,      # numeric column; not tracked here
                    "reason": data.get("reason") or data.get("detail"),
                    "payload": body,
                })
            else:
                prev = c.execute(_text(
                    "SELECT row_hash FROM evidence.ledger ORDER BY ts DESC LIMIT 1"
                )).scalar()
                rh = _row_hash(prev, body)
                c.execute(_text(_SQL_EVIDENCE), {
                    "workflow_id": _workflow_id(data, agent_id),
                    "agent_id": agent_id or None,
                    "event_type": event_type,
                    "actor": data.get("actor"),
                    "policy_result": data.get("policy_result"),
                    "step": data.get("step"),
                    "context_hash": data.get("context_hash"),
                    "degradation_state": data.get("degradation_state"),
                    "prev_hash": prev,
                    "row_hash": rh,
                    "payload": body,
                })
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        log.debug("radar.db.record failed type=%s table=%s error=%r", event_type, table, exc)


def _to_profile(detail: str | None) -> str | None:
    """Best-effort extraction of the target profile from a degraded/autonomy
    detail string like '-> recommendation_only' or 'operator set to safe_profile'.
    The full context is always kept in the jsonb payload regardless."""
    if not detail:
        return None
    for sep in ("-> ", "to "):
        if sep in detail:
            return detail.split(sep, 1)[1].strip() or None
    return None


# Unified audit read across the three canonical tables. event_type is recovered
# from the payload's _kind so the response shape matches the old flat table.
_SQL_QUERY = (
    "SELECT ts, agent_id, event_type, payload FROM ("
    "  SELECT EXTRACT(EPOCH FROM ts) AS ts, agent_id,"
    "         COALESCE(payload->>'" + _KIND_KEY + "', event_type) AS event_type, payload"
    "    FROM evidence.ledger"
    "  UNION ALL"
    "  SELECT EXTRACT(EPOCH FROM ts), agent_id,"
    "         COALESCE(payload->>'" + _KIND_KEY + "', 'gate_denied'), payload"
    "    FROM governance.policy_decisions"
    "  UNION ALL"
    "  SELECT EXTRACT(EPOCH FROM ts), agent_id,"
    "         COALESCE(payload->>'" + _KIND_KEY + "', 'degraded'), payload"
    "    FROM governance.degradation_events"
    ") u "
    "WHERE (CAST(:agent_id AS text) IS NULL OR agent_id = :agent_id) "
    "  AND (CAST(:event_type AS text) IS NULL OR event_type = :event_type) "
    "  AND (CAST(:since AS double precision) IS NULL OR ts >= :since) "
    "ORDER BY ts DESC LIMIT :limit"
)


# ── Approval tokens (governance.approval_tokens) ──────────────────────────────
# The operator gate for new write-scope grants (magazine: "New write scopes ...
# Human approval"). A scope-expansion creates a pending token; the operator
# approves/denies it; the agent stays quarantined while a pending token is open.
# This is the DURABLE backing for the in-memory approval gate on AgentEntry —
# registry.agents has no approval column, so the token table is what survives a
# restart (the radar rehydrates the gate from open tokens on startup).
APPROVAL_TTL_SECONDS = float(os.getenv("AGENT_RADAR_APPROVAL_TTL", str(7 * 24 * 3600)))
# action_class is a free-text label on the token; this is the radar's class for a
# write-scope expansion. Overridable so other gated action classes can reuse this.
APPROVAL_ACTION_CLASS = os.getenv("AGENT_RADAR_APPROVAL_ACTION_CLASS", "scope_expansion")

_SQL_APPROVAL_INSERT = (
    "INSERT INTO governance.approval_tokens "
    "(workflow_id, agent_id, action_class, write_scopes, status, requested_by, "
    " context_diff, expires_at) "
    "VALUES (:workflow_id, :agent_id, :action_class, :write_scopes, 'pending', "
    " :requested_by, CAST(:context_diff AS jsonb), "
    " now() + (:ttl * interval '1 second')) "
    "RETURNING id"
)


def record_approval_request(agent_id: str, write_scopes: list[str],
                            context_diff: dict | None = None,
                            requested_by: str | None = None,
                            workflow_id: str | None = None) -> str | None:
    """Create a pending approval token for a scope expansion. Returns the token id,
    or None when disabled / on error (fail-open — the in-memory gate still holds)."""
    if not _enabled or _engine is None:
        return None
    try:
        with _engine.begin() as c:
            tid = c.execute(_text(_SQL_APPROVAL_INSERT), {
                "workflow_id": workflow_id or agent_id or "radar",
                "agent_id": agent_id or None,
                "action_class": APPROVAL_ACTION_CLASS,
                "write_scopes": list(write_scopes or []),
                "requested_by": requested_by,
                "context_diff": json.dumps(context_diff or {}, default=str),
                "ttl": APPROVAL_TTL_SECONDS,
            }).scalar()
        return str(tid) if tid else None
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        log.warning("radar.db.approval_request failed agent=%s error=%r", agent_id, exc)
        return None


def decide_approval(agent_id: str, decision: str = "approved",
                    decided_by: str | None = None) -> bool:
    """Mark this agent's open pending token(s) decided. decision ∈ approved|denied
    (canonical CHECK). Returns True if any token was updated."""
    if not _enabled or _engine is None:
        return False
    if decision not in ("approved", "denied"):
        decision = "approved"
    try:
        with _engine.begin() as c:
            res = c.execute(_text(
                "UPDATE governance.approval_tokens "
                "SET status=:decision, decided_by=:decided_by, decided_at=now() "
                "WHERE agent_id=:agent_id AND status='pending'"
            ), {"decision": decision, "decided_by": decided_by, "agent_id": agent_id})
            return (res.rowcount or 0) > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.db.decide_approval failed agent=%s error=%r", agent_id, exc)
        return False


def open_approval_agent_ids() -> set[str]:
    """Agent ids with an unexpired pending token — used to rehydrate the in-memory
    approval gate after a restart. Empty set when disabled."""
    if not _enabled or _engine is None:
        return set()
    try:
        with _engine.connect() as c:
            rows = c.execute(_text(
                "SELECT DISTINCT agent_id FROM governance.approval_tokens "
                "WHERE status='pending' AND expires_at > now() AND agent_id IS NOT NULL"
            )).scalars().all()
        return {r for r in rows if r}
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.db.open_approvals failed error=%r", exc)
        return set()


# ── Token ledger (evidence.token_ledger) ──────────────────────────────────────
# Per-LLM-call token/cost accounting. The laminar TokenLedger keeps the live
# sliding-window/lifetime view in memory; this is its DURABLE canonical sink (the
# magazine's Evidence Ledger token trail). evidence is append-only by GRANT, so
# this only ever INSERTs. Fail-open: a no-op when the DB is unavailable.
_SQL_TOKEN = (
    "INSERT INTO evidence.token_ledger "
    "(ts, agent_id, task_id, step, model, input_tokens, output_tokens, cost) "
    "VALUES (to_timestamp(:ts), :agent_id, :task_id, :step, :model, "
    " :input_tokens, :output_tokens, :cost)"
)


def record_token_usage(agent_id: str, input_tokens: int, output_tokens: int,
                       cost: float = 0.0, model: str = "unknown",
                       task_id: str | None = None, step: str | None = None,
                       ts: float | None = None) -> None:
    """Append one LLM call's token/cost to evidence.token_ledger. No-op when
    disabled or on error — must never disturb token accounting."""
    if not _enabled or _engine is None or not agent_id:
        return
    import time as _time
    try:
        with _engine.begin() as c:
            c.execute(_text(_SQL_TOKEN), {
                "ts": ts if ts is not None else _time.time(),
                "agent_id": agent_id, "task_id": task_id, "step": step,
                "model": model or "unknown",
                "input_tokens": max(0, int(input_tokens)),
                "output_tokens": max(0, int(output_tokens)),
                "cost": float(cost or 0.0),
            })
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        log.debug("radar.db.token_usage failed agent=%s error=%r", agent_id, exc)


# ── Freeze journal (registry.freeze_journal) ──────────────────────────────────
# Durable mirror of the in-memory/JSON freeze journal: which agents the control
# plane has hard-stopped (SIGSTOP / remote suspend). The local JSON file stays the
# crash-recovery source (it is readable even when the DB is down); this table is
# the queryable canonical record. Fail-open.
_SQL_FREEZE = (
    "INSERT INTO registry.freeze_journal (agent_id, kind, pid, url, reason, payload) "
    "VALUES (:agent_id, :kind, :pid, :url, :reason, CAST(:payload AS jsonb)) "
    "ON CONFLICT (agent_id) DO UPDATE SET kind=EXCLUDED.kind, pid=EXCLUDED.pid, "
    " url=EXCLUDED.url, reason=EXCLUDED.reason, payload=EXCLUDED.payload, frozen_at=now()"
)


def record_freeze(agent_id: str, kind: str, pid: int | None = None,
                  url: str | None = None, reason: str | None = None,
                  payload: dict | None = None) -> None:
    """Upsert a freeze record. kind ∈ process|remote (canonical CHECK). No-op when
    disabled / on error (the JSON journal remains the recovery source)."""
    if not _enabled or _engine is None or not agent_id:
        return
    if kind not in ("process", "remote"):
        return
    try:
        with _engine.begin() as c:
            c.execute(_text(_SQL_FREEZE), {
                "agent_id": agent_id, "kind": kind, "pid": pid, "url": url,
                "reason": reason, "payload": json.dumps(payload or {}, default=str),
            })
    except Exception as exc:  # noqa: BLE001
        log.debug("radar.db.record_freeze failed agent=%s error=%r", agent_id, exc)


def clear_freeze(agent_id: str) -> None:
    """Remove a freeze record when the agent is resumed. No-op when disabled."""
    if not _enabled or _engine is None or not agent_id:
        return
    try:
        with _engine.begin() as c:
            c.execute(_text("DELETE FROM registry.freeze_journal WHERE agent_id=:a"),
                      {"a": agent_id})
    except Exception as exc:  # noqa: BLE001
        log.debug("radar.db.clear_freeze failed agent=%s error=%r", agent_id, exc)


# ── Onboarding runs (ops.onboarding_runs) ─────────────────────────────────────
# Per-onboarding workflow state, keyed by workflow_id. finished_at is stamped when
# the run reaches 'done'. Fail-open.
_SQL_ONBOARD = (
    "INSERT INTO ops.onboarding_runs (workflow_id, agent_id, state, patch_ref, payload, finished_at) "
    "VALUES (:workflow_id, :agent_id, :state, :patch_ref, CAST(:payload AS jsonb), "
    " CASE WHEN :state='done' THEN now() ELSE NULL END) "
    "ON CONFLICT (workflow_id) DO UPDATE SET agent_id=EXCLUDED.agent_id, "
    " state=EXCLUDED.state, patch_ref=EXCLUDED.patch_ref, payload=EXCLUDED.payload, "
    " finished_at=CASE WHEN EXCLUDED.state='done' THEN now() "
    "             ELSE ops.onboarding_runs.finished_at END"
)


def record_onboarding_run(workflow_id: str, agent_id: str, state: str,
                          patch_ref: str | None = None, payload: dict | None = None) -> None:
    """Upsert an onboarding run. state ∈ pending|running|done (canonical CHECK).
    No-op when disabled / on error."""
    if not _enabled or _engine is None or not workflow_id:
        return
    if state not in ("pending", "running", "done"):
        return
    try:
        with _engine.begin() as c:
            c.execute(_text(_SQL_ONBOARD), {
                "workflow_id": workflow_id, "agent_id": agent_id or None,
                "state": state, "patch_ref": patch_ref,
                "payload": json.dumps(payload or {}, default=str),
            })
    except Exception as exc:  # noqa: BLE001
        log.debug("radar.db.record_onboarding_run failed wf=%s error=%r", workflow_id, exc)


def query(agent_id: str | None = None, since: float | None = None,
          event_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Query the durable audit trail across the canonical tables, newest first.
    `since` is UNIX epoch seconds. Returns [] when disabled."""
    if not _enabled or _engine is None:
        return []
    limit = max(1, min(int(limit or 100), 1000))
    try:
        with _engine.connect() as c:
            rows = c.execute(_text(_SQL_QUERY), {
                "agent_id": agent_id, "event_type": event_type,
                "since": since, "limit": limit,
            }).mappings().all()
        out = []
        for r in rows:
            payload = r["payload"] or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:  # noqa: BLE001
                    payload = {"raw": payload}
            out.append({
                "ts": float(r["ts"]) if r["ts"] is not None else None,
                "agent_id": r["agent_id"],
                "event_type": r["event_type"],
                "payload": payload,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.db.query failed error=%r", exc)
        return []
