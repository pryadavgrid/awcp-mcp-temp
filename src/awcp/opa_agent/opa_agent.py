"""OPA Agent — a background tool-call policy decision point (PDP) for AWCP.

Unlike the five worker agents, this one answers NO user prompts. It is a hidden
governance service: the control plane (radar) calls it once per tool call the
worker agents make, and it answers three questions:

  1. what RISK TIER does this tool carry?        (low | medium | high | severe)
  2. should this tool call be ALLOWED or BLOCKED? (block iff tier ∈ block set)
  3. it records the call into a per-question JSON  ({tool_name, risk_tier, decision})
     so the Radar can render a tier bar for EVERY tool call the agents make.

The risk TIER of each tool is not set by an operator: a **small language model** (a
local Ollama SLM, e.g. gemma2:2b) REASONS about each tool call and emits its tier.
What the operator DOES control is a single BLOCK THRESHOLD (a slider on the Radar):
any tool call whose SLM tier is at or above that threshold blocks the answer. The
threshold is runtime-mutable (POST /threshold) and persisted across restarts. The tier per tool is cached (a tool's inherent risk
doesn't change call-to-call), persisted across restarts, and surfaced to the Radar
UI. The block decision itself is still delegated to OPA (Open Policy Agent / Rego)
when AWCP_OPA_URL is set, with a fail-secure deterministic fallback — the same
pattern the radar's write-action gate uses (src/awcp/radar/opa.py).

This service is deliberately invisible to the control plane: it does NOT
self-register with the radar, and run.sh adds it to AGENT_RADAR_EXCLUDE so the
process scanner skips it. It never shows up as a governed agent.

Everything is env-driven — NOTHING (ports, tiers, model, block set, URLs) is
hardcoded:

  OPA_PORT                 8105                     this service's port
  OPA_RISK_TIERS           low,medium,high,severe   the tier vocabulary (ascending)
  OPA_BLOCK_THRESHOLD      (from OPA_BLOCK_TIERS)    operator slider: block at/above this tier
  OPA_BLOCK_TIERS          high,severe              legacy seed for the initial threshold
  OPA_THRESHOLD_PATH       <tmp>/awcp-opa-block-threshold.json  where the slider persists
  OPA_DEFAULT_TIER         low                      tier used when the SLM can't decide
  OPA_SLM_ENABLED          true                     reason the tier with the SLM
  OPA_SLM_BASE             <ollama runtime>         Ollama-compatible base URL
  OPA_SLM_MODEL            gemma2:2b                the small model that reasons the tier
  OPA_SLM_TIMEOUT          30                       per-classification timeout (s)
  OPA_SLM_TEMPERATURE      0                        deterministic classification
  OPA_SLM_CACHE            true                     cache the tier per tool (persisted)
  OPA_TOOL_POLICY_PATH     <tmp>/awcp-opa-tool-tiers.json   where the tier cache persists
  OPA_RECENT_MAX           200                      tool-call ring shown on the Radar
  AWCP_OPA_URL             ""                       OPA server base (empty ⇒ Python fallback)
  AWCP_OPA_TOOLS_PACKAGE   awcp/tools               Rego package under /v1/data
  AWCP_OPA_TIMEOUT         2                        per-OPA-request timeout (s)
  AWCP_GATEWAY_URL         http://localhost:8000    where to POST Laminar tool-token records
  OPA_LAMINAR_ENABLED      false                    log every evaluated tool call to Laminar
"""

from __future__ import annotations

import json
import os
import threading
import time
import tempfile
from collections import deque

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from slm import SLM, SLMResult          # local modules (run as a script from this dir)
from radar_register import RadarPresence

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── config (all env-driven) ───────────────────────────────────────────────────
PORT = int(os.getenv("OPA_PORT", "8105"))


def _csv(name: str, default: str) -> list[str]:
    return [t.strip().lower() for t in os.getenv(name, default).split(",") if t.strip()]


