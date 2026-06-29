"""Tiny HTTP client for recording checkpoints from ANOTHER process.

The MCP server (`awcp.mcp.server`) runs in a separate process from the radar and
cannot touch the Postgres engine, so it records steps by POSTing to the radar's
``/agents/{id}/checkpoint`` endpoint (served by ``awcp.context_graph.api``, mounted
into the radar router). Best-effort and synchronous: a metering/recording hiccup
must never affect a tool's result, so every failure is swallowed at debug level.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("awcp.context_graph.client")


def record_checkpoint(radar_url: str, agent_id: str, *, step: str,
                      task_id: str = "", workflow_id: str = "", actor: str = "",
                      resume_pointer: str = "", context: Any = None,
                      payload: dict | None = None, timeout: float = 3.0) -> None:
    """POST one checkpoint to the radar. No-op without a radar_url/agent_id."""
    if not (radar_url and agent_id):
        return
    try:
        httpx.post(
            f"{radar_url.rstrip('/')}/agents/{agent_id}/checkpoint",
            json={
                "step": step, "task_id": task_id, "workflow_id": workflow_id,
                "actor": actor or agent_id, "resume_pointer": resume_pointer,
                "context": context, "payload": payload or {},
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — recording must never break a tool call
        log.debug("context_graph.client.record failed agent=%s step=%s err=%r",
                  agent_id, step, exc)
