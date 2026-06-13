"""AWCP Gateway — one FastAPI app exposing two clearly separated route groups.

  USER routes (/user/*)
      GET  /user/agents   list every agent in the external bundle (dynamic)
      POST /user/ask       run a chosen agent on a prompt and return the result

  AWCP control-plane routes (/awcp/*)
      1. Registry (radar)  -> mounted at /awcp/registry  (reuses awcp/radar/api.py
         verbatim: discovery, onboarding, the write-action gate, the agent-task
         execution workflows the bundle agents report into, and the web UI)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Drive the radar's lifespan here so its background scanner and Temporal
    # onboarding/execution workers start — those are what the bundle agents report
    # their per-step execution events into. The radar lifespan ignores the app it
    # is handed, so passing the gateway app is fine.
    async with radar_lifespan(app):
        yield


app = FastAPI(title="AWCP Gateway", lifespan=lifespan)

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

# ── AWCP control-plane routes ────────────────────────────────────────────────
#   1. Registry (radar): the full radar API + its web UI, included as a router.
#      Endpoints become /awcp/registry/agents, /awcp/registry/agents/register,
#      /awcp/registry/tasks/execution/*, /awcp/registry/healthz, /awcp/registry/.
app.include_router(radar_router, prefix="/awcp/registry", tags=["AWCP Registry"])


@app.get("/", tags=["gateway"])
def index() -> dict:
    """A map of the gateway's two route groups."""
    return {
        "service": "awcp-gateway",
        "user_routes": {
            "list_agents": "GET /user/agents",
            "ask": 'POST /user/ask   body: {"agent": "<id from /user/agents>", "input": "<prompt>"}',
        },
        "awcp_control_plane": {
            "registry_radar": {
                "base": "/awcp/registry",
                "ui": "/awcp/registry/",
                "list_agents": "GET /awcp/registry/agents",
                "register": "POST /awcp/registry/agents/register",
                "gate": "POST /awcp/registry/agents/{agent_id}/gate",
                "exec_start": "POST /awcp/registry/tasks/execution/start",
                "events": "GET /awcp/registry/events",
                "health": "GET /awcp/registry/healthz",
            }
        },
    }