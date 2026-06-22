"""Gateway proxy to the hidden OPA agent.

The OPA agent (awcp-agents/opa_agent) is deliberately invisible and on its own
port, but the control-plane UI only talks to the gateway (single port). These thin
routes forward the per-tool risk-tier policy (the Agent Hooks → Tool Risk Policy
sliders) and the per-question tool-risk JSON to/from the OPA agent, so the operator
edits the policy without the OPA agent ever being exposed directly.

Env-driven (nothing hardcoded): AWCP_OPA_AGENT_URL selects the OPA agent; unset ⇒
the routes report it's disabled (the UI degrades gracefully). AWCP_OPA_AGENT_TIMEOUT
bounds each hop.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["tool-policy"])

OPA_AGENT_URL = os.getenv("AWCP_OPA_AGENT_URL", "").strip().rstrip("/")
OPA_AGENT_TIMEOUT = float(os.getenv("AWCP_OPA_AGENT_TIMEOUT", "3"))


class ToolRiskRequest(BaseModel):
    tier: str                       # low | medium | high | severe ('' clears to default)


def _disabled() -> dict:
    """Shape returned when no OPA agent is configured — the UI shows an empty,
    inert Tool Risk Policy panel instead of an error."""
    return {"enabled": False, "tiers": [], "block_tiers": [],
            "default_tier": "", "policy": {}}


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


@router.get("/tools/policy")
async def get_tool_policy() -> dict:
    """Tier vocabulary + block set + per-tool tier map (the slider feed). Returns an
    inert disabled shape when no OPA agent is wired, so the UI never errors."""
    if not OPA_AGENT_URL:
        return _disabled()
    out = await _opa("GET", "/tools")
    out["enabled"] = True
    return out


@router.post("/tools/policy/{name}")
async def set_tool_policy(name: str, req: ToolRiskRequest) -> dict:
    """Operator sets one tool's risk tier (the slider)."""
    return await _opa("POST", f"/tools/{name}/risk", {"tier": req.tier})


@router.get("/opa/decisions/{task_id}")
async def get_decisions(task_id: str) -> dict:
    """The structured JSON of every tool call (+ tier + decision) for one question."""
    return await _opa("GET", f"/decisions/{task_id}")
