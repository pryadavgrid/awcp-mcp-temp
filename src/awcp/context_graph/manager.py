"""Context Graph MANAGER — the smart-memory layer over the governed-step trail.

The trail (``store.py``) is the *receipt book*: a tamper-evident record of what
each agent did. This module is the brochure's **Context Graph Manager** — it
*reasons* over that trail instead of just storing it:

  * **relevance scoring**     — how relevant is each recorded step to the current
    point in the workflow (recency · step kind · outcome · focus-query overlap)?
  * **stale-context detection** — which steps no longer reflect live state and so
    must NOT seed a recovery (aged out · superseded by a newer snapshot · a
    dead/blocked branch)?
  * **token-budget management** — given a context-window token budget, which
    relevant, fresh steps actually fit? Produces the **working set** a recovering
    workflow should carry forward, plus the resume anchor.

Optionally it folds in **long-term recall** from Letta (``memory.py``) so recovery
can pull durable cross-run knowledge, not just this run's steps.

Everything here is a pure-ish read over nodes from ``store`` — it writes nothing,
raises nothing into a caller (the API layer wraps it), and degrades to sane empty
results when there is no data. Nothing is hardcoded: every weight, half-life, and
threshold is env-tunable, mirroring ``radar/policy.py`` / ``laminar/budget.py``.
"""

from __future__ import annotations

import logging
import os
import re

from awcp.context_graph import store
from awcp.context_graph.models import (
    ContextNode,
    RelevanceReport,
    ScoredNode,
    StaleReport,
    WorkingSet,
)

log = logging.getLogger("awcp.context_graph.manager")

# ── tunables (env-overridable; the defaults are only a seed) ───────────────────
RECENCY_HALFLIFE_S = float(os.getenv("AWCP_CTX_RECENCY_HALFLIFE_S", "1800"))  # 30 min
STALE_MAX_AGE_S = float(os.getenv("AWCP_CTX_STALE_MAX_AGE_S", "3600"))        # 1 h
DEFAULT_TOKEN_BUDGET = int(os.getenv("AWCP_CTX_TOKEN_BUDGET", "4000"))
_ENCODING = os.getenv("AWCP_CTX_ENCODING", "cl100k_base")

# Relevance weights — normalised over whichever components are active for a query.
W_RECENCY = float(os.getenv("AWCP_CTX_W_RECENCY", "0.40"))
W_STEP = float(os.getenv("AWCP_CTX_W_STEP", "0.15"))
W_OUTCOME = float(os.getenv("AWCP_CTX_W_OUTCOME", "0.15"))
W_FOCUS = float(os.getenv("AWCP_CTX_W_FOCUS", "0.30"))

# How much each kind of step is worth as carried context (prefix before the ':').
# Unknown kinds get a neutral mid weight, so a new step type is never zero-scored.
_STEP_WEIGHTS = {
    "generate": 1.0, "synthesize": 1.0, "answer": 1.0,
    "route": 0.8, "llm": 0.8, "plan": 0.8,
    "tool": 0.9, "web_search": 0.9,
    "checkpoint": 0.5,
}
# State-producing steps: a later one of the SAME kind in the SAME task supersedes
# the earlier snapshot. Tool calls are NOT here — they legitimately repeat (two
# searches with different queries are both valid), so they're never "superseded".
_STATE_STEPS = {"route", "generate", "synthesize", "answer", "plan"}


# ── token counting (self-contained, fail-open; tiktoken if present) ───────────
_enc = None
_enc_tried = False


def _encoder():
    global _enc, _enc_tried
    if _enc_tried:
        return _enc
    _enc_tried = True
    try:
        import tiktoken
        _enc = tiktoken.get_encoding(_ENCODING)
    except Exception:  # noqa: BLE001 — tiktoken optional; fall back to a heuristic
        _enc = None
    return _enc


def estimate_tokens(text: str) -> int:
    """Best-effort token count for a chunk of context text. Uses tiktoken when
    available, else the same ~chars/4 heuristic as laminar's FallbackEstimator."""
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # noqa: BLE001
            pass
    return max(1, len(text) // 4)


# ── helpers ──────────────────────────────────────────────────────────────────
def _step_kind(step: str) -> str:
    return (step or "").split(":", 1)[0].strip().lower()


def node_text(n: ContextNode) -> str:
    """Flatten a node's meaningful content for token counting + focus matching."""
    parts = [n.step, n.actor, n.resume_pointer]
    for k, v in (n.payload or {}).items():
        if k.startswith("_"):
            continue
        parts.append(f"{k}={v}")
    return " ".join(str(p) for p in parts if p)


_WORD = re.compile(r"[a-z0-9]+")


def _words(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower()))


