"""Letta long-term memory — an optional, fail-open recall backend for the manager.

Postgres ``evidence.ledger`` is the per-run trail; Neo4j (``graph_store.py``) is the
graph view. This module adds the **durable, cross-run memory** layer the brochure
names as a platform partner: **Letta** (the open-source memory framework, formerly
MemGPT). Every governed step can be *remembered* into Letta's archival memory, and
the manager can *recall* the most relevant past knowledge for a focus query and
fold it into a recovery working set.

Design, identical in spirit to ``graph_store.py``:

  * **Additive + fail-open.** If Letta is off, unreachable, or unconfigured, every
    function is a silent no-op / empty result and the rest of the app is
    unaffected. Nothing requires Letta.
  * **Config-driven, nothing hardcoded.** Letta's REST surface has shifted across
    versions, so the base URL, auth, target agent, and the insert/search/health
    *paths* are all env-overridable — adapting to version drift is a config change,
    not a code change.
  * **Talks REST over httpx** (already a dependency) rather than pinning the Letta
    SDK, so there is no hard import to satisfy.

Recall is version-tolerant: it lists the target agent's archival passages (passing
a ``search`` hint when the server supports it) and then ranks them locally by
lexical overlap with the query, so it returns a meaningful, scored result against
any Letta that can list passages.

Env knobs
  AWCP_LETTA_ENABLED      default "true"  — master off-switch ("false" → disabled)
  AWCP_LETTA_BASE_URL     default "http://localhost:8283"
  AWCP_LETTA_AGENT_ID     REQUIRED to store/recall (the Letta agent that holds the
                          archival memory). Unset → enabled but inert, with a note.
  AWCP_LETTA_TOKEN        optional bearer token
  AWCP_LETTA_TIMEOUT      default "4" (seconds)
  AWCP_LETTA_INSERT_PATH  default "/v1/agents/{agent_id}/archival-memory"
  AWCP_LETTA_SEARCH_PATH  default "/v1/agents/{agent_id}/archival-memory"
  AWCP_LETTA_HEALTH_PATH  default "/v1/health/"
"""

from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger("awcp.context_graph.memory")


def _enabled() -> bool:
    return os.getenv("AWCP_LETTA_ENABLED", "true").lower() == "true"


def _base() -> str:
    return os.getenv("AWCP_LETTA_BASE_URL", "http://localhost:8283").rstrip("/")


def _agent() -> str:
    return os.getenv("AWCP_LETTA_AGENT_ID", "").strip()


def _timeout() -> float:
    try:
        return float(os.getenv("AWCP_LETTA_TIMEOUT", "4"))
    except Exception:  # noqa: BLE001
        return 4.0


def _headers() -> dict:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    tok = os.getenv("AWCP_LETTA_TOKEN", "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _path(env: str, default: str) -> str:
    return os.getenv(env, default).replace("{agent_id}", _agent())


# ── HTTP (every call guarded; returns None on any failure) ────────────────────
def _request(method: str, path: str, json_body: dict | None = None,
             params: dict | None = None):
    try:
        import httpx
        url = _base() + path
        resp = httpx.request(method, url, headers=_headers(), json=json_body,
                             params=params, timeout=_timeout())
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — Letta is never required
        log.debug("memory.%s %s failed err=%r", method, path, exc)
        return None


# ── status ────────────────────────────────────────────────────────────────────
def status() -> dict:
    """Connection status for the Radar/UI. Never raises."""
    if not _enabled():
        return {"enabled": False, "backend": "letta", "connected": False,
                "note": "AWCP_LETTA_ENABLED=false"}
    if not _agent():
        return {"enabled": False, "backend": "letta", "connected": False,
                "note": "set AWCP_LETTA_AGENT_ID to enable long-term memory"}
    health = _request("GET", _path("AWCP_LETTA_HEALTH_PATH", "/v1/health/"))
    connected = health is not None
    return {"enabled": True, "backend": "letta", "connected": connected,
            "note": "" if connected else f"unreachable at {_base()}",
            "detail": {"base_url": _base(), "agent_id": _agent()}}


def available() -> bool:
    return _enabled() and bool(_agent())


# ── remember (write) ───────────────────────────────────────────────────────────
def remember(node) -> bool:
    """Persist one governed step into Letta archival memory. Best-effort; returns
    True iff the insert was accepted. A ``ContextNode`` (duck-typed) is expected."""
    if not available():
        return False
    try:
        from awcp.context_graph.manager import node_text
        text = node_text(node)
    except Exception:  # noqa: BLE001
        text = getattr(node, "step", "") or ""
    if not text:
        return False
    # A compact, self-describing memory line so recall is meaningful out of context.
    line = (f"[{getattr(node, 'workflow_id', '')}] "
            f"{getattr(node, 'agent_id', '')} :: {getattr(node, 'step', '')} :: {text}")
    out = _request("POST", _path("AWCP_LETTA_INSERT_PATH",
                                 "/v1/agents/{agent_id}/archival-memory"),
                   json_body={"text": line})
    return out is not None


# ── recall (read) ──────────────────────────────────────────────────────────────
_WORD = re.compile(r"[a-z0-9]+")


def _words(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower()))


def _passages(data) -> list[dict]:
    """Pull a passage list out of whatever shape this Letta version returns."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (data.get("archival_memory") or data.get("passages")
                 or data.get("data") or data.get("results") or [])
    else:
        items = []
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str):
            out.append({"text": it})
    return out


def recall(query: str, workflow_id: str = "", agent_id: str = "",
           limit: int = 5) -> list[dict]:
    """Return up to ``limit`` past memories most relevant to ``query``.

    Lists the target agent's archival passages (sending ``search=query`` as a hint
    for servers that support it), then ranks locally by lexical overlap so the
    result is scored and version-tolerant. Returns [] when memory is off/unreachable.
    Each item: ``{"text","score","ts","agent_id","id"}``.
    """
    if not (available() and query):
        return []
    params = {"search": query, "limit": max(limit * 5, 25)}
    data = _request("GET", _path("AWCP_LETTA_SEARCH_PATH",
                                 "/v1/agents/{agent_id}/archival-memory"),
                    params=params)
    if data is None:
        return []
    qwords = _words(query)
    scored: list[dict] = []
    for p in _passages(data):
        text = str(p.get("text") or p.get("content") or "")
        if not text:
            continue
        pw = _words(text)
        score = (len(qwords & pw) / len(qwords)) if qwords else 0.0
        ts = p.get("created_at") or p.get("ts") or 0
        if isinstance(ts, str):
            ts = 0.0  # leave ISO strings as 0; ordering uses score anyway
        scored.append({"text": text, "score": round(float(score), 4),
                       "ts": float(ts or 0.0), "agent_id": agent_id,
                       "id": p.get("id", "")})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
