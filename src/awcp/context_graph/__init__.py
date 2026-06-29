"""AWCP Context Graph — a durable, tamper-evident trail of governed steps.

Every governed step an agent takes (a tool call, a route decision, a generation)
is recorded as a *node*; consecutive steps in one run form *edges*. Each node
carries a ``resume_pointer`` (where to resume after it) and a ``context_hash``
(a fingerprint of what it acted on), and the nodes are chained with
``prev_hash`` / ``row_hash`` so the trail is tamper-evident.

This package is the piece that finally *writes* that trail: the ``evidence.ledger``
table already had the columns, but nothing populated them. See ``README.md``.

Public surface:
  * ``record_checkpoint(...)``  — record a node (in-radar-process callers)
  * ``client.record_checkpoint(...)`` — record a node over HTTP (other processes)
  * ``router``                  — the FastAPI APIRouter to mount on the radar
  * ``graph_for_workflow(...)`` / ``nodes_for_agent(...)`` / ``recent_nodes(...)`` — reads
  * ``manager``                 — the smart-memory layer (relevance / staleness /
    token-budget working set) reasoning over the trail
  * ``memory``                  — optional Letta long-term recall backend (fail-open)
  * models: ``ContextNode``, ``ContextGraph``, ``CheckpointRequest``,
    ``ScoredNode``, ``WorkingSet``, ``StaleReport``, ``RelevanceReport``
"""

from __future__ import annotations

from awcp.context_graph.api import router
from awcp.context_graph.models import (
    ChainVerification,
    CheckpointRequest,
    ContextGraph,
    ContextNode,
    MemoryRecallRequest,
    MemoryStatus,
    RelevanceReport,
    ScoredNode,
    StaleReport,
    WorkingSet,
)
from awcp.context_graph.store import (
    graph_for_workflow,
    nodes_for_agent,
    record_checkpoint,
    recent_nodes,
)
from awcp.context_graph.verify import verify_chain
from awcp.context_graph import graph_store, manager, memory

__all__ = [
    "router",
    "record_checkpoint",
    "graph_for_workflow",
    "nodes_for_agent",
    "recent_nodes",
    "verify_chain",
    "graph_store",
    "manager",
    "memory",
    "CheckpointRequest",
    "ContextGraph",
    "ContextNode",
    "ChainVerification",
    "ScoredNode",
    "WorkingSet",
    "StaleReport",
    "RelevanceReport",
    "MemoryStatus",
    "MemoryRecallRequest",
]
