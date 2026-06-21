"""Temporal activities that drive the AWCP agent loop via the MCP server.

Each activity is one logical step (reason / tool call / generate / admission
check) and acts as an MCP client. By default it spawns a LOCAL AWCP MCP server
over stdio. If AWCP_MCP_SSE_URL is set, it instead connects to a REMOTE MCP
server over SSE.
"""

import base64
import json
import logging
import os
import time
from contextlib import asynccontextmanager, contextmanager

import httpx
from opentelemetry import propagate, trace as otel_trace
from opentelemetry.trace import Status, StatusCode
from temporalio import activity

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from awcp.temporal.config import (
    MCP_PYTHON,
    MCP_SERVER_ARGS,
    MCP_WORKDIR,
    SRC_DIR,
    MCP_SSE_URL,
    MCP_SSE_AUTH,
)
from awcp.observability.middleware import AWCPMetrics


logger = logging.getLogger(__name__)

# Radar write-action gate (the policy_gate activity calls it over HTTP). Same
# env knobs the MCP firewall uses, so the gate behaves identically wherever it is
# consulted. Nothing hardcoded — URL, timeout, and fail mode are all env-driven.
RADAR_URL = os.getenv("AGENT_RADAR_URL", "http://localhost:8090").rstrip("/")
GATE_TIMEOUT = float(os.getenv("AWCP_GATE_TIMEOUT", "3"))
GATE_FAIL_OPEN = os.getenv("AWCP_GATE_FAIL_OPEN", "true").lower() == "true"

# Lazy metrics — created on first activity call, after setup_otel() has run
_awcp_metrics: AWCPMetrics | None = None


def _get_metrics() -> AWCPMetrics:
    global _awcp_metrics
    if _awcp_metrics is None:
        _awcp_metrics = AWCPMetrics()
    return _awcp_metrics


def _extract_ctx(payload):
    """Extract OTel parent context from activity payload dict."""
    if not isinstance(payload, dict):
        return None
    carrier = payload.get("_otel_ctx") or {}
    return propagate.extract(carrier) if carrier else None


@contextmanager
def _act_span(act_name: str, parent_ctx=None, **attrs):
    """Creates a Temporal activity span, optionally linked to a parent trace."""
    tracer = otel_trace.get_tracer("awcp.temporal_worker")
    kw = {"context": parent_ctx} if parent_ctx is not None else {}
    with tracer.start_as_current_span(f"activity.{act_name}", **kw) as span:
        span.set_attribute("temporal.activity_type", act_name)
        for k, v in attrs.items():
            if v is not None:
                span.set_attribute(k, str(v)[:256])
        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


# ── MCP session helpers ──────────────────────────────────────────────────────

def _server_params() -> StdioServerParameters:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = SRC_DIR + (os.pathsep + existing if existing else "")
    return StdioServerParameters(
        command=MCP_PYTHON,
        args=MCP_SERVER_ARGS,
        cwd=MCP_WORKDIR,
        env=env,
    )


