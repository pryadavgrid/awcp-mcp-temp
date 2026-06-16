"""Token-aware LLM gateway — enforcement way #5.

A thin Ollama-compatible reverse proxy that sits IN FRONT of the model runtime.
Every model call is metered and gated at the source: if the calling agent is
over its token budget, the gateway returns 429 and NEVER forwards the call — so
the agent gets no completion and physically cannot spend another token. Because
the limit is fundamentally about model consumption, intercepting the model call
is the one hard wall that works for ANY agent topology (local, remote,
containerised, cooperative or not).

Dynamic for every agent, nothing hardcoded:
  * the agent is identified per request, in priority order:
      1. the `X-AWCP-Agent-Id` header,
      2. the `?awcp_agent=<id>` query param,
      3. best-effort: the source process's pid mapped to a registry entry;
  * budgets are resolved by laminar positionally (operator override -> declared
    token_budget -> risk tier -> default) — no agent or model name appears here;
  * crossing the budget while metering fires the SAME laminar breach chain as
    every other layer (graceful degrade + process/remote hard-stop).

To put an agent behind it, point its model base URL at  <radar>/llm  (e.g.
http://localhost:8090/llm) and, if it can, send its id in X-AWCP-Agent-Id.
Mounted on the radar app so it shares the one in-memory token ledger.
"""

from __future__ import annotations

import json
import logging
import os
import time

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from awcp.radar.store import REGISTRY

try:                                            # laminar is optional; gateway still proxies
    from awcp import laminar as _laminar
    _LAMINAR = True
except Exception:                               # noqa: BLE001
    _LAMINAR = False

log = logging.getLogger("awcp.radar")

# Upstream model runtime the gateway fronts (Ollama by default).
UPSTREAM_BASE = os.getenv("AWCP_GATEWAY_UPSTREAM", os.getenv("OLLAMA_BASE", "http://localhost:11434"))
AGENT_HEADER = "x-awcp-agent-id"
# When true, a gated call with no identifiable agent is refused (fail-closed).
# Default false so unidentified traffic still flows (the gateway is also a plain
# proxy); only IDENTIFIED, over-budget agents are ever blocked.
REQUIRE_AGENT = os.getenv("AWCP_GATEWAY_REQUIRE_AGENT", "false").lower() == "true"
GATEWAY_TIMEOUT = float(os.getenv("AWCP_GATEWAY_TIMEOUT", "300"))

# Upstream paths that CONSUME tokens — gated + metered. Everything else (model
# list, health, pulls) passes straight through ungated.
_GATED_SUFFIXES = (
    "api/chat", "api/generate", "api/embeddings", "api/embed",
    "v1/chat/completions", "v1/completions", "v1/embeddings",
)

gateway_router = APIRouter(prefix="/llm", tags=["llm-gateway"])


# Cache the (expensive, privilege-sensitive) system-wide socket enumeration so a
# headerless request flood can't force a net_connections() syscall per call.
_CONN_CACHE_TTL = float(os.getenv("AWCP_GATEWAY_CONN_TTL", "2"))
_conn_cache: dict = {"ts": 0.0, "port2pid": {}}


def _port_to_pid() -> dict:
    now = time.time()
    if now - _conn_cache["ts"] <= _CONN_CACHE_TTL:
        return _conn_cache["port2pid"]
    mapping: dict = {}
    try:
        import psutil
        for c in psutil.net_connections(kind="inet"):
            if c.laddr and c.pid:
                mapping[c.laddr.port] = c.pid
    except Exception:                           # noqa: BLE001 — needs privilege; best-effort
        pass
    _conn_cache["ts"] = now
    _conn_cache["port2pid"] = mapping
    return mapping


def _agent_from_socket(request: Request) -> str:
    """Best-effort: map the client's source port -> owning pid -> registry entry.
    Lets the gateway attribute a LOCAL agent that sends no header. Cached (TTL)
    so it is cheap under load; returns "" on any miss (remote clients, no match,
    or insufficient privilege for psutil)."""
    try:
        client = request.client
        if not client:
            return ""
        pid = _port_to_pid().get(client.port)
        if not pid:
            return ""
        for e in REGISTRY.all():
            if getattr(e, "pid", None) == pid:
                return e.id
    except Exception:                           # noqa: BLE001
        pass
    return ""


