"""AWCP Gateway — one FastAPI app exposing two clearly separated route groups.

  USER routes (/user/*)
      GET  /user/agents   list every agent in the external bundle (dynamic)
      POST /user/ask       run a chosen agent on a prompt and return the result

  AWCP control-plane routes (mounted at the ROOT, so everything is one port)
      /agents , /tasks/execution/* , /events , /healthz   the Registry (radar):
         discovery, onboarding, the write-action gate, the agent-task execution
         workflows the bundle agents report into, and the web UI at "/"
      /laminar/*                                            the token monitor
         (awcp.laminar): per-agent token usage + budgets + the /laminar/ui board

The bundle agents (LangGraph / CrewAI / PydanticAI / arXiv, and any folder you
add) self-instrument: each one emits its own OTel traces/metrics/logs and pushes
per-step execution events to the radar, which converts them into Temporal
activities. This gateway hosts that radar and proxies user prompts to the agents
— so workflows, tool calls, activities and OTel are all driven by the agents
themselves and stay dynamic as the fleet grows.

Run:
    uvicorn awcp.gateway.app:app --host 0.0.0.0 --port 8000

Requires (for /user/ask end to end): a Temporal server, the telemetry stack
(OTel collector + Grafana/Tempo/Prometheus/Loki), and a running Ollama. The
radar's onboarding/execution Temporal workers are started in-process by this
app's lifespan; the agents are started on demand by /user/ask.
"""

from __future__ import annotations

import os


# Load IAM / dev secrets from observability/.env into the process env (NON-overriding
# — anything already exported wins, missing file is a no-op). This is what lets the
# gateway reach the Keycloak-admin / SMTP / approver config for the credential
# approval flow (and OPENFGA_STORE_ID etc.) without any of it being hardcoded.
def _load_dotenv() -> None:
    try:
        import pathlib
        p = pathlib.Path(__file__).resolve().parents[3] / "observability" / ".env"
        if not p.is_file():
            return
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k and k not in os.environ:
                os.environ[k] = v.strip()
    except Exception:
        pass


_load_dotenv()

# Initialise OTel FIRST — before importing the radar app, which calls
# setup_otel() at import time. The first provider registered wins, so doing it
# here makes the whole process report under one service name: "awcp-gateway".
from awcp.observability.setup import setup_otel

setup_otel("awcp-gateway")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from awcp.observability.middleware import instrument_fastapi, instrument_requests

# Reuse the radar's routes verbatim as the control-plane Registry surface. The
# radar now exposes an APIRouter (not a standalone FastAPI app), included below.
from awcp.radar.api import router as radar_router, lifespan as radar_lifespan
from awcp.gateway.user import router as user_router
from awcp.gateway.opa_proxy import router as opa_proxy_router
from awcp.gateway.auth import AWCPAuthMiddleware, auth_startup_banner
from awcp.gateway.signup import router as signup_router
from awcp.gateway import chat_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Durable per-chat conversation store (fail-open: a no-op if no DB is
    # configured). Backs POST /user/chat/turn + GET /user/chat/history — the
    # per-chat context memory and the task console's context-window meter.
    import asyncio as _asyncio
    await _asyncio.to_thread(chat_store.init)
    # Drive the radar's lifespan here so its background scanner and Temporal
    # onboarding/execution workers start — those are what the bundle agents report
    # their per-step execution events into. The radar lifespan ignores the app it
    # is handed, so passing the gateway app is fine.
    import logging
    logging.getLogger("awcp.gateway").info(auth_startup_banner())
    async with radar_lifespan(app):
        yield


app = FastAPI(title="AWCP Gateway", lifespan=lifespan)

# IAM enforcement (Keycloak + OpenFGA). Added BEFORE CORS so CORS ends up the
# OUTERMOST middleware and its headers apply even to auth's 401/403 responses.
# Self-disabling: it's a pass-through unless AWCP_AUTH_MODE=shadow|enforce, so this
# line changes nothing until IAM is switched on. See awcp/gateway/auth.py.
app.add_middleware(AWCPAuthMiddleware)

# Allow the React dashboard (served from a different origin during dev) to call
# the gateway. This is independent of the UI folder — deleting the UI has no
# effect here. Override the allowed origins with AWCP_CORS_ORIGINS (comma list).
_cors = os.getenv("AWCP_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors.strip() == "*" else [o.strip() for o in _cors.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

instrument_fastapi(app)        # every gateway HTTP route is auto-traced
instrument_requests()          # outbound HTTP calls are auto-traced

# ── USER routes ───────────────────────────────────────────────────────────────
app.include_router(user_router)

# ── Tool Risk Tiers proxy → the hidden SLM OPA agent (per-call tiers + decisions) ─
app.include_router(opa_proxy_router)

# ── AWCP control-plane routes (mounted at ROOT for a single-port surface) ────
#   The radar router carries the REGISTRY endpoints (/agents, /tasks/execution/*,
#   /events, /healthz) plus the registry web UI at "/", and it has the token
#   monitor (awcp.laminar) included under /laminar/* (+ /laminar/ui). Mounting at
#   the root — rather than under a prefix — keeps the bundled web UIs' absolute
#   links (/laminar/ui, /laminar/usage, /agents …) working, so EVERYTHING is
#   reachable on this one port:  /user/*  ·  /agents+/tasks+/events  ·  /laminar/*
app.include_router(radar_router)

# ── IAM self-service signup ("Create account" on the login page) ────────────────
app.include_router(signup_router)


@app.get("/api", tags=["gateway"])
def api_map() -> dict:
    """Machine-readable map of the gateway's three route groups. The registry
    dashboard itself is the home page (GET /); the token monitor is /laminar/ui."""
    return {
        "service": "awcp-gateway",
        "note": "single port — everything below is on this same host:port",
        "user": {
            "list_agents": "GET /user/agents",
            "ask": 'POST /user/ask   body: {"agent": "<id from /user/agents>", "input": "<prompt>"}',
        },
        "agents": {
            "dashboard": "GET /            (registry web UI)",
            "list": "GET /agents",
            "register": "POST /agents/register",
            "gate": "POST /agents/{agent_id}/gate",
            "exec_start": "POST /tasks/execution/start",
            "events": "GET /events",
            "health": "GET /healthz",
        },
        "laminar": {
            "dashboard": "GET /laminar/ui  (token monitor)",
            "status": "GET /laminar/status",
            "usage": "GET /laminar/usage",
            "set_budget": "POST /laminar/budgets/{agent_id}",
        },
    }