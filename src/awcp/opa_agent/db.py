"""Durable Postgres store for the OPA agent — replaces the on-disk JSON cache.

The OPA agent decides two things worth keeping across restarts:

  1. the SLM-reasoned RISK TIER per tool   (was: a JSON file on disk)
  2. every tool-call DECISION it made       (was: in-memory only, lost on restart)

Both now live in the canonical control-plane Postgres (the same DB the radar uses),
so NOTHING is written to disk and a restart restores the tier cache + the decision
history. Two tables, both in the `governance` schema:

  governance.tool_tiers              tool_name PK -> {tier, reason, engine, model, ts}
  governance.tool_call_evaluations   append log of every evaluated tool call

This module is fully self-contained (the OPA agent runs as a standalone script and
imports only its local siblings — slm, radar_register — so it can't import the
awcp.radar package). It mirrors radar/db.py's philosophy:

  * env-driven — AGENT_RADAR_DATABASE_URL (app, DML) + AGENT_RADAR_DB_ADMIN_URL
    (owner, used once to CREATE the tables IF NOT EXISTS so an already-initialised
    DB picks them up without re-running init-db);
  * FAIL-OPEN — no URL / no driver / DB unreachable ⇒ every call here is a no-op and
    the OPA agent keeps running on its in-memory dicts (just without persistence).
    Persistence never crashes a decision.

Env:
  AGENT_RADAR_DATABASE_URL   ""   SQLAlchemy URL for reads/writes (app role).
  AGENT_RADAR_DB_ADMIN_URL   ""   owner URL for DDL; falls back to the app URL.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("awcp.opa_agent.db")

DATABASE_URL = os.getenv("AGENT_RADAR_DATABASE_URL", "").strip()
DB_ADMIN_URL = os.getenv("AGENT_RADAR_DB_ADMIN_URL", "").strip() or DATABASE_URL

# App role(s) granted DML on the two tables after the owner creates them. The
# canonical roles from observability/init-db; granting to a missing role is ignored.
_GRANT_ROLES = ("awcp_app", "awcp_ro")

_DDL = (
    "CREATE SCHEMA IF NOT EXISTS governance",
    # The SLM-reasoned tier per tool (the durable replacement for the JSON cache).
    "CREATE TABLE IF NOT EXISTS governance.tool_tiers ("
    " tool_name  text PRIMARY KEY,"
    " tier       text NOT NULL,"
    " reason     text,"
    " engine     text,"
    " model      text,"
    " ts         double precision,"
    " updated_at timestamptz NOT NULL DEFAULT now())",
    # Append log of every tool call the OPA agent tiered + decided.
    "CREATE TABLE IF NOT EXISTS governance.tool_call_evaluations ("
    " id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
    " ts        timestamptz NOT NULL DEFAULT now(),"
    " task_id   text,"
    " agent_id  text,"
    " tool_name text NOT NULL,"
    " risk_tier text,"
    " decision  text NOT NULL,"
    " reason    text,"
    " reasoning text,"
    " engine    text,"
    " question  text)",
    "CREATE INDEX IF NOT EXISTS idx_tooleval_task "
    " ON governance.tool_call_evaluations (task_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_tooleval_ts "
    " ON governance.tool_call_evaluations (ts DESC)",
)

_lock = threading.Lock()
_engine = None
_text = None
_enabled = False
_initialized = False


def _create_tables() -> None:
    """Create the two tables IF NOT EXISTS via the owner URL, then grant DML to the
    app role(s). Owner rights are needed for DDL; the operational engine below uses
    the (possibly least-privileged) app URL. Best-effort — a grant to a non-existent
    role is swallowed."""
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
                        f"GRANT SELECT, INSERT, UPDATE, DELETE "
                        f"ON governance.tool_tiers, governance.tool_call_evaluations TO {role}"))
            except Exception:  # noqa: BLE001 — role may not exist; ignore
                pass
    finally:
        eng.dispose()


def init() -> bool:
    """Idempotently build the engine and ensure the tables exist. Returns True when
    durable storage is live; any failure leaves it disabled (logged once)."""
    global _engine, _text, _enabled, _initialized
    with _lock:
        if _initialized:
            return _enabled
        _initialized = True
        if not DATABASE_URL:
            log.info("opa.db disabled (AGENT_RADAR_DATABASE_URL unset) — in-memory only")
            return False
        try:
            from sqlalchemy import create_engine, text
        except Exception as exc:  # noqa: BLE001 — SQLAlchemy not installed
            log.info("opa.db disabled (sqlalchemy unavailable: %r)", exc)
            return False
        try:
            _create_tables()                      # owner DDL (IF NOT EXISTS)
            engine = create_engine(
                DATABASE_URL, pool_pre_ping=True, pool_recycle=300,
                connect_args={"connect_timeout": 3}
                if DATABASE_URL.startswith(("postgresql", "postgres")) else {},
            )
            with engine.connect() as c:           # verify the app role can read them
                c.execute(text("SELECT 1 FROM governance.tool_tiers LIMIT 1"))
                c.execute(text("SELECT 1 FROM governance.tool_call_evaluations LIMIT 1"))
            _engine, _text, _enabled = engine, text, True
            log.info("opa.db enabled — tool tiers + decisions -> Postgres at %s",
                     DATABASE_URL.split("@")[-1])
            return True
        except Exception as exc:  # noqa: BLE001 — DB down / no perms / driver — fail open
            log.warning("opa.db init failed (%r) — durable storage off, in-memory only", exc)
            return False


def enabled() -> bool:
    return _enabled


# ── tool tiers (the durable replacement for the JSON cache) ───────────────────
def load_tiers() -> dict[str, dict]:
    """Re-load the SLM-decided tier cache from Postgres (so a restart doesn't re-pay
    the SLM). Empty dict when disabled / on error."""
    if not _enabled or _engine is None:
        return {}
    try:
        with _engine.connect() as c:
            rows = c.execute(_text(
                "SELECT tool_name, tier, reason, engine, model, ts FROM governance.tool_tiers"
            )).mappings().all()
        return {r["tool_name"]: {"tier": r["tier"], "reason": r["reason"] or "",
                                 "engine": r["engine"] or "slm", "model": r["model"] or "",
                                 "ts": float(r["ts"]) if r["ts"] is not None else 0.0}
                for r in rows}
    except Exception as exc:  # noqa: BLE001
        log.warning("opa.db.load_tiers failed error=%r", exc)
        return {}


def upsert_tier(tool_name: str, rec: dict) -> None:
    """Persist one tool's resolved tier record. No-op when disabled / on error."""
    if not _enabled or _engine is None or not tool_name:
        return
    try:
        with _engine.begin() as c:
            c.execute(_text(
                "INSERT INTO governance.tool_tiers (tool_name, tier, reason, engine, model, ts) "
                "VALUES (:tool_name, :tier, :reason, :engine, :model, :ts) "
                "ON CONFLICT (tool_name) DO UPDATE SET tier=EXCLUDED.tier, "
                " reason=EXCLUDED.reason, engine=EXCLUDED.engine, model=EXCLUDED.model, "
                " ts=EXCLUDED.ts, updated_at=now()"
            ), {"tool_name": tool_name, "tier": rec.get("tier"),
                "reason": rec.get("reason", ""), "engine": rec.get("engine", "slm"),
                "model": rec.get("model", ""), "ts": float(rec.get("ts", 0) or 0)})
    except Exception as exc:  # noqa: BLE001
        log.warning("opa.db.upsert_tier failed tool=%s error=%r", tool_name, exc)


