"""Durable Postgres store for per-chat conversation history — fail-open.

Every user turn (input) and the agent's answer (output) is recorded here, keyed by
a `session_id` (one chat). This replaces the ad-hoc `artifacts/` folder as the
canonical record of a run, and is the backing store for two features:

  * per-chat CONTEXT MEMORY — before an agent runs, it reads the prior turns of the
    same session so it can reference earlier context ("what did I ask before?");
  * the task console's inline CONTEXT-WINDOW meter — Σ total_tokens for the session
    measured against AWCP_CONTEXT_WINDOW_TOKENS (default 128k).

One table in the `ops` schema, next to ops.workflow_events / ops.sandbox_events:

  ops.chat_turns   append log of every completed agent turn (input, output, tools,
                   timing, token usage, task/workflow ids)

It mirrors awcp.runtime.sandbox_db's philosophy exactly:

  * env-driven — AGENT_RADAR_DATABASE_URL (app, DML) + AGENT_RADAR_DB_ADMIN_URL
    (owner, used once to CREATE the table IF NOT EXISTS so an already-initialised
    DB volume picks it up without re-running init-db);
  * FAIL-OPEN — no URL / no driver / DB unreachable ⇒ record() is a no-op and
    history() returns None, so the chat keeps working with no memory rather than
    breaking. Persistence never crashes a task.

Env:
  AGENT_RADAR_DATABASE_URL     ""       SQLAlchemy URL for reads/writes (app role).
  AGENT_RADAR_DB_ADMIN_URL     ""       owner URL for DDL; falls back to the app URL.
  AWCP_CONTEXT_WINDOW_TOKENS   128000   the model context window the meter is measured against.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

log = logging.getLogger("awcp.gateway.chat_store")

DATABASE_URL = os.getenv("AGENT_RADAR_DATABASE_URL", "").strip()
DB_ADMIN_URL = os.getenv("AGENT_RADAR_DB_ADMIN_URL", "").strip() or DATABASE_URL

# The context window the inline meter measures Σ tokens against. Env-overridable so
# a deployment on a smaller num_ctx (or a bigger model) reports the right %.
CONTEXT_WINDOW_TOKENS = int(os.getenv("AWCP_CONTEXT_WINDOW_TOKENS", "128000") or 128000)

# App role(s) granted DML on the table after the owner creates it. Canonical roles
# from observability/init-db; a grant to a missing role is ignored.
_GRANT_ROLES = ("awcp_app", "awcp_ro")

_DDL = (
    "CREATE SCHEMA IF NOT EXISTS ops",
    "CREATE TABLE IF NOT EXISTS ops.chat_turns ("
    " id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
    " ts            timestamptz NOT NULL DEFAULT now(),"
    " session_id    text NOT NULL,"
    " seq           integer NOT NULL DEFAULT 0,"
    " task_id       text,"
    " workflow_id   text,"
    " agent_id      text,"
    " agent_name    text,"
    " framework     text,"
    " model         text,"
    " input         text,"
    " output        text,"
    " tools_used    jsonb   NOT NULL DEFAULT '[]',"
    " status        text,"
    " input_tokens  integer NOT NULL DEFAULT 0,"
    " output_tokens integer NOT NULL DEFAULT 0,"
    " total_tokens  integer NOT NULL DEFAULT 0,"
    " created_ts    double precision,"
    " started_ts    double precision,"
    " finished_ts   double precision,"
    " duration_ms   integer NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON ops.chat_turns (session_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_chat_turns_ts      ON ops.chat_turns (ts DESC)",
)

_lock = threading.Lock()
_engine = None
_text = None
_enabled = False
_initialized = False


def _create_table() -> None:
    """Create ops.chat_turns IF NOT EXISTS via the owner URL, then grant DML to the
    app role(s). Owner rights are needed for DDL; the operational engine below uses
    the (possibly least-privileged) app URL. Best-effort — a grant to a
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
                        f"ON ops.chat_turns TO {role}"))
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
            log.info("chat_store disabled (AGENT_RADAR_DATABASE_URL unset) — no chat history/memory")
            return False
        try:
            from sqlalchemy import create_engine, text
        except Exception as exc:  # noqa: BLE001 — SQLAlchemy not installed
            log.info("chat_store disabled (sqlalchemy unavailable: %r)", exc)
            return False
        try:
            _create_table()                       # owner DDL (IF NOT EXISTS)
            engine = create_engine(
                DATABASE_URL, pool_pre_ping=True, pool_recycle=300,
                connect_args={"connect_timeout": 3}
                if DATABASE_URL.startswith(("postgresql", "postgres")) else {},
            )
            with engine.connect() as c:           # verify the app role can read it
                c.execute(text("SELECT 1 FROM ops.chat_turns LIMIT 1"))
            _engine, _text, _enabled = engine, text, True
            log.info("chat_store enabled — chat turns -> Postgres at %s",
                     DATABASE_URL.split("@")[-1])
            return True
        except Exception as exc:  # noqa: BLE001 — DB down / no perms / driver — fail open
            log.warning("chat_store init failed (%r) — chat history/memory off", exc)
            return False


def enabled() -> bool:
    return _enabled


def _next_seq(c, session_id: str) -> int:
    """The next turn index within a chat (0-based, gap-free enough for ordering)."""
    row = c.execute(_text(
        "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM ops.chat_turns WHERE session_id = :s"
    ), {"s": session_id}).mappings().first()
    return int(row["n"]) if row else 0


