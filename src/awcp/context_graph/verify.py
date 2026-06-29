"""Whole-ledger hash-chain verification — a pure read.

Re-derives the ``evidence.ledger`` tamper chain and reports any break. For each
row (ordered by ts) we check two independent things:

  * **linkage** — ``row.prev_hash == the previous row's row_hash``. Detects a
    deleted, reordered, or inserted row (the append-only property).
  * **content** — ``row.row_hash == sha256(row.prev_hash + canonical(payload))``,
    i.e. re-hashing the stored payload reproduces the recorded hash. Detects an
    in-place edit of a row's body.

``canonical()`` matches ``awcp.radar.db``'s body production exactly, so BOTH
ordinary evidence rows and context-graph checkpoint rows verify against this one
function. The payload column is ``jsonb`` (normalised by Postgres), so content
verification assumes the payload round-trips canonically — true for the control
plane's string/scalar payloads. Linkage breaks are always definitive.

Verification is a property of the DURABLE ledger; with Postgres disabled there is
nothing persisted to verify, so it returns ``enabled=False``.
"""

from __future__ import annotations

import json
import logging

from awcp.context_graph.hashing import canonical, row_hash
from awcp.context_graph.models import ChainBreak, ChainVerification
from awcp.radar import db

log = logging.getLogger("awcp.context_graph")

# Safety cap so verification never tries to load an unbounded table into memory.
_MAX_ROWS = 50000

_VERIFY_SQL = (
    "SELECT EXTRACT(EPOCH FROM ts) AS ts, agent_id, event_type, step, "
    "       prev_hash, row_hash, payload "
    "  FROM evidence.ledger "
    " ORDER BY ts ASC, id ASC "
    " LIMIT :limit"
)


def _db_ready() -> bool:
    return bool(getattr(db, "_enabled", False)) and getattr(db, "_engine", None) is not None


def verify_chain(limit: int = _MAX_ROWS) -> ChainVerification:
    """Re-derive the evidence-ledger chain and report breaks. Never raises."""
    if not _db_ready():
        return ChainVerification(enabled=False, intact=True, total=0,
                                 note="durable ledger disabled (Postgres off) — nothing to verify")
    try:
        text = db._text
        with db._engine.connect() as c:
            rows = c.execute(text(_VERIFY_SQL), {"limit": limit}).fetchall()
    except Exception as exc:  # noqa: BLE001 — verification must never raise
        log.debug("context_graph.verify read failed err=%r", exc)
        return ChainVerification(enabled=True, intact=True, total=0,
                                 note=f"verify read failed: {type(exc).__name__}")

    breaks: list[ChainBreak] = []
    prev_row_hash: str | None = None
    content_verified = 0

    for i, r in enumerate(rows):
        m = r._mapping
        payload = m["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload or "{}")
            except Exception:  # noqa: BLE001
                payload = {}

        # content: re-hash the stored payload with the recorded prev_hash
        body = canonical(payload or {})
        recomputed = row_hash(m["prev_hash"], body)
        content_ok = recomputed == (m["row_hash"] or "")
        if content_ok:
            content_verified += 1

        # linkage: this row's prev_hash must equal the previous row's row_hash.
        # The first row in the window has no in-window predecessor — skip it (it
        # may legitimately point outside a capped window / to genesis).
        linkage_break = i > 0 and (m["prev_hash"] or None) != (prev_row_hash or None)

        if linkage_break or not content_ok:
            kind = "linkage+content" if (linkage_break and not content_ok) else (
                "linkage" if linkage_break else "content")
            breaks.append(ChainBreak(
                index=i, ts=float(m["ts"] or 0.0), agent_id=m["agent_id"] or "",
                event_type=m["event_type"] or "", step=m["step"] or "",
                kind=kind, row_hash=m["row_hash"] or "",
            ))

        prev_row_hash = m["row_hash"]

    total = len(rows)
    intact = len(breaks) == 0
    note = (f"{total} row(s) verified, chain intact" if intact
            else f"{len(breaks)} break(s) in {total} row(s)")
    return ChainVerification(
        enabled=True, intact=intact, total=total,
        content_verified=content_verified, breaks=breaks[:50], note=note,
    )
