"""HTTP surface for the token monitor — an APIRouter the radar mounts.

Mounted with one guarded line in radar/api.py (app.include_router(router)), so
removing this package only removes these routes — nothing else in the radar
depends on them.

Routes (all under /laminar):
  GET  /laminar/status               module status (exporter, window, defaults)
  GET  /laminar/usage                per-agent usage + budget evaluation (UI feed)
  GET  /laminar/usage/{agent_id}     one agent in detail + its recent LLM calls
  GET  /laminar/budgets              effective budgets (overrides + defaults)
  POST /laminar/budgets/{agent_id}   operator: set/clear a token budget override
  POST /laminar/reset/{agent_id}     operator: clear an agent's usage window
                                     (pairs with POST /agents/{id}/autonomy to
                                     restore a token-degraded agent)
  GET  /laminar/ui                   the token dashboard (static page)
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from awcp.laminar import bridge, budget, config
from awcp.laminar.ledger import LEDGER

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

router = APIRouter(prefix="/laminar", tags=["laminar"])


class BudgetRequest(BaseModel):
    tokens: int                 # tokens per window; 0 clears the override


@router.get("/status")
def status() -> dict:
    return bridge.status_summary()


@router.get("/usage")
def usage() -> list[dict]:
    return bridge.all_usage()


@router.get("/usage/{agent_id}")
def usage_one(agent_id: str) -> dict:
    if agent_id not in LEDGER.agents():
        raise HTTPException(status_code=404, detail="no usage recorded for this agent")
    out = bridge.usage_summary(agent_id)
    # attach a Tempo deep-link to each recent call (None when no template set)
    recent = LEDGER.recent(agent_id, limit=50)
    for r in recent:
        r["trace_url"] = config.trace_url(r.get("trace_id"))
    out["recent"] = recent
    return out


@router.get("/budgets")
def budgets() -> dict:
    return {
        "overrides": budget.overrides(),
        "risk_defaults": dict(bridge.config.RISK_TOKEN_BUDGET),
        "system_default": bridge.config.DEFAULT_TOKEN_BUDGET,
        "window_s": bridge.config.BUDGET_WINDOW_S,
    }


@router.post("/budgets/{agent_id}")
def set_budget(agent_id: str, req: BudgetRequest) -> dict:
    budget.set_budget(agent_id, req.tokens)
    return {"agent_id": agent_id,
            "budget_tokens": budget.budget_for(agent_id),
            "override": req.tokens > 0}


@router.post("/reset/{agent_id}")
def reset(agent_id: str) -> dict:
    return bridge.reset_agent(agent_id)


@router.get("/ui")
def ui() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