RISK_TIERS = _csv("OPA_RISK_TIERS", "low,medium,high,severe")
DEFAULT_TIER = (os.getenv("OPA_DEFAULT_TIER", "low").strip().lower() or "low")
if DEFAULT_TIER not in RISK_TIERS:
    DEFAULT_TIER = RISK_TIERS[0]

# The block decision is driven by a SINGLE operator-set THRESHOLD (the Radar slider):
# any tool call whose tier is at or ABOVE this tier blocks the question (which surfaces
# as a blocked answer in the user UI). The threshold is mutable at runtime via
# POST /threshold and persisted so the slider position survives a restart.
THRESHOLD_PATH = os.getenv(
    "OPA_THRESHOLD_PATH",
    os.path.join(tempfile.gettempdir(), "awcp-opa-block-threshold.json"),
)


def _block_tiers_at_or_above(threshold: str) -> list[str]:
    """Expand one threshold tier into the set of tiers that BLOCK (it + everything
    more severe), e.g. threshold 'high' ⇒ ['high', 'severe']."""
    try:
        return RISK_TIERS[RISK_TIERS.index(threshold):]
    except ValueError:
        return list(RISK_TIERS)


def _seed_threshold() -> str:
    """Initial threshold: a persisted slider value wins (survives restarts), else the
    OPA_BLOCK_THRESHOLD env, else the least-severe tier named in the legacy
    OPA_BLOCK_TIERS env (default keeps the old high/severe behaviour)."""
    try:
        with open(THRESHOLD_PATH, encoding="utf-8") as fh:
            t = str(json.load(fh).get("threshold", "")).strip().lower()
        if t in RISK_TIERS:
            return t
    except Exception:                       # noqa: BLE001 — no/invalid file ⇒ fall through
        pass
    env = os.getenv("OPA_BLOCK_THRESHOLD", "").strip().lower()
    if env in RISK_TIERS:
        return env
    legacy = [t for t in _csv("OPA_BLOCK_TIERS", "high,severe") if t in RISK_TIERS]
    return min(legacy, key=RISK_TIERS.index) if legacy else RISK_TIERS[-1]


BLOCK_THRESHOLD = _seed_threshold()
BLOCK_TIERS = _block_tiers_at_or_above(BLOCK_THRESHOLD)

CACHE_ENABLED = os.getenv("OPA_SLM_CACHE", "true").strip().lower() == "true"
CACHE_PATH = os.getenv(
    "OPA_TOOL_POLICY_PATH",
    os.path.join(tempfile.gettempdir(), "awcp-opa-tool-tiers.json"),
)
RECENT_MAX = int(os.getenv("OPA_RECENT_MAX", "200"))

OPA_URL = os.getenv("AWCP_OPA_URL", "").strip().rstrip("/")
OPA_PACKAGE = os.getenv("AWCP_OPA_TOOLS_PACKAGE", "awcp/tools").strip().strip("/")
OPA_TIMEOUT = float(os.getenv("AWCP_OPA_TIMEOUT", "2"))

GATEWAY_URL = os.getenv("AWCP_GATEWAY_URL", "http://localhost:8000").rstrip("/")
# Token metering for tool calls is done at the MCP server (the one place tools
# actually run, so it has the REAL input+output to count). This nominal logging
# from the decision path would only DOUBLE-count, so it is OFF by default.
LAMINAR_ENABLED = os.getenv("OPA_LAMINAR_ENABLED", "false").strip().lower() == "true"

# The small language model that REASONS each tool's risk tier (env-driven).
_SLM = SLM(tiers=RISK_TIERS, default_tier=DEFAULT_TIER)

# Makes the OPA agent VISIBLE on the radar (self-register + heartbeat). Hidden infra
# by nature, but operators want to see it running like the other agents. Env-gated
# (OPA_RADAR_REGISTER=false ⇒ stays hidden). Started on app startup below.
_PRESENCE = RadarPresence(port=PORT, framework="opa",
                          tier_model=_SLM.info().get("model", ""),
                          capabilities=["tool-call-pdp", "slm-risk-tiering"])

_lock = threading.Lock()            # guards the tier cache + decision stores
_slm_lock = threading.Lock()        # serialises SLM classification (dedupes a cold tool)


