"""Content hashing for context-graph nodes.

A context-graph node is one governed step an agent took. Two hashes give it
identity and make the trail tamper-evident:

  * ``context_hash`` — a fingerprint of the *state the step acted on* (its inputs
    / context). Two steps that saw the same context produce the same
    ``context_hash``, which is what lets the graph spot a repeat or a divergence.
  * ``row_hash``     — ``sha256(prev_row_hash + canonical(row))``. Each node folds
    in its predecessor's hash, so editing any past node breaks every hash after
    it. This is the evidence-ledger's tamper chain.

``row_hash`` uses the **exact same formula** as ``awcp.radar.db._row_hash`` so the
checkpoint rows we write chain seamlessly with the rest of the evidence ledger —
one continuous chain, not a parallel one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical(obj: Any) -> str:
    """Stable JSON encoding: sorted keys + str fallback, so equal content always
    serialises to the same bytes (and therefore hashes equal).

    This MUST match ``awcp.radar.db``'s body production exactly
    (``json.dumps(data, sort_keys=True, default=str)``) — both writers append to
    the same evidence chain, so whole-ledger verification (``verify.py``) re-derives
    every row with this one function regardless of which writer wrote it. Do not
    add ``ensure_ascii=False`` here: db.py uses the default (True)."""
    return json.dumps(obj, sort_keys=True, default=str)


def context_hash(context: Any) -> str:
    """Fingerprint the context/inputs a step acted on. ``None`` → empty string
    (a step with no recorded context simply has no context fingerprint)."""
    if context is None:
        return ""
    return hashlib.sha256(canonical(context).encode("utf-8")).hexdigest()


def row_hash(prev_hash: str | None, body: str) -> str:
    """Tamper-evident chain link. Identical to ``awcp.radar.db._row_hash`` so
    checkpoint rows and ordinary evidence rows share one continuous chain."""
    return hashlib.sha256(((prev_hash or "") + body).encode("utf-8")).hexdigest()
