"""User-facing AWCP routes — the human entry point to the agent workforce.

Fully dynamic over the external agent bundle (see agents_fs): agents are
discovered at request time, so onboarding a new one is just dropping a folder
with a run.sh into the bundle — no code changes here.

Endpoints
---------
  GET  /user/agents                      list bundle agents + live state
  POST /user/ask                          run one agent, BLOCK, return result
  POST /user/submit                       run one agent, return immediately (ids)
  GET  /user/status/{agent}/{task_id}     live task state + dynamic step timeline
  POST /user/approve/{agent}/{task_id}    approve/deny a paused high-risk write

How the live timeline works WITHOUT hardcoding any steps:
Each bundle agent self-instruments. As it runs a task it pushes one execution
event per step (llm_called / web_search / tool_called / synthesize) to the radar,
which turns each into its OWN Temporal activity. /user/status reads that
workflow's history back and returns the steps in order — whatever the agent
actually did, keyed by the runtime name it emitted. Add agents or tools and they
appear in the timeline automatically.
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from temporalio.client import Client

from awcp.gateway import agents_fs as fs

router = APIRouter(prefix="/user", tags=["user"])

# Temporal Web UI base — used only to build deep links to the task's workflow.
TEMPORAL_UI_BASE = os.getenv("AWCP_TEMPORAL_UI_BASE", "http://localhost:8233")
TEMPORAL_SERVER_URL = os.getenv("TEMPORAL_SERVER_URL", "localhost:7233")

# Tunables (env-driven — nothing about timing is baked in).
START_TIMEOUT = float(os.getenv("AWCP_AGENT_START_TIMEOUT", "90"))   # wait for boot
TASK_TIMEOUT = float(os.getenv("AWCP_ASK_TIMEOUT", "300"))           # blocking /ask wait
POLL_INTERVAL = float(os.getenv("AWCP_ASK_POLL_INTERVAL", "1.5"))

# A task is "settled" once it reaches one of these states.
TERMINAL = {"done", "blocked", "failed", "awaiting_approval"}

# Temporal activity name -> step kind, used only as a fallback when the agent's
# own event payload doesn't carry a `type` (setup/complete don't). The agent's
# emitted `type` always wins, so new event kinds need no change here.
_ACT_KIND = {
    "execution_setup": "setup",
    "execution_llm_call": "llm_called",
    "execution_web_search": "web_search",
    "execution_tool_call": "tool_called",
    "execution_synthesize_answer": "synthesize",
    "execution_complete": "complete",
}
_KIND_LABELS = {
    "setup": "Task setup",
    "llm_called": "LLM call",
    "web_search": "Web search",
    "tool_called": "Tool call",
    "synthesize": "Synthesize answer",
    "complete": "Complete",
}

# Cached, process-wide Temporal client (lazy; the event loop is shared).
_client: Client | None = None


class AskRequest(BaseModel):
    agent: str = Field(..., description="Agent id from GET /user/agents (the folder name)")
    input: str = Field(..., description="The prompt / goal to run through the agent")
    auto_start: bool = Field(True, description="Start the agent if it is not already running")


class ApproveBody(BaseModel):
    decision: str = Field("approve", description="approve | deny")


# ── small helpers ─────────────────────────────────────────────────────────────

async def _temporal_client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(TEMPORAL_SERVER_URL)
    return _client


def _temporal_url(workflow_id: str | None) -> str | None:
    if not workflow_id:
        return None
    return f"{TEMPORAL_UI_BASE}/namespaces/default/workflows/{workflow_id}"


def _label_for(kind: str, activity: str) -> str:
    if kind in _KIND_LABELS:
        return _KIND_LABELS[kind]
    base = (kind or activity or "step").replace("execution_", "").replace("_", " ").strip()
    return base[:1].upper() + base[1:] if base else "Step"


async def _fetch_info(port: int) -> dict:
    """The agent's own /info (framework, model, tools, agent_id, …)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{fs.base_url(port)}/info")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


async def _fetch_task(port: int, task_id: str) -> dict:
    """The agent's own task record (status, governed steps, result, tools_used)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{fs.base_url(port)}/tasks/{task_id}")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


async def _describe(agent: dict) -> dict:
    """One agent's catalog entry + live state for GET /user/agents."""
    state = await asyncio.to_thread(fs.running_state, agent)  # subprocess; off-loop
    port = state["port"]
    info = await _fetch_info(port) if port else {}
    return {
        "id": agent["id"],
        "running": state["running"],
        "pid": state["pids"][0] if state["pids"] else None,
        "port": port,
        "url": fs.base_url(port) if port else None,
        "name": info.get("agent"),
        "framework": info.get("framework"),
        "model": info.get("model"),
        "tools": info.get("tools", []),
        "examples": info.get("examples", []),
        "registered": info.get("registered"),
        "agent_id": info.get("agent_id"),
        # Whether this agent takes a file alongside the prompt (e.g. file-inspector).
        # The UI shows an upload box only for agents that advertise this.
        "accepts_files": bool(info.get("accepts_files", False)),
    }