def _seed_cache() -> dict[str, dict]:
    """Re-load the SLM-decided tier cache from disk (so a restart doesn't re-pay the
    SLM for tools it already classified). Invalid entries are dropped."""
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            out: dict[str, dict] = {}
            for tool, rec in saved.items():
                if isinstance(rec, dict) and str(rec.get("tier", "")).lower() in RISK_TIERS:
                    out[str(tool)] = {"tier": str(rec["tier"]).lower(),
                                      "reason": str(rec.get("reason", "")),
                                      "engine": str(rec.get("engine", "slm")),
                                      "model": str(rec.get("model", "")),
                                      "ts": float(rec.get("ts", 0) or 0)}
            return out
    except Exception:                       # noqa: BLE001 — no/invalid file ⇒ start empty
        pass
    return {}


# tool_name -> {tier, reason, engine, model, ts}  (the SLM's per-tool decision)
_TIERS: dict[str, dict] = _seed_cache()
# task_id -> {task_id, question, tools:[{...}], blocked}
_DECISIONS: dict[str, dict] = {}
# flat ring of recent tool-call decisions across ALL agents (newest appended right)
_RECENT: deque = deque(maxlen=RECENT_MAX)


def _persist() -> None:
    if not CACHE_ENABLED:
        return
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(_TIERS, fh)
    except Exception:                       # noqa: BLE001 — persistence is best-effort
        pass


def _persist_threshold() -> None:
    """Best-effort persist of the operator's block threshold so the Radar slider
    survives a restart. Always on — unlike the SLM tier cache, this is operator
    intent, not a recomputable optimisation."""
    try:
        with open(THRESHOLD_PATH, "w", encoding="utf-8") as fh:
            json.dump({"threshold": BLOCK_THRESHOLD}, fh)
    except Exception:                       # noqa: BLE001 — persistence is best-effort
        pass


def tier_for(tool_name: str, tool_input: dict | None = None, question: str = "") -> dict:
    """Resolve a tool's tier record {tier, reason, engine, model, ts}. Cache hit ⇒
    instant; miss ⇒ ask the SLM to reason it (serialised, with a re-check inside the
    lock so a burst of the same cold tool only classifies once)."""
    if CACHE_ENABLED:
        with _lock:
            hit = _TIERS.get(tool_name)
        if hit:
            return hit
    with _slm_lock:
        if CACHE_ENABLED:
            with _lock:
                hit = _TIERS.get(tool_name)
            if hit:
                return hit
        res: SLMResult = _SLM.classify(tool_name, tool_input, question)
        rec = {"tier": res.tier, "reason": res.reason, "engine": res.engine,
               "model": res.model, "ts": time.time()}
        with _lock:
            _TIERS[tool_name] = rec
            _persist()
        return rec


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
    """Optionally log this tool call (+ estimated tokens) to Laminar via the control
    plane. OFF by default — the MCP server already meters tool tokens at the source,
    so logging here would only double-count. Best-effort either way."""
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
app = FastAPI(title="AWCP OPA Agent (SLM tool-call PDP)")


@app.on_event("startup")
def _on_startup() -> None:
    # Self-register with the radar + heartbeat so the OPA agent shows up as a
    # running agent on the control plane (no-op when OPA_RADAR_REGISTER=false).
    _PRESENCE.start()


class EvaluateRequest(BaseModel):
    agent_id: str = ""
    task_id: str = ""
    tool_name: str
    tool_input: dict | None = None
    question: str = ""


class ThresholdRequest(BaseModel):
    threshold: str


@app.get("/")
def about() -> FileResponse:
    return FileResponse(os.path.join(_HERE, "about.html"))


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "opa-agent", "tiers": RISK_TIERS,
            "block_tiers": BLOCK_TIERS, "block_threshold": BLOCK_THRESHOLD,
            "default_tier": DEFAULT_TIER, "opa": bool(OPA_URL),
            "slm": _SLM.info(), "tools_classified": len(_TIERS)}