def _sse_headers() -> dict:
    headers = {"ngrok-skip-browser-warning": "true"}
    if MCP_SSE_AUTH:
        token = base64.b64encode(MCP_SSE_AUTH.encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    return headers


@asynccontextmanager
async def _mcp_session():
    """Yield an initialized MCP session over SSE (if configured) or stdio."""
    if MCP_SSE_URL:
        async with sse_client(MCP_SSE_URL, headers=_sse_headers()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        async with stdio_client(_server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def _call_mcp(tool_name: str, arguments: dict) -> str:
    """Call one MCP tool. Creates a child span under the currently active span."""
    logger.info("Starting MCP tool call: %s", tool_name)
    start = time.time()
    tracer = otel_trace.get_tracer("awcp.mcp_gateway")

    with tracer.start_as_current_span(f"mcp.{tool_name}") as span:
        span.set_attribute("mcp.tool_name", tool_name)
        try:
            async with _mcp_session() as session:
                result = await session.call_tool(tool_name, arguments)
                text_parts = [
                    block.text
                    for block in result.content
                    if getattr(block, "type", None) == "text"
                ]
                output = "\n".join(text_parts).strip()
                span.set_attribute("mcp.output_chars", len(output))
                logger.info("Completed MCP tool call: %s output_chars=%s", tool_name, len(output))
                _get_metrics().record_mcp_call(tool_name, time.time() - start)
                return output
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.exception("MCP tool call failed: %s", tool_name)
            raise


def _unwrap_execute_tool(raw: str) -> str:
    """execute_tool now returns a JSON governance envelope
    {status, output, decision, ...} (the MCP server is the write-action firewall).
    Callers here expect the raw tool-output string, so unwrap to `output` when the
    envelope is present; fall back to the raw text for any non-envelope response."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if isinstance(data, dict) and "output" in data and "status" in data:
        return str(data["output"])
    return raw


# ── Activities ───────────────────────────────────────────────────────────────

@activity.defn
async def mcp_get_agent_info(payload: dict) -> dict:
    """Admission control: fetch the agent manifest (incl. quarantine status)."""
    agent_name = payload["agent_name"]
    ctx = _extract_ctx(payload)
    start = time.time()

    with _act_span("mcp_get_agent_info", ctx, agent_name=agent_name):
        try:
            raw = await _call_mcp("get_agent_info", {"agent_name": agent_name})
            if raw.startswith("Agent '") and "not found" in raw:
                raise ValueError(raw)
            manifest: dict = {"name": agent_name, "raw": raw}
            for line in raw.splitlines():
                if ": " in line:
                    key, _, value = line.partition(": ")
                    manifest[key.strip().lower()] = value.strip()
            _get_metrics().record_activity("mcp_get_agent_info", time.time() - start, "success")
            return manifest
        except Exception:
            _get_metrics().record_activity("mcp_get_agent_info", time.time() - start, "failed")
            raise


@activity.defn
async def mcp_agent_route(payload: dict) -> dict:
    """Reasoning step: decide SEARCH vs ANSWER for the prompt."""
    ctx = _extract_ctx(payload)
    start = time.time()

    with _act_span("mcp_agent_route", ctx, agent_name=payload.get("agent_name")):
        try:
            raw = await _call_mcp(
                "agent_route",
                {"agent_name": payload["agent_name"], "prompt": payload["input"]},
            )
            try:
                result = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result = {"action": "ANSWER", "raw": raw}
            _get_metrics().record_activity("mcp_agent_route", time.time() - start, "success")
            return result
        except Exception:
            _get_metrics().record_activity("mcp_agent_route", time.time() - start, "failed")
            raise


@activity.defn
async def mcp_execute_tool(payload: dict) -> str:
    """Tool Executor step: run a single registered tool (e.g. web_search)."""
    ctx = _extract_ctx(payload)
    start = time.time()

    with _act_span("mcp_execute_tool", ctx, tool_name=payload.get("tool_name")):
        try:
            result = _unwrap_execute_tool(await _call_mcp(
                "execute_tool",
                {"tool_name": payload["tool_name"], "tool_input": payload["tool_input"]},
            ))
            _get_metrics().record_activity("mcp_execute_tool", time.time() - start, "success")
            return result
        except Exception:
            _get_metrics().record_activity("mcp_execute_tool", time.time() - start, "failed")
            raise


@activity.defn
async def mcp_agent_generate(payload: dict) -> str:
    """Generation/synthesis step. Grounds the answer if search_results given."""
    ctx = _extract_ctx(payload)
    start = time.time()

    with _act_span("mcp_agent_generate", ctx, agent_name=payload.get("agent_name")):
        try:
            arguments = {
                "agent_name": payload["agent_name"],
                "prompt": payload["input"],
            }
            if payload.get("search_results"):
                arguments["search_results"] = payload["search_results"]
            result = await _call_mcp("agent_generate", arguments)
            _get_metrics().record_activity("mcp_agent_generate", time.time() - start, "success")
            return result
        except Exception:
            _get_metrics().record_activity("mcp_agent_generate", time.time() - start, "failed")
            raise


@activity.defn(name="call_llm")
async def mcp_call_llm(payload: dict) -> dict:
    """First attempt: ask the MCP-hosted LLM for a final answer if safe."""
    ctx = _extract_ctx(payload)
    start = time.time()
    logger.info("Starting call_llm activity")

    with _act_span("call_llm", ctx, query=payload.get("query", "")[:80]):
        try:
            raw = await _call_mcp("call_llm", {"query": payload["query"]})
            try:
                parsed = json.loads(raw)
                logger.info("Completed call_llm activity final=%s", parsed.get("final"))
            except (json.JSONDecodeError, TypeError):
                logger.exception("call_llm returned malformed JSON")
                parsed = {
                    "configured": False,
                    "final": False,
                    "answer": "",
                    "reason": "MCP call_llm returned malformed JSON.",
                    "raw": raw,
                }
            _get_metrics().record_activity("call_llm", time.time() - start, "success")
            return parsed
        except Exception:
            _get_metrics().record_activity("call_llm", time.time() - start, "failed")
            raise


@activity.defn(name="discover_tools")
async def mcp_discover_tools(payload: dict) -> list[dict]:
    """Discover runtime tools dynamically from the MCP server."""
    ctx = _extract_ctx(payload)
    start = time.time()
    logger.info("Starting discover_tools activity")

    with _act_span("discover_tools", ctx):
        try:
            raw = await _call_mcp("list_runtime_tools", {})
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.exception("discover_tools returned malformed JSON")
                raise ValueError(f"MCP list_runtime_tools returned malformed JSON: {raw}")
            if not isinstance(parsed, list):
                raise ValueError("MCP list_runtime_tools did not return a list.")
            logger.info("Completed discover_tools activity count=%s", len(parsed))
            _get_metrics().record_activity("discover_tools", time.time() - start, "success")
            return parsed
        except Exception:
            _get_metrics().record_activity("discover_tools", time.time() - start, "failed")
            raise


@activity.defn(name="select_tools")
async def mcp_select_tools(payload: dict) -> dict:
    """Ask the MCP-hosted selector to choose from discovered tools."""
    ctx = _extract_ctx(payload)
    start = time.time()
    logger.info("Starting select_tools activity")

    with _act_span("select_tools", ctx, query=payload.get("query", "")[:80]):
        try:
            raw = await _call_mcp(
                "select_runtime_tools",
                {"query": payload["query"], "tools": payload["tools"]},
            )
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.exception("select_tools returned malformed JSON")
                raise ValueError(f"MCP select_runtime_tools returned malformed JSON: {raw}")
            calls = parsed.get("tool_calls", [])
            if not isinstance(calls, list):
                parsed["tool_calls"] = []
            logger.info("Completed select_tools activity selected=%s", len(parsed["tool_calls"]))
            _get_metrics().record_activity("select_tools", time.time() - start, "success")
            return parsed
        except Exception:
            _get_metrics().record_activity("select_tools", time.time() - start, "failed")
            raise


@activity.defn(name="run_tool")
async def mcp_run_tool(payload: dict) -> dict:
    """Run exactly one dynamically selected runtime tool via MCP."""
    tool_name = payload["tool_name"]
    ctx = _extract_ctx(payload)
    start = time.time()
    logger.info("Starting run_tool activity tool_name=%s", tool_name)

    with _act_span("run_tool", ctx, tool_name=tool_name):
        try:
            output = _unwrap_execute_tool(await _call_mcp(
                "execute_tool",
                {"tool_name": tool_name, "tool_input": payload.get("tool_input") or {}},
            ))
            if output.startswith(f"Error executing tool '{tool_name}'"):
                raise RuntimeError(output)
            logger.info("Completed run_tool activity tool_name=%s output_chars=%s", tool_name, len(output))
            _get_metrics().record_activity("run_tool", time.time() - start, "success")
            return {
                "tool_name": tool_name,
                "tool_input": payload.get("tool_input") or {},
                "output": output,
                "status": "succeeded",
            }
        except Exception:
            _get_metrics().record_activity("run_tool", time.time() - start, "failed")
            raise


@activity.defn(name="policy_gate")
async def policy_gate(payload: dict) -> dict:
    """Policy gate step (magazine Step 03) — ask the radar's write-action gate
    BEFORE a state-changing tool runs, so the workflow can pause/deny durably.

    Returns the radar's decision dict {decision, mode, reason, requires_approval,
    ...}. On any failure falls back to allow/deny per AWCP_GATE_FAIL_OPEN so a
    missing control plane never hard-breaks the workflow (unless ops fail-closed).
    """
    agent_id = payload.get("agent_id") or ""
    ctx = _extract_ctx(payload)
    start = time.time()

    with _act_span("policy_gate", ctx, agent_id=agent_id,
                   tool_name=payload.get("tool_name")):
        if not agent_id:
            # No identity to gate against — treat as ungoverned (same as the MCP firewall).
            _get_metrics().record_activity("policy_gate", time.time() - start, "success")
            return {"decision": "allow", "mode": "ungoverned",
                    "reason": "no agent_id — action not attributed to a governed agent",
                    "requires_approval": False}
        body = {
            "action": payload.get("action") or payload.get("tool_name") or "",
            "write": bool(payload.get("write", True)),
            "scope": payload.get("scope") or "",
            "tool_name": payload.get("tool_name") or "",
            "workflow_id": payload.get("workflow_id") or "",
            "task_id": payload.get("task_id") or "",
            "actor": payload.get("actor") or "agent",
            "approval_token": payload.get("approval_token") or "",
        }
        try:
            async with httpx.AsyncClient(timeout=GATE_TIMEOUT) as c:
                resp = await c.post(f"{RADAR_URL}/agents/{agent_id}/gate", json=body)
            if resp.status_code == 200:
                _get_metrics().record_activity("policy_gate", time.time() - start, "success")
                return resp.json()
            decision = {"decision": "allow" if GATE_FAIL_OPEN else "deny",
                        "mode": "gate_unavailable",
                        "reason": f"radar gate returned HTTP {resp.status_code}",
                        "requires_approval": False}
        except Exception as exc:  # noqa: BLE001 — the gate must never crash the workflow
            logger.warning("temporal.policy_gate.error agent_id=%s error=%r", agent_id, exc)
            decision = {"decision": "allow" if GATE_FAIL_OPEN else "deny",
                        "mode": "gate_unavailable",
                        "reason": f"radar gate unreachable: {type(exc).__name__}",
                        "requires_approval": False}
        _get_metrics().record_activity("policy_gate", time.time() - start, "success")
        return decision


@activity.defn(name="synthesize_answer")
async def mcp_synthesize_answer(payload: dict) -> str:
    """Generate the final answer from collected tool outputs."""
    ctx = _extract_ctx(payload)
    start = time.time()
    logger.info(
        "Starting synthesize_answer activity tool_result_count=%s",
        len(payload.get("tool_results") or []),
    )

    with _act_span("synthesize_answer", ctx):
        try:
            answer = await _call_mcp(
                "synthesize_tool_results",
                {"query": payload["query"], "tool_results": payload["tool_results"]},
            )
            logger.info("Completed synthesize_answer activity answer_chars=%s", len(answer))
            _get_metrics().record_activity("synthesize_answer", time.time() - start, "success")
            return answer
        except Exception:
            logger.exception("synthesize_answer failed")
            _get_metrics().record_activity("synthesize_answer", time.time() - start, "failed")
            raise
