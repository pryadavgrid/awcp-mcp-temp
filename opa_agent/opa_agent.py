"""OPA Agent — a background tool-call policy decision point (PDP) for AWCP.

Unlike the five worker agents, this one runs NO LLM and answers NO user prompts.
It is a hidden governance service: the control plane (radar) calls it once per
tool call the worker agents make, and it answers three questions deterministically:

  1. what RISK TIER does this tool carry?        (low | medium | high | severe)
  2. should this tool call be ALLOWED or BLOCKED? (block iff tier ∈ block set)
  3. it records the call into a per-question JSON  ({tool_name, risk_tier, decision})
     and logs the call + its tokens to Laminar.

The risk tier per tool is set by the operator on the AWCP control-plane UI (a
slider) and stored here (the single cross-process authority), seeded from policy
defaults. The decision itself is delegated to OPA (Open Policy Agent / Rego) when
AWCP_OPA_URL is set, with a fail-secure deterministic fallback — the same pattern
the radar's write-action gate uses (src/awcp/radar/opa.py).

This service is deliberately invisible to the control plane: it does NOT
self-register with the radar, and run.sh adds it to AGENT_RADAR_EXCLUDE so the
process scanner skips it. It never shows up as a governed agent.

Everything is env-driven — NOTHING (ports, tiers, tools, block set, URLs) is
hardcoded:

  OPA_PORT                 8105                     this service's port
  OPA_RISK_TIERS           low,medium,high,severe   the tier vocabulary (ascending)
  OPA_BLOCK_TIERS          high,severe              tiers that BLOCK the answer
  OPA_DEFAULT_TIER         low                      tier for a tool with none set
  OPA_TOOL_TIERS           ""                       seed map, e.g. "web_search:high,read_file:low"
  OPA_TOOL_POLICY_PATH     <tmp>/awcp-opa-tool-tiers.json   where the slider state persists
  AWCP_OPA_URL             ""                       OPA server base (empty ⇒ Python fallback)
  AWCP_OPA_TOOLS_PACKAGE   awcp/tools               Rego package under /v1/data
  AWCP_OPA_TIMEOUT         2                        per-OPA-request timeout (s)
  AWCP_GATEWAY_URL         http://localhost:8000    where to POST Laminar tool-token records
  OPA_LAMINAR_ENABLED      true                     log every evaluated tool call to Laminar
"""

from __future__ import annotations

import json
import os
import threading
import time
import tempfile

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── config (all env-driven) ───────────────────────────────────────────────────
PORT = int(os.getenv("OPA_PORT", "8105"))


def _csv(name: str, default: str) -> list[str]:
    return [t.strip().lower() for t in os.getenv(name, default).split(",") if t.strip()]


RISK_TIERS = _csv("OPA_RISK_TIERS", "low,medium,high,severe")
BLOCK_TIERS = [t for t in _csv("OPA_BLOCK_TIERS", "high,severe") if t in RISK_TIERS]
DEFAULT_TIER = (os.getenv("OPA_DEFAULT_TIER", "low").strip().lower() or "low")
if DEFAULT_TIER not in RISK_TIERS:
    DEFAULT_TIER = RISK_TIERS[0]

POLICY_PATH = os.getenv(
    "OPA_TOOL_POLICY_PATH",
    os.path.join(tempfile.gettempdir(), "awcp-opa-tool-tiers.json"),
)

OPA_URL = os.getenv("AWCP_OPA_URL", "").strip().rstrip("/")
OPA_PACKAGE = os.getenv("AWCP_OPA_TOOLS_PACKAGE", "awcp/tools").strip().strip("/")
OPA_TIMEOUT = float(os.getenv("AWCP_OPA_TIMEOUT", "2"))

GATEWAY_URL = os.getenv("AWCP_GATEWAY_URL", "http://localhost:8000").rstrip("/")
# Token metering for tool calls is done at the MCP server (the one place tools
# actually run, so it has the REAL input+output to count). This nominal logging
# from the decision path would only DOUBLE-count, so it is OFF by default; flip
# OPA_LAMINAR_ENABLED=true (and AWCP_METER_TOOL_TOKENS=false on the MCP) to instead
# meter from here.
LAMINAR_ENABLED = os.getenv("OPA_LAMINAR_ENABLED", "false").strip().lower() == "true"

_lock = threading.Lock()


