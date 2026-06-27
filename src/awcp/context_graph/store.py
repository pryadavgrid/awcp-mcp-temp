"""Persistence + in-memory mirror for context-graph nodes.

A node is one governed step. We append it to two places, best-effort:

  1. ``evidence.ledger`` (Postgres) as an ``event_type='checkpoint'`` row,
     INCLUDING the ``resume_pointer`` column the radar's own ``_SQL_EVIDENCE``
     omits — that omission is exactly why the context graph was empty. We reuse
     ``awcp.radar.db``'s engine (no second connection pool) and the same hash
     formula, so checkpoint rows extend the one continuous evidence chain.
  2. an in-memory ring, so reads are instant and still work when Postgres is off
     (fail-open: the demo always shows a graph). When the DB is enabled it is the
     source of truth for reads; the ring is the fallback.

Everything here is fail-open: a write that fails is logged at debug and dropped —
it never raises into a request handler or a tool call.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque

from awcp.context_graph.hashing import canonical, context_hash as _ctx_hash, row_hash
from awcp.context_graph.models import ContextGraph, ContextNode
from awcp.radar import db

log = logging.getLogger("awcp.context_graph")

_RING_MAX = int(os.getenv("AWCP_CONTEXT_GRAPH_RING", "5000"))
_ring: deque[ContextNode] = deque(maxlen=_RING_MAX)
_ring_lock = threading.Lock()

# Our own INSERT — same columns as awcp.radar.db._SQL_EVIDENCE PLUS resume_pointer.
# event_type is fixed to 'checkpoint'; _kind in the payload lets db.py's unified
# audit read recover the kind for free.
_INSERT_SQL = (
    "INSERT INTO evidence.ledger "
    "(workflow_id, agent_id, event_type, actor, policy_result, step, "
    " resume_pointer, context_hash, degradation_state, prev_hash, row_hash, payload) "
    "VALUES (:workflow_id, :agent_id, 'checkpoint', :actor, :policy_result, :step, "
    " :resume_pointer, :context_hash, :degradation_state, :prev_hash, :row_hash, "
    " CAST(:payload AS jsonb))"
)

_SELECT_SQL = (
    "SELECT EXTRACT(EPOCH FROM ts) AS ts, workflow_id, agent_id, step, actor, "
    "       resume_pointer, context_hash, prev_hash, row_hash, payload "
    "  FROM evidence.ledger "
    " WHERE event_type = 'checkpoint' "
    "   AND (CAST(:wf AS text) IS NULL OR workflow_id = :wf) "
    "   AND (CAST(:agent AS text) IS NULL OR agent_id = :agent) "
    " ORDER BY ts ASC "
    " LIMIT :limit"
)


def _db_ready() -> bool:
    return bool(getattr(db, "_enabled", False)) and getattr(db, "_engine", None) is not None


# ── write ────────────────────────────────────────────────────────────────────

def record_checkpoint(*, agent_id: str, step: str, task_id: str = "",
                      workflow_id: str = "", actor: str = "",
                      resume_pointer: str = "", context=None,
                      context_hash: str = "", payload: dict | None = None) -> ContextNode:
    """Record one governed step as a context-graph node. Never raises."""
    wf = workflow_id or task_id or agent_id or "radar"
    ch = context_hash or _ctx_hash(context)
    payload = dict(payload or {})

    # The stored body == what we hash, so row_hash verifies against the row later.
    stored = {
        "task_id": task_id, "agent_id": agent_id, "workflow_id": wf,
        "step": step, "actor": actor, "resume_pointer": resume_pointer,
        "context_hash": ch, **payload, "_kind": "checkpoint",
    }
    body = canonical(stored)

    prev: str | None = None
    rh: str | None = None
    if _db_ready():
        try:
            prev, rh = _write_db(body, wf=wf, agent_id=agent_id, actor=actor,
                                 step=step, resume_pointer=resume_pointer, ch=ch)
        except Exception as exc:  # noqa: BLE001 — durability is best-effort
            log.debug("context_graph.write_db failed agent=%s step=%s err=%r",
                      agent_id, step, exc)
            prev = rh = None
    if rh is None:  # DB off or write failed → chain off the ring instead
        with _ring_lock:
            prev = _ring[-1].row_hash if _ring else None
        rh = row_hash(prev, body)

    node = ContextNode(
        ts=time.time(), workflow_id=wf, agent_id=agent_id, task_id=task_id,
        step=step, actor=actor, resume_pointer=resume_pointer, context_hash=ch,
        prev_hash=prev, row_hash=rh, payload=payload,
    )
    with _ring_lock:
        _ring.append(node)

    # Project into the Neo4j graph mirror (additive, best-effort, fail-open). The
    # ledger above is the source of truth; this just keeps the graph view current.
    try:
        from awcp.context_graph import graph_store
        graph_store.mirror_checkpoint(node)
    except Exception:  # noqa: BLE001 — Neo4j is never required
        pass
    return node


def _write_db(body: str, *, wf: str, agent_id: str, actor: str, step: str,
              resume_pointer: str, ch: str) -> tuple[str | None, str]:
    """Insert one checkpoint row inside a single transaction so the prev→row
    chain has no race. Returns (prev_hash, row_hash)."""
    text = db._text
    with db._engine.begin() as c:
        prev = c.execute(
            text("SELECT row_hash FROM evidence.ledger ORDER BY ts DESC LIMIT 1")
        ).scalar()
        rh = row_hash(prev, body)
        c.execute(text(_INSERT_SQL), {
            "workflow_id": wf,
            "agent_id": agent_id or None,
            "actor": actor or None,
            "policy_result": None,
            "step": step or None,
            "resume_pointer": resume_pointer or None,
            "context_hash": ch or None,
            "degradation_state": None,
            "prev_hash": prev,
            "row_hash": rh,
            "payload": body,
        })
    return prev, rh


# ── read ─────────────────────────────────────────────────────────────────────

def _node_from_row(row) -> ContextNode:
    m = row._mapping if hasattr(row, "_mapping") else row
    payload = m["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:  # noqa: BLE001
            payload = {}
    payload = dict(payload or {})
    payload.pop("_kind", None)
    return ContextNode(
        ts=float(m["ts"]), workflow_id=m["workflow_id"] or "", agent_id=m["agent_id"] or "",
        task_id=payload.get("task_id", ""), step=m["step"] or "", actor=m["actor"] or "",
        resume_pointer=m["resume_pointer"] or "", context_hash=m["context_hash"] or "",
        prev_hash=m["prev_hash"], row_hash=m["row_hash"] or "", payload=payload,
    )


def _read_nodes(*, workflow_id: str | None = None, agent_id: str | None = None,
                limit: int = 500) -> list[ContextNode]:
    if _db_ready():
        try:
            text = db._text
            with db._engine.connect() as c:
                rows = c.execute(text(_SELECT_SQL), {
                    "wf": workflow_id, "agent": agent_id, "limit": limit,
                }).fetchall()
            return [_node_from_row(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.debug("context_graph.read_db failed err=%r — falling back to ring", exc)
    # in-memory fallback
    with _ring_lock:
        nodes = list(_ring)
    if workflow_id is not None:
        nodes = [n for n in nodes if n.workflow_id == workflow_id]
    if agent_id is not None:
        nodes = [n for n in nodes if n.agent_id == agent_id]
    nodes.sort(key=lambda n: n.ts)
    return nodes[-limit:]


def graph_for_workflow(workflow_id: str, limit: int = 500) -> ContextGraph:
    """Return one workflow's ordered nodes plus its step→step lineage edges."""
    nodes = _read_nodes(workflow_id=workflow_id, limit=limit)
    edges = [
        {"from": nodes[i - 1].row_hash, "to": nodes[i].row_hash, "step": nodes[i].step}
        for i in range(1, len(nodes))
    ]
    return ContextGraph(workflow_id=workflow_id, count=len(nodes), nodes=nodes, edges=edges)


def nodes_for_agent(agent_id: str, limit: int = 200) -> list[ContextNode]:
    return _read_nodes(agent_id=agent_id, limit=limit)


def recent_nodes(limit: int = 200) -> list[ContextNode]:
    return _read_nodes(limit=limit)