def _resolve_agent(request: Request) -> str:
    aid = request.headers.get(AGENT_HEADER) or request.query_params.get("awcp_agent")
    if aid:
        return aid.strip()
    return _agent_from_socket(request)


def _extract_usage(resp: httpx.Response) -> tuple[int, int, str]:
    """Pull (input, output, model) token usage from a model response — Ollama
    (prompt_eval_count/eval_count, incl. the final line of a streamed NDJSON
    body) or OpenAI-compatible (usage.prompt_tokens/completion_tokens)."""
    def _from_obj(d: dict) -> tuple[int, int, str]:
        if not isinstance(d, dict):
            return 0, 0, ""
        if "prompt_eval_count" in d or "eval_count" in d:        # Ollama
            return int(d.get("prompt_eval_count") or 0), int(d.get("eval_count") or 0), str(d.get("model") or "")
        u = d.get("usage") or {}                                  # OpenAI-compatible
        if u:
            return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), str(d.get("model") or "")
        return 0, 0, str(d.get("model") or "")

    try:
        return _from_obj(resp.json())
    except Exception:                                            # noqa: BLE001
        pass
    try:                                                          # streamed NDJSON -> last line
        lines = [ln for ln in resp.text.splitlines() if ln.strip()]
        if lines:
            return _from_obj(json.loads(lines[-1]))
    except Exception:                                            # noqa: BLE001
        pass
    return 0, 0, ""


@gateway_router.api_route("/{upstream:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def proxy(upstream: str, request: Request) -> Response:
    agent_id = _resolve_agent(request)
    gated = any(upstream == s or upstream.endswith(s) for s in _GATED_SUFFIXES)

    if gated and not agent_id and REQUIRE_AGENT:
        return JSONResponse(status_code=400, content={
            "error": "AWCP gateway: missing agent identity (set X-AWCP-Agent-Id)"})

    # ── HARD WALL ─────────────────────────────────────────────────────────────
    # Over budget -> the model call never happens, so no token is spent. The
    # budget check is FAIL-OPEN by design: a control-plane error must never brick
    # all model traffic, so on an unexpected error we log and let the call
    # through rather than 500 the agent.
    if gated and agent_id and _LAMINAR:
        blocked, ev = False, None
        try:
            if _laminar.is_exhausted(agent_id):
                blocked, ev = True, _laminar.budget_state(agent_id)
        except Exception as exc:                                 # noqa: BLE001
            log.warning("radar.llm_gateway.check_error agent_id=%s error=%r", agent_id, exc)
        if blocked:
            log.warning("radar.llm_gateway.blocked agent_id=%s used=%s/%s",
                        agent_id, (ev or {}).get("used_tokens"), (ev or {}).get("budget_tokens"))
            return JSONResponse(status_code=429, content={
                "error": "token budget exhausted — model access blocked by the AWCP control plane",
                "agent_id": agent_id, "budget": ev or {}})

    body = await request.body()
    drop = {"host", "content-length", AGENT_HEADER}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in drop}
    try:
        async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
            up = await client.request(
                request.method, f"{UPSTREAM_BASE}/{upstream}",
                content=body, headers=fwd_headers, params=dict(request.query_params))
    except Exception as exc:                                     # noqa: BLE001
        return JSONResponse(status_code=502, content={"error": f"upstream model error: {exc}"})

    # ── METER at the source ───────────────────────────────────────────────────
    # Count this call's tokens for the agent and run the budget evaluation +
    # breach chain — so an autonomous agent is accounted for the instant it
    # spends, and its NEXT call hits the wall above once it crosses the limit.
    if gated and agent_id and _LAMINAR:
        try:
            tin, tout, model = _extract_usage(up)
            if tin or tout:
                _laminar.record_usage(agent_id, model, tin, tout)
        except Exception as exc:        # noqa: BLE001 — accounting must never break the response
            log.warning("radar.llm_gateway.meter_error agent_id=%s error=%r", agent_id, exc)

    return Response(content=up.content, status_code=up.status_code,
                    media_type=up.headers.get("content-type"))