# ── tool-call decisions (the durable replacement for the in-memory ring) ──────
def record_decision(record: dict) -> None:
    """Append one evaluated tool call. No-op when disabled / on error."""
    if not _enabled or _engine is None:
        return
    try:
        with _engine.begin() as c:
            c.execute(_text(
                "INSERT INTO governance.tool_call_evaluations "
                "(task_id, agent_id, tool_name, risk_tier, decision, reason, reasoning, engine, question) "
                "VALUES (:task_id, :agent_id, :tool_name, :risk_tier, :decision, :reason, "
                " :reasoning, :engine, :question)"
            ), {"task_id": record.get("task_id") or None,
                "agent_id": record.get("agent_id") or None,
                "tool_name": record.get("tool_name"),
                "risk_tier": record.get("risk_tier"),
                "decision": record.get("decision"),
                "reason": record.get("reason", ""),
                "reasoning": record.get("reasoning", ""),
                "engine": record.get("engine", ""),
                "question": record.get("question", "")})
    except Exception as exc:  # noqa: BLE001
        log.warning("opa.db.record_decision failed tool=%s error=%r",
                    record.get("tool_name"), exc)


def decisions_for(task_id: str) -> dict | None:
    """The structured decision JSON for one question/task (every tool call + the
    derived `blocked`). None when disabled / on error so the caller can fall back."""
    if not _enabled or _engine is None:
        return None
    try:
        with _engine.connect() as c:
            rows = c.execute(_text(
                "SELECT EXTRACT(EPOCH FROM ts) AS ts, agent_id, tool_name, risk_tier, "
                " decision, reason, reasoning, engine, question "
                "FROM governance.tool_call_evaluations "
                "WHERE task_id = :task_id ORDER BY ts ASC"
            ), {"task_id": task_id}).mappings().all()
        if not rows:
            return {"task_id": task_id, "tools": [], "blocked": False}
        tools = [{"tool_name": r["tool_name"], "risk_tier": r["risk_tier"],
                  "decision": r["decision"], "reason": r["reason"] or "",
                  "reasoning": r["reasoning"] or "", "engine": r["engine"] or "",
                  "agent_id": r["agent_id"], "task_id": task_id,
                  "question": r["question"] or "",
                  "ts": float(r["ts"]) if r["ts"] is not None else None} for r in rows]
        question = next((t["question"] for t in tools if t["question"]), "")
        blocked = any(t["decision"] == "block" for t in tools)
        return {"task_id": task_id, "question": question, "tools": tools, "blocked": blocked}
    except Exception as exc:  # noqa: BLE001
        log.warning("opa.db.decisions_for failed task=%s error=%r", task_id, exc)
        return None