def _seed_tiers() -> dict[str, str]:
    """Initial per-tool tier map: the persisted slider state if present, else the
    OPA_TOOL_TIERS seed env. Tools not listed resolve to DEFAULT_TIER at lookup."""
    # 1) persisted operator edits win (survive restarts)
    try:
        with open(POLICY_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            return {str(k): str(v).lower() for k, v in saved.items()
                    if str(v).lower() in RISK_TIERS}
    except Exception:                       # noqa: BLE001 — no/invalid file ⇒ seed from env
        pass
    # 2) seed map from env
    out: dict[str, str] = {}
    for pair in os.getenv("OPA_TOOL_TIERS", "").split(","):
        name, _, val = pair.partition(":")
        if name.strip() and val.strip().lower() in RISK_TIERS:
            out[name.strip()] = val.strip().lower()
    return out


_TIERS: dict[str, str] = _seed_tiers()
# per-task structured JSON: task_id -> {tools:[{tool_name,risk_tier,decision,ts}], blocked}
_DECISIONS: dict[str, dict] = {}


def _persist() -> None:
    try:
        with open(POLICY_PATH, "w", encoding="utf-8") as fh:
            json.dump(_TIERS, fh)
    except Exception:                       # noqa: BLE001 — persistence is best-effort
        pass


def tier_for(tool_name: str) -> str:
    with _lock:
        return _TIERS.get(tool_name, DEFAULT_TIER)


def _decide(tool_name: str, tier: str) -> tuple[str, str]:
    """Return (decision, reason). Consults OPA Rego when AWCP_OPA_URL is set;
    fail-secure fallback to the deterministic rule (block iff tier ∈ block set)."""
    fallback_block = tier in BLOCK_TIERS

    def _result(block: bool, engine: str) -> tuple[str, str]:
        if block:
            return "block", (f"tool '{tool_name}' is {tier.upper()} risk — blocked by "
                             f"OPA tool policy ({engine})")
        return "allow", f"tool '{tool_name}' is {tier} risk — allowed ({engine})"

    if not OPA_URL:
        return _result(fallback_block, "policy")
    try:
        url = f"{OPA_URL}/v1/data/{OPA_PACKAGE}/result"
        r = httpx.post(url, json={"input": {
            "tool": tool_name, "risk_tier": tier, "block_tiers": BLOCK_TIERS,
        }}, timeout=OPA_TIMEOUT)
        r.raise_for_status()
        res = r.json().get("result")
        if isinstance(res, dict) and "block" in res:
            return _result(bool(res["block"]), "opa")
    except Exception:                       # noqa: BLE001 — OPA must never break governance
        pass
    return _result(fallback_block, "policy(opa_fallback)")


def _log_laminar(agent_id: str, task_id: str, tool_name: str, tool_input) -> None:
    """Log this tool call (and its estimated tokens) to Laminar via the control
    plane, so every governed tool call shows in the Token Monitor / Laminar under
    the calling agent. Best-effort: a telemetry hiccup never affects the decision."""
    if not (LAMINAR_ENABLED and agent_id):
        return
    try:
        text = json.dumps(tool_input or {}, ensure_ascii=False)
        httpx.post(f"{GATEWAY_URL}/laminar/record", json={
            "agent_id": agent_id,
            "task_id": task_id or "opa",
            "tool_name": tool_name,
            "model": f"tool:{tool_name}",
            "step": "tool_called",
            "text": text,              # the gateway estimates tokens from this
        }, timeout=OPA_TIMEOUT)
    except Exception:                       # noqa: BLE001
        pass


# ── HTTP surface ──────────────────────────────────────────────────────────────
app = FastAPI(title="AWCP OPA Agent (tool-call PDP)")


class EvaluateRequest(BaseModel):
    agent_id: str = ""
    task_id: str = ""
    tool_name: str
    tool_input: dict | None = None
    question: str = ""


class RiskRequest(BaseModel):
    tier: str


@app.get("/")
def about() -> FileResponse:
    return FileResponse(os.path.join(_HERE, "about.html"))


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "opa-agent", "tiers": RISK_TIERS,
            "block_tiers": BLOCK_TIERS, "default_tier": DEFAULT_TIER,
            "opa": bool(OPA_URL), "tools_set": len(_TIERS)}


@app.get("/tools")
def tools() -> dict:
    """The tier vocabulary + block set + the per-tool tier map (the slider feed).
    Tools the UI knows about but that aren't here resolve to DEFAULT_TIER."""
    with _lock:
        tiers = dict(_TIERS)
    return {"tiers": RISK_TIERS, "block_tiers": BLOCK_TIERS,
            "default_tier": DEFAULT_TIER, "policy": tiers}


@app.post("/tools/{name}/risk")
def set_tool_risk(name: str, req: RiskRequest) -> dict:
    """Operator sets one tool's tier (from the control-plane slider). tier='' (or
    the default tier) clears any override back to the default."""
    tier = (req.tier or "").strip().lower()
    if tier and tier not in RISK_TIERS:
        return {"ok": False, "error": f"unknown tier '{tier}'", "tiers": RISK_TIERS}
    with _lock:
        if not tier or tier == DEFAULT_TIER:
            _TIERS.pop(name, None)
        else:
            _TIERS[name] = tier
        _persist()
    return {"ok": True, "tool": name, "risk_tier": tier_for(name),
            "blocks": tier_for(name) in BLOCK_TIERS}


@app.post("/evaluate")
def evaluate(req: EvaluateRequest) -> dict:
    """Decide one tool call: resolve its tier, decide allow/block, append it to the
    question's structured JSON, and log it (+ tokens) to Laminar. The radar enforces
    a 'block' by finishing the task blocked, which surfaces in the user UI."""
    tier = tier_for(req.tool_name)
    decision, reason = _decide(req.tool_name, tier)
    record = {"tool_name": req.tool_name, "risk_tier": tier,
              "decision": decision, "ts": time.time()}
    with _lock:
        entry = _DECISIONS.setdefault(
            req.task_id or "unknown",
            {"task_id": req.task_id, "question": req.question, "tools": [], "blocked": False},
        )
        if req.question and not entry.get("question"):
            entry["question"] = req.question
        entry["tools"].append(record)
        if decision == "block":
            entry["blocked"] = True
    _log_laminar(req.agent_id, req.task_id, req.tool_name, req.tool_input)
    return {"tool_name": req.tool_name, "risk_tier": tier,
            "decision": decision, "reason": reason, "block_tiers": BLOCK_TIERS}


@app.get("/decisions/{task_id}")
def decisions(task_id: str) -> dict:
    """The structured JSON of every tool call (+ tier + decision) for one question."""
    with _lock:
        return _DECISIONS.get(task_id, {"task_id": task_id, "tools": [], "blocked": False})


if __name__ == "__main__":
    import uvicorn
    print(f"\U0001F6E1️  AWCP OPA Agent (tool-call PDP, hidden) → http://localhost:{PORT}"
          f"   tiers={RISK_TIERS} block={BLOCK_TIERS} opa={'on' if OPA_URL else 'off(fallback)'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