async def _wait_until_up(agent: dict) -> dict:
    """Poll for the agent's port to open and /health to answer, up to START_TIMEOUT."""
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        state = await asyncio.to_thread(fs.running_state, agent)
        if state["port"]:
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    r = await c.get(f"{fs.base_url(state['port'])}/health")
                    if r.status_code == 200:
                        return state
            except Exception:
                pass
        await asyncio.sleep(2.0)
    return await asyncio.to_thread(fs.running_state, agent)


async def _ensure_up(agent_id: str, auto_start: bool) -> tuple[dict, dict]:
    """Resolve the agent and make sure it is running. Returns (agent, state)."""
    agent = fs.find(agent_id)
    if not agent:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"unknown agent '{agent_id}'",
                "available_agents": [a["id"] for a in fs.discover()],
            },
        )
    state = await asyncio.to_thread(fs.running_state, agent)
    if not state["running"] or not state["port"]:
        if not auto_start:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"agent '{agent_id}' is not running",
                    "hint": "send auto_start=true, or start it from the control plane",
                },
            )
        await asyncio.to_thread(fs.start, agent)
        state = await _wait_until_up(agent)
        if not state["port"]:
            raise HTTPException(
                status_code=504,
                detail={
                    "message": f"agent '{agent_id}' did not come up within {START_TIMEOUT:.0f}s",
                    "hint": "first launch builds a venv + installs deps; check the agent log under $TMPDIR and retry",
                },
            )
    return agent, state


