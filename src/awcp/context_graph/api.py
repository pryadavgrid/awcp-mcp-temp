"""HTTP surface for the context graph — a FastAPI APIRouter.

Mounted into the radar router (``awcp.radar.api``), so every endpoint here is
served on the same port the MCP server already talks to (the gate port). All
handlers are fail-open: on any internal error they return an empty/ok-false body
rather than 500, so the trail never breaks a caller.

Endpoints:
  POST /agents/{agent_id}/checkpoint   record one governed step
  GET  /agents/{agent_id}/context-graph all nodes for one agent (most recent)
  GET  /context-graph/{workflow_id}     one workflow's ordered graph (nodes+edges)
  GET  /context-graph                   recent nodes across all workflows
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from awcp.context_graph import graph_store, store, verify
from awcp.context_graph.models import CheckpointRequest

log = logging.getLogger("awcp.context_graph")

router = APIRouter(tags=["context-graph"])


@router.post("/agents/{agent_id}/checkpoint")
def post_checkpoint(agent_id: str, req: CheckpointRequest) -> dict:
    """Record one governed step (a node) in this agent's context graph."""
    try:
        node = store.record_checkpoint(
            agent_id=agent_id, step=req.step, task_id=req.task_id,
            workflow_id=req.workflow_id, actor=req.actor or agent_id,
            resume_pointer=req.resume_pointer, context=req.context,
            context_hash=req.context_hash, payload=req.payload,
        )
        return {"ok": True, "node": node.model_dump()}
    except Exception as exc:  # noqa: BLE001 — recording must never fail a caller
        log.debug("context_graph.post_checkpoint failed agent=%s err=%r", agent_id, exc)
        return {"ok": False, "error": type(exc).__name__}


@router.get("/agents/{agent_id}/context-graph")
def get_agent_graph(agent_id: str, limit: int = 200) -> dict:
    """All recorded steps for one agent (most recent first not guaranteed —
    ordered by time ascending so the trail reads top-to-bottom)."""
    nodes = store.nodes_for_agent(agent_id, limit=limit)
    return {"agent_id": agent_id, "count": len(nodes),
            "nodes": [n.model_dump() for n in nodes]}


# NOTE: declared BEFORE the parameterised /context-graph/{workflow_id} route so
# "verify" is matched as this literal path, not captured as a workflow_id.
@router.get("/context-graph/verify")
def verify_chain() -> dict:
    """Re-derive the whole evidence-ledger hash chain and report any break.
    Pure read; returns {enabled:false} when the durable ledger (Postgres) is off."""
    return verify.verify_chain().model_dump()


# ── Neo4j graph projection (additive read-model) ─────────────────────────────
@router.get("/context-graph/neo4j/status")
def neo4j_status() -> dict:
    """Neo4j connection + node counts. {enabled:false} when Neo4j is off."""
    return graph_store.status()


@router.get("/context-graph/neo4j/graph")
def neo4j_graph(workflow: str | None = None, agent: str | None = None,
                limit: int = 300) -> dict:
    """Node-link graph (nodes + edges) for visualization, from Neo4j."""
    return graph_store.fetch_graph(workflow=workflow, agent=agent, limit=limit)


@router.post("/context-graph/neo4j/backfill")
def neo4j_backfill(limit: int = 10000) -> dict:
    """Mirror existing ledger checkpoints into Neo4j (idempotent). Run once after
    Neo4j first comes up to project history that predates it."""
    return graph_store.backfill(limit=limit)


@router.get("/context-graph/{workflow_id}")
def get_workflow_graph(workflow_id: str, limit: int = 500) -> dict:
    """One workflow's ordered nodes plus its step→step lineage edges."""
    return store.graph_for_workflow(workflow_id, limit=limit).model_dump()


@router.get("/context-graph")
def list_recent(limit: int = 200) -> dict:
    """Recent nodes across all workflows (a global feed of governed steps)."""
    nodes = store.recent_nodes(limit=limit)
    return {"count": len(nodes), "nodes": [n.model_dump() for n in nodes]}
