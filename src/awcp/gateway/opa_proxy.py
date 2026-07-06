"""Gateway proxy to the hidden OPA agent.

The OPA agent (src/awcp/opa_agent) is deliberately invisible and on its own port,
but the control-plane UI only talks to the gateway (single port). These thin routes
forward the SLM-reasoned tool-risk tiers and the per-question tool-risk JSON from the
OPA agent, so the Radar can draw a tier bar for every tool call without the OPA agent
ever being exposed directly.

Env-driven (nothing hardcoded): AWCP_OPA_AGENT_URL selects the OPA agent; unset ⇒ the
routes report it's disabled (the UI degrades gracefully). AWCP_OPA_AGENT_TIMEOUT bounds
each hop.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["tool-policy"])

OPA_AGENT_URL = os.getenv("AWCP_OPA_AGENT_URL", "").strip().rstrip("/")
OPA_AGENT_TIMEOUT = float(os.getenv("AWCP_OPA_AGENT_TIMEOUT", "30"))


def _disabled() -> dict:
    """Shape returned when no OPA agent is configured — the Radar shows an empty,
    inert tier panel instead of an error."""
    return {"enabled": False, "tiers": [], "block_tiers": [], "block_threshold": "",
            "default_tier": "", "slm": {"enabled": False, "model": "", "base": ""},
            "by_tool": {}, "recent": []}


async def _opa(method: str, path: str, json: dict | None = None) -> dict:
    if not OPA_AGENT_URL:
        raise HTTPException(status_code=503, detail="OPA agent not configured (AWCP_OPA_AGENT_URL unset)")
    try:
        async with httpx.AsyncClient(timeout=OPA_AGENT_TIMEOUT) as c:
            r = await c.request(method, f"{OPA_AGENT_URL}{path}", json=json)
            r.raise_for_status()
            return r.json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"OPA agent unreachable: {type(exc).__name__}")


@router.get("/opa/tiers")
async def get_tiers() -> dict:
    """The SLM-reasoned tier vocabulary + block set + per-tool tiers + recent tool-call
    decisions (newest first) — the Radar's tier-bar feed. Returns an inert disabled
    shape when no OPA agent is wired, so the Radar never errors."""
    if not OPA_AGENT_URL:
        return _disabled()
    out = await _opa("GET", "/tiers")
    out["enabled"] = True
    return out


class ThresholdBody(BaseModel):
    threshold: str


@router.post("/opa/threshold")
async def set_threshold(body: ThresholdBody) -> dict:
    """Operator-set block threshold (the Radar slider) → forwarded to the OPA agent.
    Any tool call whose SLM tier is at or above this tier blocks the question in the
    user UI. Returns the new block set; 503 when no OPA agent is wired."""
    return await _opa("POST", "/threshold", {"threshold": body.threshold})


class EvaluateBody(BaseModel):
    tool_name: str
    tool_input: dict | None = None
    question: str = ""
    agent_id: str = ""
    task_id: str = ""


@router.post("/opa/evaluate")
async def evaluate_tool(body: EvaluateBody) -> dict:
    """Pre-execution tool check for a governed agent: forward one tool call to the
    OPA agent, which reasons its risk tier and returns whether it crosses the
    operator's block threshold. Body: {tool_name, agent_id, task_id}. Response:
    {risk_tier, decision ("allow"|"block"), engine}. Agents call this BEFORE running a
    tool so a tier at/above the threshold can pause for operator approval on the AWCP
    UI. Returns an inert allow when no OPA agent is wired (so agents never hard-break)."""
    if not OPA_AGENT_URL:
        return {"tool_name": body.tool_name, "risk_tier": "", "decision": "allow",
                "engine": "disabled", "enabled": False}
    return await _opa("POST", "/evaluate", body.model_dump())


@router.get("/opa/decisions/{task_id}")
async def get_decisions(task_id: str) -> dict:
    """The structured JSON of every tool call (+ tier + decision) for one question."""
    return await _opa("GET", f"/decisions/{task_id}")
