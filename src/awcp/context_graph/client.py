"""Tiny HTTP client for the context graph, used from ANOTHER process.

The MCP server (`awcp.mcp.server`) runs in a separate process from the radar and
cannot touch the Postgres engine, so it talks to the radar's context-graph
endpoints over HTTP (served by ``awcp.context_graph.api``, mounted into the radar
router). Best-effort and synchronous: a metering/recording hiccup must never
affect a tool's result, so every failure is swallowed at debug level.

Three calls cover the whole offload/retrieve loop:
  * ``record_checkpoint``    — write one node (a governed step OR an offloaded
    context chunk carried in ``payload``); returns the created node so the caller
    gets its ``row_hash`` ref back.
  * ``fetch_working_set``    — the budget-fitted, relevance-ranked recovery slice.
  * ``fetch_workflow_nodes`` — one run's raw nodes (for targeted retrieval by ref).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("awcp.context_graph.client")


def record_checkpoint(radar_url: str, agent_id: str, *, step: str,
                      task_id: str = "", workflow_id: str = "", actor: str = "",
                      resume_pointer: str = "", context: Any = None,
                      payload: dict | None = None,
                      timeout: float = 3.0) -> dict | None:
    """POST one checkpoint to the radar. Returns the created node dict (so the
    caller can keep its ``row_hash`` as a retrieval ref) or None on any failure.
    No-op without a radar_url/agent_id."""
    if not (radar_url and agent_id):
        return None
    try:
        resp = httpx.post(
            f"{radar_url.rstrip('/')}/agents/{agent_id}/checkpoint",
            json={
                "step": step, "task_id": task_id, "workflow_id": workflow_id,
                "actor": actor or agent_id, "resume_pointer": resume_pointer,
                "context": context, "payload": payload or {},
            },
            timeout=timeout,
        )
        body = resp.json()
        return body.get("node") if isinstance(body, dict) and body.get("ok") else None
    except Exception as exc:  # noqa: BLE001 — recording must never break a tool call
        log.debug("context_graph.client.record failed agent=%s step=%s err=%r",
                  agent_id, step, exc)
        return None


def fetch_working_set(radar_url: str, workflow_id: str, *, budget: int = 0,
                      focus: str = "", timeout: float = 6.0) -> dict | None:
    """GET the workflow's budget-fitted working set (the recovery slice an agent
    should carry forward). Returns the response dict or None on any failure."""
    if not (radar_url and workflow_id):
        return None
    try:
        resp = httpx.get(
            f"{radar_url.rstrip('/')}/context-graph/{workflow_id}/working-set",
            params={"budget": budget or 0, "focus": focus or ""},
            timeout=timeout,
        )
        body = resp.json()
        return body if isinstance(body, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.debug("context_graph.client.working_set failed wf=%s err=%r",
                  workflow_id, exc)
        return None


def fetch_workflow_nodes(radar_url: str, workflow_id: str, *, limit: int = 500,
                         timeout: float = 6.0) -> list[dict]:
    """GET one workflow's raw nodes (ordered oldest→newest). Used for targeted
    retrieval of a specific offloaded chunk by its row_hash ref. [] on failure."""
    if not (radar_url and workflow_id):
        return []
    try:
        resp = httpx.get(
            f"{radar_url.rstrip('/')}/context-graph/{workflow_id}",
            params={"limit": limit},
            timeout=timeout,
        )
        body = resp.json()
        nodes = body.get("nodes") if isinstance(body, dict) else None
        return nodes if isinstance(nodes, list) else []
    except Exception as exc:  # noqa: BLE001
        log.debug("context_graph.client.nodes failed wf=%s err=%r", workflow_id, exc)
        return []
