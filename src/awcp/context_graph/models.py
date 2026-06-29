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


# ── Context Graph MANAGER (the smart-memory layer over the trail) ─────────────
# The trail above is the *receipt book* (what happened). The manager reasons over
# it: how RELEVANT is each step to the current point, which steps are STALE, and
# which fit a context-window TOKEN BUDGET for recovery/resume.

class ScoredNode(BaseModel):
    """One context node enriched with the manager's judgements about it."""

    node: ContextNode
    relevance: float = 0.0                       # 0..1 combined score
    components: dict = Field(default_factory=dict)  # explainable score breakdown
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)  # e.g. ["aged","superseded"]
    tokens: int = 0                              # estimated context-window cost
    source: str = "ledger"                       # "ledger" | "memory" (long-term recall)


class RelevanceReport(BaseModel):
    """Every node in a workflow, scored for relevance (most-relevant first)."""

    workflow_id: str
    focus: str = ""                              # optional focus query the score was relative to
    count: int = 0
    nodes: list[ScoredNode] = Field(default_factory=list)


class StaleReport(BaseModel):
    """Which of a workflow's nodes are stale, and why."""

    workflow_id: str
    total: int = 0
    fresh: int = 0
    stale: int = 0
    nodes: list[ScoredNode] = Field(default_factory=list)  # the stale ones, with reasons


class WorkingSet(BaseModel):
    """The relevance-ranked, staleness-filtered, budget-fitted slice of context a
    recovering workflow should actually carry forward."""

    workflow_id: str
    focus: str = ""
    budget_tokens: int = 0
    used_tokens: int = 0
    selected: list[ScoredNode] = Field(default_factory=list)  # chronological order
    dropped: int = 0                             # fresh nodes that didn't fit the budget
    excluded_stale: int = 0                      # nodes left out because they were stale
    resume_pointer: str = ""                     # anchor to resume from (latest fresh node)
    memory: list[ScoredNode] = Field(default_factory=list)    # long-term recall folded in
    note: str = ""


class MemoryStatus(BaseModel):
    """Connection status of the optional Letta long-term-memory backend."""

    enabled: bool = False        # False ⇒ Letta off / unreachable (everything no-ops)
    backend: str = "letta"
    connected: bool = False
    note: str = ""
    detail: dict = Field(default_factory=dict)


class MemoryRecallRequest(BaseModel):
    """Body for ``POST /context-graph/memory/recall``."""

    query: str
    workflow_id: str = ""
    agent_id: str = ""
    limit: int = 5