def _is_dead_branch(n: ContextNode) -> bool:
    """A step whose action did NOT take effect — blocked by the gate, denied, or
    errored. Its context shouldn't seed recovery as if it were live state."""
    p = n.payload or {}
    if str(p.get("outcome", "")).lower() in {"blocked", "denied", "failed", "error"}:
        return True
    if str(p.get("decision", "")).lower() in {"deny", "denied", "block", "blocked"}:
        return True
    return bool(p.get("error"))


# ── staleness ────────────────────────────────────────────────────────────────
def _stale_reasons(n: ContextNode, idx: int, nodes: list[ContextNode],
                   superseded_idx: set[int], latest_ts: float) -> list[str]:
    reasons: list[str] = []
    if idx in superseded_idx:
        reasons.append("superseded")
    if STALE_MAX_AGE_S > 0 and (latest_ts - n.ts) > STALE_MAX_AGE_S:
        reasons.append("aged")
    if _is_dead_branch(n):
        reasons.append("dead_branch")
    return reasons


def _superseded(nodes: list[ContextNode]) -> set[int]:
    """Indices of state-producing snapshots replaced by a later one in the same
    task. Keyed by (task_id, step-kind); the last occurrence wins, earlier ones
    are superseded."""
    last_for: dict[tuple[str, str], int] = {}
    for i, n in enumerate(nodes):
        kind = _step_kind(n.step)
        if kind in _STATE_STEPS:
            last_for[(n.task_id, kind)] = i
    keep = set(last_for.values())
    return {i for i, n in enumerate(nodes)
            if _step_kind(n.step) in _STATE_STEPS and i not in keep}


# ── relevance ────────────────────────────────────────────────────────────────
def _score(n: ContextNode, latest_ts: float, focus_words: set[str]) -> tuple[float, dict]:
    age = max(0.0, latest_ts - n.ts)
    recency = 0.5 ** (age / RECENCY_HALFLIFE_S) if RECENCY_HALFLIFE_S > 0 else 1.0
    step = _STEP_WEIGHTS.get(_step_kind(n.step), 0.6)
    outcome = 0.3 if _is_dead_branch(n) else 1.0

    weights = {"recency": W_RECENCY, "step": W_STEP, "outcome": W_OUTCOME}
    components = {"recency": round(recency, 4), "step": round(step, 4),
                 "outcome": round(outcome, 4)}
    if focus_words:
        nw = _words(node_text(n))
        focus = (len(focus_words & nw) / len(focus_words)) if focus_words else 0.0
        weights["focus"] = W_FOCUS
        components["focus"] = round(focus, 4)

    total_w = sum(weights.values()) or 1.0
    score = sum(weights[k] * components[k] for k in weights) / total_w
    return round(score, 4), components


def _scored_nodes(nodes: list[ContextNode], focus: str = "") -> list[ScoredNode]:
    if not nodes:
        return []
    latest_ts = max(n.ts for n in nodes)
    focus_words = _words(focus)
    superseded_idx = _superseded(nodes)
    out: list[ScoredNode] = []
    for i, n in enumerate(nodes):
        score, comp = _score(n, latest_ts, focus_words)
        reasons = _stale_reasons(n, i, nodes, superseded_idx, latest_ts)
        out.append(ScoredNode(
            node=n, relevance=score, components=comp,
            stale=bool(reasons), stale_reasons=reasons,
            tokens=estimate_tokens(node_text(n)),
        ))
    return out


# ── public API (used by api.py; each call is read-only + fail-open) ───────────
def relevance(workflow_id: str, focus: str = "", limit: int = 500) -> RelevanceReport:
    """Every node in a workflow, scored and ordered most-relevant first."""
    nodes = store._read_nodes(workflow_id=workflow_id, limit=limit)
    scored = _scored_nodes(nodes, focus)
    scored.sort(key=lambda s: s.relevance, reverse=True)
    return RelevanceReport(workflow_id=workflow_id, focus=focus,
                           count=len(scored), nodes=scored)


