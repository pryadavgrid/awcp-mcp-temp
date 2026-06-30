"""Durable Postgres store for the sandbox event timeline — fail-open.

The sandbox lifecycle + tool-call timeline the UI's "Sandbox" page renders lives
in an in-memory ring buffer (`_EVENTS` in awcp.runtime.sandbox). That ring is
VOLATILE: every event is lost when the MCP server process exits (e.g. Ctrl+C in
the launcher), so after a restart the Sandbox page shows "No sandbox activity yet"
even though the workspace did real work.

This module mirrors each event into the canonical control-plane Postgres (the same
DB the radar/OPA agent use) so the timeline survives a restart and is queryable in
Adminer. One table in the `ops` schema, next to ops.workflow_events:

  ops.sandbox_events   append log of every sandbox lifecycle/tool event

It mirrors awcp.opa_agent.db's philosophy exactly:

  * env-driven — AGENT_RADAR_DATABASE_URL (app, DML) + AGENT_RADAR_DB_ADMIN_URL
    (owner, used once to CREATE the table IF NOT EXISTS so an already-initialised
    DB volume picks it up without re-running init-db);
  * FAIL-OPEN — no URL / no driver / DB unreachable ⇒ every call here is a no-op
    (record) or returns None (recent), and the sandbox keeps running on its
    in-memory ring exactly as before. Persistence never crashes a tool call.

Env:
  AGENT_RADAR_DATABASE_URL   ""   SQLAlchemy URL for reads/writes (app role).
  AGENT_RADAR_DB_ADMIN_URL   ""   owner URL for DDL; falls back to the app URL.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

log = logging.getLogger("awcp.runtime.sandbox_db")

DATABASE_URL = os.getenv("AGENT_RADAR_DATABASE_URL", "").strip()
DB_ADMIN_URL = os.getenv("AGENT_RADAR_DB_ADMIN_URL", "").strip() or DATABASE_URL

# App role(s) granted DML on the table after the owner creates it. The canonical
# roles from observability/init-db; granting to a missing role is ignored.
_GRANT_ROLES = ("awcp_app", "awcp_ro")

_DDL = (
    "CREATE SCHEMA IF NOT EXISTS ops",
    # One row per sandbox lifecycle/tool event. event_ts is the original
    # time.time() epoch from record_event (so ordering matches the in-memory
    # ring exactly); ts is the human-readable insert time for Adminer. Any extra
    # kwargs passed to record_event land in payload.
    "CREATE TABLE IF NOT EXISTS ops.sandbox_events ("
    " id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
    " ts        timestamptz NOT NULL DEFAULT now(),"
    " event_ts  double precision NOT NULL,"
    " kind      text NOT NULL,"
    " detail    text,"
    " payload   jsonb NOT NULL DEFAULT '{}')",
    "CREATE INDEX IF NOT EXISTS idx_sandbox_events_event_ts "
    " ON ops.sandbox_events (event_ts DESC)",
)

_lock = threading.Lock()
_engine = None
_text = None
_enabled = False
_initialized = False


def _create_table() -> None:
    """Create ops.sandbox_events IF NOT EXISTS via the owner URL, then grant DML to
    the app role(s). Owner rights are needed for DDL; the operational engine below
    uses the (possibly least-privileged) app URL. Best-effort — a grant to a
    non-existent role is swallowed."""
    from sqlalchemy import create_engine, text
    eng = create_engine(DB_ADMIN_URL, connect_args={"connect_timeout": 3}
                        if DB_ADMIN_URL.startswith(("postgresql", "postgres")) else {})
    try:
        with eng.begin() as c:
            for stmt in _DDL:
                c.execute(text(stmt))
        for role in _GRANT_ROLES:
            try:
                with eng.begin() as c:
                    c.execute(text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE "
                        f"ON ops.sandbox_events TO {role}"))
            except Exception:  # noqa: BLE001 — role may not exist; ignore
                pass
    finally:
        eng.dispose()


def init() -> bool:
    """Idempotently build the engine and ensure the table exists. Returns True when
    durable storage is live; any failure leaves it disabled (logged once)."""
    global _engine, _text, _enabled, _initialized
    with _lock:
        if _initialized:
            return _enabled
        _initialized = True
        if not DATABASE_URL:
            log.info("sandbox.db disabled (AGENT_RADAR_DATABASE_URL unset) — in-memory only")
            return False
        try:
            from sqlalchemy import create_engine, text
        except Exception as exc:  # noqa: BLE001 — SQLAlchemy not installed
            log.info("sandbox.db disabled (sqlalchemy unavailable: %r)", exc)
            return False
        try:
            _create_table()                       # owner DDL (IF NOT EXISTS)
            engine = create_engine(
                DATABASE_URL, pool_pre_ping=True, pool_recycle=300,
                connect_args={"connect_timeout": 3}
                if DATABASE_URL.startswith(("postgresql", "postgres")) else {},
            )
            with engine.connect() as c:           # verify the app role can read it
                c.execute(text("SELECT 1 FROM ops.sandbox_events LIMIT 1"))
            _engine, _text, _enabled = engine, text, True
            log.info("sandbox.db enabled — sandbox events -> Postgres at %s",
                     DATABASE_URL.split("@")[-1])
            return True
        except Exception as exc:  # noqa: BLE001 — DB down / no perms / driver — fail open
            log.warning("sandbox.db init failed (%r) — durable storage off, in-memory only", exc)
            return False


def enabled() -> bool:
    return _enabled


def record(kind: str, detail: str = "", event_ts: float = 0.0,
           payload: dict | None = None) -> None:
    """Append one sandbox event. No-op when disabled / on error — must never raise
    into a tool call or the sandbox lifecycle."""
    if not _enabled or _engine is None or not kind:
        return
    try:
        with _engine.begin() as c:
            c.execute(_text(
                "INSERT INTO ops.sandbox_events (event_ts, kind, detail, payload) "
                "VALUES (:event_ts, :kind, :detail, CAST(:payload AS jsonb))"
            ), {"event_ts": float(event_ts or 0.0), "kind": kind,
                "detail": detail or None,
                "payload": json.dumps(payload or {}, default=str)})
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        log.debug("sandbox.db.record failed kind=%s error=%r", kind, exc)


def recent(limit: int = 50) -> list[dict[str, Any]] | None:
    """The most recent sandbox events, newest first, in the SAME shape as the
    in-memory ring ({ts, kind, detail, **payload}) so the UI is unaffected. Returns
    None when disabled / on error so the caller can fall back to memory."""
    if not _enabled or _engine is None:
        return None
    limit = max(1, min(int(limit or 50), 2000))
    try:
        with _engine.connect() as c:
            rows = c.execute(_text(
                "SELECT event_ts, kind, detail, payload FROM ops.sandbox_events "
                "ORDER BY event_ts DESC LIMIT :limit"
            ), {"limit": limit}).mappings().all()
        out: list[dict[str, Any]] = []
        for r in rows:
            payload = r["payload"] or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:  # noqa: BLE001
                    payload = {}
            out.append({"ts": float(r["event_ts"]) if r["event_ts"] is not None else None,
                        "kind": r["kind"], "detail": r["detail"] or "",
                        **(payload if isinstance(payload, dict) else {})})
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("sandbox.db.recent failed error=%r", exc)
        return None
