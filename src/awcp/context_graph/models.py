"""Pydantic models for the context graph.

The graph is intentionally thin: a *node* is one governed step, an *edge* is the
chronological succession of steps inside one workflow (step N → step N+1). The
tamper chain (``prev_hash`` / ``row_hash``) is carried on each node so the graph
is auditable, but lineage edges are the human-meaningful "this led to that".
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CheckpointRequest(BaseModel):
    """Body for ``POST /agents/{agent_id}/checkpoint`` — one governed step to
    record. Everything except ``step`` is optional so callers can record as much
    or as little as they have."""

    step: str                                  # e.g. "tool:web_search", "route", "generate"
    task_id: str = ""                          # the run this step belongs to
    workflow_id: str = ""                      # Temporal/run id; falls back to task_id/agent_id
    actor: str = ""                            # who acted (agent id, "operator", "gate")
    resume_pointer: str = ""                   # opaque cursor: where to resume after this step
    context: Any = None                        # the state/inputs this step acted on (gets hashed)
    context_hash: str = ""                     # precomputed; if absent, derived from `context`
    payload: dict = Field(default_factory=dict)  # any extra detail to keep on the node


class ContextNode(BaseModel):
    """One node (one governed step) in an agent's context graph."""

    ts: float
    workflow_id: str
    agent_id: str
    task_id: str = ""
    step: str = ""
    actor: str = ""
    resume_pointer: str = ""
    context_hash: str = ""
    prev_hash: str | None = None               # tamper chain: the previous ledger row's hash
    row_hash: str = ""                          # this node's stable id + chain link
    payload: dict = Field(default_factory=dict)


class ContextGraph(BaseModel):
    """A workflow's ordered chain of nodes plus its step→step edges."""

    workflow_id: str
    count: int = 0
    nodes: list[ContextNode] = Field(default_factory=list)
    # edges connect consecutive steps by their row_hash (stable node ids):
    #   [{"from": <row_hash>, "to": <row_hash>, "step": <to.step>}]
    edges: list[dict] = Field(default_factory=list)


class ChainBreak(BaseModel):
    """One row where the tamper chain failed to verify."""

    index: int                  # position in the time-ordered ledger
    ts: float = 0.0
    agent_id: str = ""
    event_type: str = ""
    step: str = ""
    kind: str = ""              # "linkage" | "content" | "linkage+content"
    row_hash: str = ""


class ChainVerification(BaseModel):
    """Result of re-deriving the whole evidence-ledger hash chain."""

    enabled: bool = True        # False ⇒ no durable ledger (Postgres off) to verify
    intact: bool = True         # no linkage or content breaks
    total: int = 0              # rows checked
    content_verified: int = 0   # rows whose row_hash re-hashed exactly
    breaks: list[ChainBreak] = Field(default_factory=list)
    note: str = ""