def stale(workflow_id: str, limit: int = 500) -> StaleReport:
    """Report which of a workflow's nodes are stale and why."""
    nodes = store._read_nodes(workflow_id=workflow_id, limit=limit)
    scored = _scored_nodes(nodes)
    stale_nodes = [s for s in scored if s.stale]
    return StaleReport(workflow_id=workflow_id, total=len(scored),
                       fresh=len(scored) - len(stale_nodes),
                       stale=len(stale_nodes), nodes=stale_nodes)


def working_set(workflow_id: str, budget_tokens: int | None = None, focus: str = "",
                include_memory: bool = True, limit: int = 500) -> WorkingSet:
    """Build the relevance-ranked, staleness-filtered, budget-fitted slice of
    context a recovering workflow should carry forward.

    Algorithm:
      1. score + flag every node;
      2. keep only FRESH nodes (stale ones are excluded from recovery state);
      3. always seat the newest fresh node first — it is the resume anchor;
      4. greedily add the rest by relevance until the next node would bust budget;
      5. (optional) spend any leftover budget on Letta long-term recall for `focus`;
      6. return the selection in chronological order (reads top-to-bottom).
    """
    budget = int(budget_tokens) if budget_tokens and budget_tokens > 0 else DEFAULT_TOKEN_BUDGET
    nodes = store._read_nodes(workflow_id=workflow_id, limit=limit)
    scored = _scored_nodes(nodes, focus)

    fresh = [s for s in scored if not s.stale]
    excluded_stale = len(scored) - len(fresh)
    resume_pointer = ""
    if scored:
        # anchor = newest node overall (even if stale we still know where we were)
        anchor = max(scored, key=lambda s: s.node.ts)
        resume_pointer = anchor.node.resume_pointer or ""

    # Order candidates by relevance, but guarantee the newest fresh node is seated.
    selected: list[ScoredNode] = []
    used = 0
    if fresh:
        newest_fresh = max(fresh, key=lambda s: s.node.ts)
        ordered = [newest_fresh] + sorted(
            (s for s in fresh if s is not newest_fresh),
            key=lambda s: s.relevance, reverse=True,
        )
        for s in ordered:
            if not selected:  # always seat the anchor, budget or not
                selected.append(s)
                used += s.tokens
                continue
            if used + s.tokens > budget:
                continue
            selected.append(s)
            used += s.tokens

    dropped = len(fresh) - len(selected)

    # Spend leftover budget on durable long-term recall (best-effort, fail-open).
    memory: list[ScoredNode] = []
    if include_memory and focus and used < budget:
        memory = _recall_as_scored(focus, workflow_id, budget - used)
        used += sum(m.tokens for m in memory)

    selected.sort(key=lambda s: s.node.ts)  # chronological for readability
    note = (f"{len(selected)} step(s) fit in {budget} tokens; "
            f"{dropped} dropped, {excluded_stale} stale excluded")
    if memory:
        note += f"; +{len(memory)} long-term memory"
    return WorkingSet(
        workflow_id=workflow_id, focus=focus, budget_tokens=budget, used_tokens=used,
        selected=selected, dropped=dropped, excluded_stale=excluded_stale,
        resume_pointer=resume_pointer, memory=memory, note=note,
    )


def _recall_as_scored(query: str, workflow_id: str, budget_left: int) -> list[ScoredNode]:
    """Pull long-term memories from Letta and wrap them as ScoredNodes so the
    working set can present ledger steps and recalled knowledge uniformly. Returns
    [] when Letta is off or anything goes wrong."""
    try:
        from awcp.context_graph import memory as _mem
        items = _mem.recall(query, workflow_id=workflow_id, limit=10)
    except Exception:  # noqa: BLE001 — long-term recall is never required
        return []
    out: list[ScoredNode] = []
    used = 0
    for it in items or []:
        text = str(it.get("text") or "")
        if not text:
            continue
        toks = estimate_tokens(text)
        if used + toks > budget_left:
            break
        node = ContextNode(
            ts=float(it.get("ts") or 0.0), workflow_id=workflow_id,
            agent_id=str(it.get("agent_id") or ""), step="memory:recall",
            actor="letta", payload={"text": text, "memory_score": it.get("score")},
        )
        out.append(ScoredNode(node=node, relevance=float(it.get("score") or 0.0),
                              components={"memory": float(it.get("score") or 0.0)},
                              tokens=toks, source="memory"))
        used += toks
    return out