def record(turn: dict) -> int | None:
    """Append one completed turn. Returns the assigned `seq` within the chat, or
    None when disabled / on error — must never raise into the request path."""
    if not _enabled or _engine is None:
        return None
    session_id = str(turn.get("session_id") or "").strip()
    if not session_id:
        return None
    try:
        tools = turn.get("tools_used") or []
        with _engine.begin() as c:
            seq = _next_seq(c, session_id)
            c.execute(_text(
                "INSERT INTO ops.chat_turns "
                "(session_id, seq, task_id, workflow_id, agent_id, agent_name, framework,"
                " model, input, output, tools_used, status, input_tokens, output_tokens,"
                " total_tokens, created_ts, started_ts, finished_ts, duration_ms) VALUES "
                "(:session_id, :seq, :task_id, :workflow_id, :agent_id, :agent_name, :framework,"
                " :model, :input, :output, CAST(:tools_used AS jsonb), :status, :input_tokens,"
                " :output_tokens, :total_tokens, :created_ts, :started_ts, :finished_ts, :duration_ms)"
            ), {
                "session_id": session_id,
                "seq": seq,
                "task_id": turn.get("task_id"),
                "workflow_id": turn.get("workflow_id"),
                "agent_id": turn.get("agent_id"),
                "agent_name": turn.get("agent_name"),
                "framework": turn.get("framework"),
                "model": turn.get("model"),
                "input": turn.get("input"),
                "output": turn.get("output"),
                "tools_used": json.dumps(tools if isinstance(tools, list) else [], default=str),
                "status": turn.get("status"),
                "input_tokens": int(turn.get("input_tokens") or 0),
                "output_tokens": int(turn.get("output_tokens") or 0),
                "total_tokens": int(turn.get("total_tokens")
                                    or (int(turn.get("input_tokens") or 0)
                                        + int(turn.get("output_tokens") or 0))),
                "created_ts": turn.get("created_ts"),
                "started_ts": turn.get("started_ts"),
                "finished_ts": turn.get("finished_ts"),
                "duration_ms": int(turn.get("duration_ms") or 0),
            })
        return seq
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        log.debug("chat_store.record failed session=%s error=%r", session_id, exc)
        return None


def usage(session_id: str) -> dict | None:
    """Whole-session token total + turn count (a cheap SUM/COUNT), so the context
    meter is correct no matter how many turns the history page returns. None when
    disabled / on error."""
    if not _enabled or _engine is None:
        return None
    session_id = str(session_id or "").strip()
    if not session_id:
        return {"used_tokens": 0, "turns": 0}
    try:
        with _engine.connect() as c:
            row = c.execute(_text(
                "SELECT COALESCE(SUM(total_tokens), 0) AS used, COUNT(*) AS n "
                "FROM ops.chat_turns WHERE session_id = :s"
            ), {"s": session_id}).mappings().first()
        return {"used_tokens": int(row["used"] or 0), "turns": int(row["n"] or 0)}
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_store.usage failed session=%s error=%r", session_id, exc)
        return None


def history(session_id: str, limit: int = 50) -> list[dict[str, Any]] | None:
    """Prior turns for one chat, OLDEST FIRST (the natural order for building a
    conversation preamble). Returns None when disabled / on error so the caller can
    fall back to no memory."""
    if not _enabled or _engine is None:
        return None
    session_id = str(session_id or "").strip()
    if not session_id:
        return []
    limit = max(1, min(int(limit or 50), 500))
    try:
        with _engine.connect() as c:
            rows = c.execute(_text(
                "SELECT seq, task_id, workflow_id, agent_id, agent_name, framework, model,"
                " input, output, tools_used, status, input_tokens, output_tokens, total_tokens,"
                " created_ts, started_ts, finished_ts, duration_ms,"
                " EXTRACT(EPOCH FROM ts) AS ts_epoch "
                "FROM ops.chat_turns WHERE session_id = :s ORDER BY seq ASC, ts ASC LIMIT :limit"
            ), {"s": session_id, "limit": limit}).mappings().all()
        out: list[dict[str, Any]] = []
        for r in rows:
            tools = r["tools_used"] or []
            if isinstance(tools, str):
                try:
                    tools = json.loads(tools)
                except Exception:  # noqa: BLE001
                    tools = []
            out.append({
                "seq": r["seq"],
                "task_id": r["task_id"],
                "workflow_id": r["workflow_id"],
                "agent_id": r["agent_id"],
                "agent_name": r["agent_name"],
                "framework": r["framework"],
                "model": r["model"],
                "input": r["input"] or "",
                "output": r["output"] or "",
                "tools_used": tools if isinstance(tools, list) else [],
                "status": r["status"],
                "input_tokens": r["input_tokens"] or 0,
                "output_tokens": r["output_tokens"] or 0,
                "total_tokens": r["total_tokens"] or 0,
                "created_ts": r["created_ts"],
                "started_ts": r["started_ts"],
                "finished_ts": r["finished_ts"],
                "duration_ms": r["duration_ms"] or 0,
                "ts": float(r["ts_epoch"]) if r["ts_epoch"] is not None else None,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_store.history failed session=%s error=%r", session_id, exc)
        return None