def load_operator_policy() -> dict | None:
    """Read the ACTIVE operator policy document from the SHARED governance.operator_policy
    table (written by the radar's Policy tab). Returns the policy JSON body, or None when
    disabled / no policy stored / the table doesn't exist yet. Fail-open: the OPA agent
    keeps tiering with the SLM alone if it can't read the policy."""
    if not _enabled or _engine is None:
        return None
    try:
        with _engine.connect() as c:
            row = c.execute(_text(
                "SELECT policy FROM governance.operator_policy ORDER BY id DESC LIMIT 1"
            )).mappings().first()
        if not row:
            return None
        policy = row["policy"]
        if isinstance(policy, str):
            import json as _json
            try:
                policy = _json.loads(policy)
            except Exception:  # noqa: BLE001
                return None
        return policy if isinstance(policy, dict) else None
    except Exception as exc:  # noqa: BLE001 — missing table / no perms / DB down: fail open
        log.warning("opa.db.load_operator_policy failed error=%r", exc)
        return None


def recent(limit: int = 200) -> list[dict] | None:
    """The most recent evaluated tool calls across ALL tasks, newest first (the
    Radar's tier-bar feed). None when disabled / on error so the caller can fall back."""
    if not _enabled or _engine is None:
        return None
    limit = max(1, min(int(limit or 200), 2000))
    try:
        with _engine.connect() as c:
            rows = c.execute(_text(
                "SELECT EXTRACT(EPOCH FROM ts) AS ts, task_id, agent_id, tool_name, "
                " risk_tier, decision, reason, reasoning, engine, question "
                "FROM governance.tool_call_evaluations ORDER BY ts DESC LIMIT :limit"
            ), {"limit": limit}).mappings().all()
        return [{"tool_name": r["tool_name"], "risk_tier": r["risk_tier"],
                 "decision": r["decision"], "reason": r["reason"] or "",
                 "reasoning": r["reasoning"] or "", "engine": r["engine"] or "",
                 "agent_id": r["agent_id"], "task_id": r["task_id"],
                 "question": r["question"] or "",
                 "ts": float(r["ts"]) if r["ts"] is not None else None} for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("opa.db.recent failed error=%r", exc)
        return None