async def _submit_task(base: str, prompt: str) -> dict:
    """POST the prompt as a governed task to the agent and return the task record."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        try:
            r = await c.post(f"{base}/tasks", json={"goal": prompt})
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail={"message": "could not reach the agent", "error": str(e)},
            )
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "agent rejected the task",
                    "status_code": r.status_code,
                    "body": r.text[:500],
                },
            )
        return r.json()


async def _extract_timeline(workflow_id: str | None) -> list[dict]:
    """Fold the AgentExecutionWorkflow history into an ordered step timeline.

    One entry per Temporal activity the agent actually triggered. Dynamic — the
    step `kind` comes from the agent's own emitted event type; unknown kinds pass
    straight through.
    """
    if not workflow_id:
        return []
    try:
        client = await _temporal_client()
        handle = client.get_workflow_handle(workflow_id)

        scheduled: dict[int, tuple[str, dict]] = {}
        states: dict[int, str] = {}
        order: list[int] = []

        async for e in handle.fetch_history_events():
            s = e.activity_task_scheduled_event_attributes
            if s and s.activity_type.name:
                detail: dict = {}
                try:
                    decoded = await client.data_converter.decode(list(s.input.payloads))
                    if decoded and isinstance(decoded[0], dict):
                        detail = decoded[0]
                except Exception:
                    detail = {}
                scheduled[e.event_id] = (s.activity_type.name, detail)
                states[e.event_id] = "scheduled"
                order.append(e.event_id)
                continue
            st = e.activity_task_started_event_attributes
            if st and st.scheduled_event_id in states:
                states[st.scheduled_event_id] = "running"
            cp = e.activity_task_completed_event_attributes
            if cp and cp.scheduled_event_id in states:
                states[cp.scheduled_event_id] = "completed"
            fa = e.activity_task_failed_event_attributes
            if fa and fa.scheduled_event_id in states:
                states[fa.scheduled_event_id] = "failed"

        timeline: list[dict] = []
        for i, eid in enumerate(order):
            atype, detail = scheduled[eid]
            kind = detail.get("type") or _ACT_KIND.get(atype) or atype
            timeline.append(
                {
                    "seq": i,
                    "activity": atype,
                    "kind": kind,
                    "label": _label_for(kind, atype),
                    "status": states[eid],
                    "tool_name": detail.get("tool_name"),
                    "model": detail.get("model"),
                    "query": detail.get("query"),
                    "risk": detail.get("risk"),
                    "gate": detail.get("gate"),
                }
            )
        return timeline
    except Exception:
        # Workflow not started yet / not found / Temporal down — no timeline yet.
        return []


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents() -> list[dict]:
    """Every agent in the bundle folder, discovered live. Dynamic: a new agent
    folder shows up here with no code change."""
    agents = fs.discover()
    return list(await asyncio.gather(*(_describe(a) for a in agents)))


@router.post("/submit")
async def submit(req: AskRequest) -> dict:
    """Start the chosen agent on the prompt and return immediately with the ids
    needed to follow it live via GET /user/status."""
    prompt = req.input.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="input must not be empty")

    agent, state = await _ensure_up(req.agent, req.auto_start)
    base = fs.base_url(state["port"])
    info = await _fetch_info(state["port"])
    agent_id = info.get("agent_id")  # the radar/Temporal id, e.g. agent-langgraph-<hash>

    task = await _submit_task(base, prompt)
    task_id = task["id"]
    workflow_id = f"task-{agent_id}-{task_id}" if agent_id else None

    return {
        "agent": req.agent,
        "agent_url": base,
        "agent_id": agent_id,
        "task_id": task_id,
        "status": task.get("status", "queued"),
        "workflow_id": workflow_id,
        "temporal_url": _temporal_url(workflow_id),
    }


@router.post("/upload/{agent}")
async def upload(agent: str, file: UploadFile = File(...)) -> dict:
    """Relay a browser file upload to the chosen agent's own /upload endpoint.

    The dashboard (a different origin) can't POST straight to the agent, so this
    proxies the multipart file through the gateway. The agent saves it on its own
    filesystem and returns a local path; the UI then submits a goal that carries
    `FILE_PATH: <path>`, which the agent reads back. Only agents that advertise
    `accepts_files` in /info are offered an upload box by the UI, but any running
    agent with an /upload route works here. Auto-starts the agent if needed.
    """
    _agent, state = await _ensure_up(agent, auto_start=True)
    base = fs.base_url(state["port"])
    content = await file.read()
    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            r = await c.post(
                f"{base}/upload",
                files={
                    "file": (
                        file.filename or "upload",
                        content,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail={"message": "could not reach the agent for upload", "error": str(e)},
            )
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "agent rejected the upload",
                "status_code": r.status_code,
                "body": r.text[:500],
            },
        )
    return r.json()


@router.get("/status/{agent}/{task_id}")
async def status(agent: str, task_id: str, workflow_id: str | None = None) -> dict:
    """Live state for one running/finished task: the agent's own record merged
    with the dynamic per-step timeline read back from Temporal."""
    agent_d = fs.find(agent)
    task_state: dict = {}
    if agent_d:
        st = await asyncio.to_thread(fs.running_state, agent_d)
        if st["port"]:
            task_state = await _fetch_task(st["port"], task_id)

    timeline = await _extract_timeline(workflow_id)

    status_val = task_state.get("status")
    if not status_val:
        if any(t["kind"] == "complete" for t in timeline):
            status_val = "done"
        elif timeline:
            status_val = "running"
        else:
            status_val = "pending"

    return {
        "agent": agent,
        "task_id": task_id,
        "status": status_val,
        "result": task_state.get("result", ""),
        "tools_used": task_state.get("tools_used", []),
        "steps": task_state.get("steps", []),
        "awaiting": task_state.get("awaiting"),
        "error": task_state.get("error", ""),
        "workflow_id": workflow_id,
        "temporal_url": _temporal_url(workflow_id),
        "timeline": timeline,
    }


@router.post("/approve/{agent}/{task_id}")
async def approve(agent: str, task_id: str, body: ApproveBody) -> dict:
    """Proxy an operator approve/deny to a paused high-risk task on the agent."""
    agent_d = fs.find(agent)
    if not agent_d:
        raise HTTPException(status_code=404, detail=f"unknown agent '{agent}'")
    st = await asyncio.to_thread(fs.running_state, agent_d)
    if not st["port"]:
        raise HTTPException(status_code=409, detail=f"agent '{agent}' is not running")
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.post(
            f"{fs.base_url(st['port'])}/tasks/{task_id}/approve",
            json={"decision": body.decision},
        )
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=r.text[:500])
        return r.json()


@router.post("/ask")
async def ask(req: AskRequest) -> dict:
    """Run the chosen agent on the prompt, BLOCK until it settles, return result.

    The simple one-shot API. For a live timeline use /user/submit + /user/status.
    """
    prompt = req.input.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="input must not be empty")

    agent, state = await _ensure_up(req.agent, req.auto_start)
    base = fs.base_url(state["port"])
    info = await _fetch_info(state["port"])
    agent_id = info.get("agent_id")

    task = await _submit_task(base, prompt)
    task_id = task["id"]
    workflow_id = f"task-{agent_id}-{task_id}" if agent_id else None

    deadline = time.monotonic() + TASK_TIMEOUT
    while task.get("status") not in TERMINAL and time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        task = await _fetch_task(state["port"], task_id) or task

    return {
        "agent": req.agent,
        "agent_url": base,
        "task_id": task_id,
        "status": task.get("status"),
        "result": task.get("result", ""),
        "tools_used": task.get("tools_used", []),
        "steps": task.get("steps", []),
        "awaiting": task.get("awaiting"),
        "error": task.get("error", ""),
        "workflow_id": workflow_id,
        "temporal_url": _temporal_url(workflow_id),
        "timeline": await _extract_timeline(workflow_id),
    }