@app.post("/threshold")
def set_threshold(req: ThresholdRequest) -> dict:
    """Operator sets the SINGLE block threshold via the Radar slider: any tool call
    whose tier is at or above this tier is blocked (which blocks the question in the
    user UI). Recomputes the block set, persists it, and returns the new state."""
    global BLOCK_THRESHOLD, BLOCK_TIERS
    want = (req.threshold or "").strip().lower()
    if want not in RISK_TIERS:
        raise HTTPException(status_code=400,
                            detail=f"threshold must be one of {RISK_TIERS}")
    with _lock:
        BLOCK_THRESHOLD = want
        BLOCK_TIERS = _block_tiers_at_or_above(want)
        _persist_threshold()
    return {"block_threshold": BLOCK_THRESHOLD, "block_tiers": BLOCK_TIERS,
            "tiers": RISK_TIERS}


@app.get("/tools")
def tools() -> dict:
    """The tier vocabulary + block set + the SLM-decided per-tool tier map (read-only
    now — the SLM owns the tier, no operator override)."""
    with _lock:
        by_tool = {t: r["tier"] for t, r in _TIERS.items()}
    return {"tiers": RISK_TIERS, "block_tiers": BLOCK_TIERS,
            "block_threshold": BLOCK_THRESHOLD, "default_tier": DEFAULT_TIER,
            "policy": by_tool, "read_only": True}


@app.get("/tiers")
def tiers() -> dict:
    """Everything the Radar's tier bars need: the tier vocabulary + block set, the
    SLM's per-tool classification, and the recent tool-call decisions (newest first)
    across ALL agents so each call can render its low/medium/high/severe bar."""
    with _lock:
        by_tool = {t: {"tier": r["tier"], "reason": r.get("reason", ""),
                       "engine": r.get("engine", ""), "model": r.get("model", "")}
                   for t, r in _TIERS.items()}
        recent = list(_RECENT)
    recent.reverse()                         # newest first for the UI
    return {"enabled": True, "tiers": RISK_TIERS, "block_tiers": BLOCK_TIERS,
            "block_threshold": BLOCK_THRESHOLD, "default_tier": DEFAULT_TIER,
            "slm": _SLM.info(), "by_tool": by_tool, "recent": recent}


@app.post("/evaluate")
def evaluate(req: EvaluateRequest) -> dict:
    """Decide one tool call: reason its tier (SLM, cached), decide allow/block, append
    it to the question's structured JSON + the recent ring, and (optionally) log it to
    Laminar. The radar enforces a 'block' by finishing the task blocked, which surfaces
    in the user UI."""
    rec = tier_for(req.tool_name, req.tool_input, req.question)
    tier = rec["tier"]
    decision, reason = _decide(req.tool_name, tier)
    record = {"tool_name": req.tool_name, "risk_tier": tier, "decision": decision,
              "reason": reason, "reasoning": rec.get("reason", ""),
              "engine": rec.get("engine", ""), "agent_id": req.agent_id,
              "task_id": req.task_id, "question": req.question, "ts": time.time()}
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
        _RECENT.append(record)
    _log_laminar(req.agent_id, req.task_id, req.tool_name, req.tool_input)
    return {"tool_name": req.tool_name, "risk_tier": tier, "decision": decision,
            "reason": reason, "reasoning": rec.get("reason", ""),
            "engine": rec.get("engine", ""), "block_tiers": BLOCK_TIERS}


@app.get("/decisions/{task_id}")
def decisions(task_id: str) -> dict:
    """The structured JSON of every tool call (+ tier + decision) for one question."""
    with _lock:
        return _DECISIONS.get(task_id, {"task_id": task_id, "tools": [], "blocked": False})


if __name__ == "__main__":
    import uvicorn
    print(f"\U0001F6E1️  AWCP OPA Agent (SLM tool-call PDP, hidden) → http://localhost:{PORT}"
          f"   tiers={RISK_TIERS} block={BLOCK_TIERS} slm={_SLM.info()} "
          f"opa={'on' if OPA_URL else 'off(fallback)'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
