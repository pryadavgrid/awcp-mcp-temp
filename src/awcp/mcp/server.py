import asyncio
import arxiv
import inspect
import json
import logging

import os
import sys
from typing import Annotated, Any

import httpx
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from starlette.responses import HTMLResponse
from starlette.routing import Route

# AWCP Integration Imports
from awcp.registry.service import build_registry
from awcp.registry import store
from awcp.runtime.tool_runtime import (
    TOOL_REGISTRY,
    discover_tools,
    execute_tool as run_tool,
    get_tool_risk,
    get_tool_scope,
    is_write_risk,
)
from awcp.runtime.ollama_client import ask_ollama
from awcp.runtime.config import SEARCH_MODEL
from awcp.context_graph.client import record_checkpoint as _cg_record
from awcp.runtime.json_utils import extract_json
from awcp.runtime.schemas import PromptRequest
from awcp.agents.ollama_search import build_search_answer_prompt
from awcp.observability.setup import setup_otel
from awcp.observability.middleware import instrument_requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OpenTelemetry
setup_otel("awcp-mcp-server")
instrument_requests()
logger = logging.getLogger(__name__)

# Initialize the FastMCP server. FastMCP auto-generates each tool's JSON schema
# from the function signature/type hints, and wires both stdio and SSE transports.
mcp = FastMCP("awcp-control-plane")

# Initialize AWCP Components.
# NOTE: logs go to stderr so they never corrupt the stdio JSON-RPC stream
# (stdout is the protocol channel when this server runs over stdio).
print("Initializing AWCP Control Plane components...", file=sys.stderr)
discover_tools()
agent_specs = build_registry()
print(f"✓ Registered {len(agent_specs)} agents in MCP Control Plane", file=sys.stderr)

# Look up the discovered AgentSpec by name. The decomposed tools below read
# each agent's self-declared `router` / `tool` / `model` from its spec, so no
# agent is special-cased here — new tool-using agents work just by declaring
# those fields in their AgentSpec (dynamic discovery stays intact).
_SPECS_BY_NAME = {s.name: s for s in agent_specs}
# Fallback model when an agent does not declare one.
_DEFAULT_MODEL = SEARCH_MODEL
_MAX_SYNTHESIS_OUTPUT_CHARS = int(os.getenv("AWCP_SYNTHESIS_TOOL_CHARS", "12000"))


# ======================================================================
# Governance plane — the MCP server is the WRITE-ACTION FIREWALL.
#
# execute_tool routes every governed call through the radar's write-action gate
# BEFORE the tool runs, and traces the run as a child of the caller's span. The
# agent can no longer bypass governance: the only way to run a tool is through
# this server, and this server asks the gate. Everything is env-driven — the
# radar URL, the timeout, and each tool's risk/scope all come from config or the
# tool's own declaration, never a hardcoded per-agent rule.
# ======================================================================
RADAR_URL = os.getenv("AGENT_RADAR_URL", "http://localhost:8090").rstrip("/")
GATE_TIMEOUT = float(os.getenv("AWCP_GATE_TIMEOUT", "3"))
# Fail-open keeps agents working when the control plane is down (matches the
# agent-side philosophy: radar offline -> allow). Set false to fail-closed.
GATE_FAIL_OPEN = os.getenv("AWCP_GATE_FAIL_OPEN", "true").lower() == "true"
# Whether to forward the tool's write SCOPE to the gate. The radar's declared-
# scope check is magazine-driven: an agent may only write within scopes the
# operator GRANTED it in the magazine, and onboarding fails closed (no scopes)
# for agents the magazine doesn't list. The original agent flow sent no scope, so
# that check was dormant; we keep it OFF by default so the gate enforces the core
# governance (quarantine / autonomy ladder / token budget) without denying every
# write from an un-magazined bundle agent. Set true once the magazine grants the
# agents their scopes, to turn on strict per-scope authorization.
GATE_SEND_SCOPE = os.getenv("AWCP_GATE_SEND_SCOPE", "false").lower() == "true"

# Meter every governed tool call's REAL token footprint (its input + output) into
# Laminar via the gateway, so each tool call shows in the Token Monitor / Laminar
# with meaningful numbers — not only the LLM calls. The MCP server is the ONE place
# tools actually run, so it is the only place the real I/O exists. Env-toggle;
# best-effort (a metering hiccup never affects the tool result).
METER_TOOL_TOKENS = os.getenv("AWCP_METER_TOOL_TOKENS", "true").lower() == "true"


def _meter_tool_tokens(agent_id: str, task_id: str, tool_name: str,
                       tool_input: dict, output: Any) -> None:
    """Log this tool call's real input+output token usage to Laminar (gateway
    /laminar/record, which estimates with the shared tiktoken estimator)."""
    if not (METER_TOOL_TOKENS and agent_id and RADAR_URL):
        return
    try:
        text = (json.dumps(tool_input or {}, ensure_ascii=False) + " " + str(output))[:20000]
        httpx.post(f"{RADAR_URL}/laminar/record", json={
            "agent_id": agent_id, "task_id": task_id or "tool",
            "tool_name": tool_name, "model": f"tool:{tool_name}",
            "step": "tool_called", "text": text,
        }, timeout=GATE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — metering must never break a tool call
        logger.debug("mcp.meter.failed tool=%s error=%r", tool_name, exc)


def _record_checkpoint(agent_id: str, task_id: str, tool_name: str,
                       tool_input: dict, gate: dict, eff_risk: str,
                       outcome: str, error: str = "") -> None:
    """Record this tool call as a node in the context graph (the governed-step
    trail). `outcome` is "succeeded" (the tool ran), "blocked" (the gate denied it
    before it ran), or "error" (the tool ran but raised) — all are part of the
    trail. Best-effort HTTP to the radar; never breaks a tool call. The
    resume_pointer marks where the run continues from; the context is the tool
    input (so equal inputs get an equal context_hash)."""
    if not agent_id:
        return
    marker = {"blocked": "blocked-at", "error": "errored-at"}.get(outcome, "after")
    payload = {"tool": tool_name, "risk": eff_risk, "outcome": outcome,
               "decision": gate.get("decision", "allow"),
               "mode": gate.get("mode", ""),
               "reason": error or gate.get("reason", "")}
    if error:
        payload["error"] = error[:500]
    _cg_record(
        RADAR_URL, agent_id,
        step=f"tool:{tool_name}",
        task_id=task_id,
        workflow_id=task_id,
        actor=agent_id,
        resume_pointer=f"{task_id or 'task'}:{marker}:tool:{tool_name}",
        context=tool_input or {},
        payload=payload,
        timeout=GATE_TIMEOUT,
    )


def _radar_gate(agent_id: str, action: str, scope: str, is_write: bool) -> dict:
    """Ask the radar's write-action gate. Returns the radar's decision dict.
    On any failure falls back to allow/deny per AWCP_GATE_FAIL_OPEN so a missing
    control plane never hard-breaks tool execution (unless ops opt into fail-closed)."""
    if not agent_id:
        # No identity to gate against — treat as an ungoverned (direct) call.
        return {"decision": "allow", "mode": "ungoverned",
                "reason": "no agent_id supplied — call not attributed to a governed agent"}
    try:
        resp = httpx.post(
            f"{RADAR_URL}/agents/{agent_id}/gate",
            json={"action": action, "write": is_write, "scope": scope},
            timeout=GATE_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        # 404 = agent not registered yet; other codes = radar trouble.
        return {"decision": "allow" if GATE_FAIL_OPEN else "deny",
                "mode": "gate_unavailable",
                "reason": f"radar gate returned HTTP {resp.status_code}"}
    except Exception as exc:  # noqa: BLE001 — the gate must never crash a tool call
        logger.warning("mcp.gate.error agent_id=%s action=%s error=%r", agent_id, action, exc)
        return {"decision": "allow" if GATE_FAIL_OPEN else "deny",
                "mode": "gate_unavailable",
                "reason": f"radar gate unreachable: {type(exc).__name__}"}


def _govern_span(name: str, trace_context: dict | None):
    """Start an OTel span as a CHILD of the caller's trace context (W3C
    traceparent), so the server-side tool span stitches into the agent's task
    trace in Tempo. No-op-safe if OTel/propagation is unavailable."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        try:
            from opentelemetry import trace
            from opentelemetry.propagate import extract
            parent = extract(trace_context or {})
            tracer = trace.get_tracer("awcp.mcp.govern")
            with tracer.start_as_current_span(name, context=parent) as span:
                yield span
        except Exception:  # noqa: BLE001
            yield None

    return _cm()


# ======================================================================
# Workspace tools
# ======================================================================

@mcp.tool(description="Read the contents of a file in the workspace.")
def read_file(
    path: Annotated[str, Field(description="Relative path to the file")],
) -> str:
    if ".." in path or path.startswith("/"):
        return "Error: Invalid path."
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"


@mcp.tool(description="Write or overwrite a file in the workspace.")
def write_file(
    path: Annotated[str, Field(description="Relative path to the file")],
    content: Annotated[str, Field(description="Content to write")],
) -> str:
    if ".." in path or path.startswith("/"):
        return "Error: Invalid path."
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@mcp.tool(description="Execute a shell command in the workspace.")
async def run_command(
    command: Annotated[str, Field(description="The shell command to run")],
) -> str:
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return f"STDOUT:\n{stdout.decode()}\nSTDERR:\n{stderr.decode()}"
    except Exception as e:
        return f"Error executing command: {str(e)}"


@mcp.tool(description="Search academic papers on arXiv.")
def search_arxiv(
    query: Annotated[str, Field(description="Search query for arXiv papers")],
    max_results: Annotated[int, Field(description="Maximum number of results to return")] = 5,
) -> list[dict]:
    try:
        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=max_results
        )
        papers = []
        for paper in client.results(search):
            papers.append(
                {
                    "title": paper.title,
                    "authors": [str(a) for a in paper.authors],
                    "summary": paper.summary,
                    "published": str(paper.published),
                    "pdf_url": paper.pdf_url,
                    "entry_id": paper.entry_id,
                }
            )
        return papers
    except Exception as e:
        raise RuntimeError(
            f"Arxiv search failed: {str(e)}"
        )


# ======================================================================
# Registry tools

# ======================================================================

@mcp.tool(description="List all agents registered in the AWCP Model Registry.")
def list_agents() -> str:
    agents = store.get_all()
    if not agents:
        return "No agents found in registry."
    result = "\n".join(
        f"- {a.name} (Status: {a.status}, ID: {a.agent_id})" for a in agents
    )
    return f"AWCP Registered Agents:\n{result}"


@mcp.tool(description="Get detailed manifest and status for a specific agent.")
def get_agent_info(
    agent_name: Annotated[str, Field(description="The name of the agent")],
) -> str:
    agents = store.get_all()
    agent = next((a for a in agents if a.name == agent_name), None)
    if not agent:
        return f"Agent '{agent_name}' not found."

    return (
        f"Agent Manifest: {agent.name}\n"
        f"{'='*30}\n"
        f"ID: {agent.agent_id}\n"
        f"Status: {agent.status}\n"
        f"Owner: {agent.owner}\n"
        f"Version: {agent.version}\n"
        f"Runtime: {agent.runtime}\n"
        f"Write Scopes: {', '.join(agent.write_scopes) if agent.write_scopes else 'None'}\n"
        f"Route: {agent.route}\n"
        f"Endpoint: {agent.endpoint_url}"
    )


@mcp.tool(description="Directly invoke an agent model with a prompt.")
def invoke_agent(
    agent_name: Annotated[str, Field(description="The name of the agent to invoke")],
    prompt: Annotated[str, Field(description="The input prompt for the agent")],
) -> str:
    spec = next((s for s in agent_specs if s.name == agent_name), None)
    if not spec:
        return f"Agent spec for '{agent_name}' not found."

    try:
        # Special case for agents requiring API keys (like deepseek)
        if agent_name == "deepseek-chat":
            return (
                "Error: DeepSeek agent requires an NVIDIA API key which must be "
                "provided via the main Agent Service REST API."
            )

        req = PromptRequest(input=prompt)
        response = spec.handler(req)
        output = response.get("output", str(response))
        return f"Agent Response:\n{output}"
    except Exception as e:
        return f"Error invoking agent: {str(e)}"


# ======================================================================
# Decomposed execution primitives
# These let an external orchestrator (e.g. Temporal) drive the agent loop
# step-by-step: reason -> (policy gate) -> tool call -> generate.
# ======================================================================

@mcp.tool(
    description=(
        "Reasoning/decision step. Decides whether an agent needs an external "
        'tool (SEARCH) or can answer directly (ANSWER). Returns JSON: '
        '{"action": "SEARCH"|"ANSWER", "search_query": str}.'
    )
)
def agent_route(
    agent_name: Annotated[str, Field(description="The name of the agent")],
    prompt: Annotated[str, Field(description="The user prompt to route")],
) -> str:
    # Delegate to the agent's self-declared router. Agents without one never
    # use a tool and answer directly.
    spec = _SPECS_BY_NAME.get(agent_name)

    if spec is not None and spec.router is not None:
        try:
            decision = spec.router(prompt)
        except Exception as e:
            # Fail safe: answer directly rather than blocking the workflow.
            decision = {"action": "ANSWER", "error": str(e)}

        # Wire the agent's declared tool onto a SEARCH decision unless the
        # router already chose a specific tool.
        if decision.get("action") == "SEARCH" and spec.tool:
            decision.setdefault("tool_name", spec.tool)

        # Governance allow-list for handoffs: an agent may only delegate to a
        # sub-agent it declared in `delegates_to`; otherwise fall back to ANSWER.
        elif decision.get("action") == "DELEGATE":
            target = decision.get("agent")
            if not target or target not in (spec.delegates_to or []):
                decision = {
                    "action": "ANSWER",
                    "error": f"delegate target '{target}' not allowed",
                }
    else:
        decision = {"action": "ANSWER"}

    return json.dumps(decision)


@mcp.tool(
    description=(
        "Governed Tool Executor — the write-action firewall. Runs a single "
        "registered tool through the radar's write-action gate first, traces the "
        "run as a child of the caller's span, and returns a JSON envelope: "
        '{"status","output","decision","mode","reason","risk"}. status is '
        '"succeeded" | "blocked" | "error". Reads run ungated; medium/high-risk '
        "writes are gated by the radar for the calling agent_id."
    )
)
def execute_tool(
    tool_name: Annotated[str, Field(description="Registered tool name, e.g. web_search")],
    tool_input: Annotated[dict, Field(description="Arguments passed to the tool")],
    agent_id: Annotated[str, Field(
        description="Calling agent's id (as registered with the radar). Drives the gate.")] = "",
    task_id: Annotated[str, Field(
        description="Calling agent's task id (for trace/audit correlation).")] = "",
    risk: Annotated[str, Field(
        description="Optional risk override; defaults to the tool's declared risk.")] = "",
    scope: Annotated[str, Field(
        description="Optional write scope override; defaults to the tool's declared scope.")] = "",
    approved: Annotated[bool, Field(
        description="Whether an operator already approved this high-risk write agent-side.")] = False,
    trace_context: Annotated[dict | None, Field(
        description="W3C trace context (traceparent) so the run links into the caller's trace.")] = None,
) -> str:
    # Resolve governance facts dynamically: explicit overrides win, else the
    # tool's own declaration / env map / default. Nothing per-tool is hardcoded.
    eff_risk = (risk or get_tool_risk(tool_name)).lower()
    eff_scope = scope or get_tool_scope(tool_name)
    is_write = is_write_risk(eff_risk)

    with _govern_span(f"awcp.mcp.govern.{tool_name}", trace_context) as span:
        if span is not None:
            for k, v in (("agent.id", agent_id), ("task.id", task_id),
                         ("tool.name", tool_name), ("tool.risk", eff_risk),
                         ("tool.scope", eff_scope), ("tool.is_write", is_write),
                         ("tool.approved", approved)):
                try:
                    span.set_attribute(k, v if isinstance(v, bool) else str(v))
                except Exception:  # noqa: BLE001
                    pass

        # 1) Governance gate — consulted for EVERY tool, read or write. We still
        #    pass is_write, so the radar's BASE policy keeps its semantics (reads
        #    are allowed; writes are gated by quarantine / autonomy / token). The
        #    reason we always call it (instead of short-circuiting reads) is so an
        #    operator's deny-list (the policy-guard) can veto ANY tool by name —
        #    the guard runs inside the gate and can tighten a read's "allow" into a
        #    "deny". Nothing tool-specific is hardcoded; the gate decides.
        #    The scope is only forwarded when strict magazine-scope authorization
        #    is enabled.
        gate = _radar_gate(
            agent_id, tool_name, eff_scope if GATE_SEND_SCOPE else "", is_write
        )
        decision = gate.get("decision", "allow")
        if span is not None:
            try:
                span.set_attribute("gate.decision", decision)
                span.set_attribute("gate.mode", gate.get("mode", ""))
            except Exception:  # noqa: BLE001
                pass

        if decision == "deny":
            logger.warning(
                "mcp.execute.blocked agent_id=%s tool=%s risk=%s mode=%s reason=%s",
                agent_id, tool_name, eff_risk, gate.get("mode"), gate.get("reason"),
            )
            # Record the blocked attempt as a node too — a denial is part of the trail.
            _record_checkpoint(agent_id, task_id, tool_name, tool_input or {}, gate,
                               eff_risk, "blocked")
            return json.dumps({
                "status": "blocked",
                "output": (f"BLOCKED: '{tool_name}' was denied by the AWCP "
                           f"governance gate ({gate.get('reason', '')})."),
                "decision": "deny",
                "mode": gate.get("mode", ""),
                "reason": gate.get("reason", ""),
                "risk": eff_risk,
            })

        # 2) Execute the registered tool (this is the ONLY place tools run).
        try:
            result = run_tool(tool_name, tool_input or {})
            # Meter the call's real input+output tokens into Laminar (best-effort).
            _meter_tool_tokens(agent_id, task_id, tool_name, tool_input or {}, result)
            # Record the step in the context graph (governed-step trail, best-effort).
            _record_checkpoint(agent_id, task_id, tool_name, tool_input or {}, gate,
                               eff_risk, "succeeded")
            logger.info(
                "mcp.execute.ok agent_id=%s tool=%s risk=%s decision=%s",
                agent_id, tool_name, eff_risk, decision,
            )
            return json.dumps({
                "status": "succeeded",
                "output": str(result),
                "decision": decision,
                "mode": gate.get("mode", "read" if not is_write else "gated"),
                "reason": gate.get("reason", ""),
                "risk": eff_risk,
            })
        except Exception as e:  # noqa: BLE001
            if span is not None:
                try:
                    span.set_attribute("tool.error", str(e)[:200])
                except Exception:  # noqa: BLE001
                    pass
            logger.warning("mcp.execute.error tool=%s error=%r", tool_name, e)
            # Record the failed step as an error node in the context graph.
            _record_checkpoint(agent_id, task_id, tool_name, tool_input or {}, gate,
                               eff_risk, "error", error=str(e))
            return json.dumps({
                "status": "error",
                "output": f"Error executing tool '{tool_name}': {str(e)}",
                "decision": decision,
                "mode": gate.get("mode", ""),
                "reason": str(e),
                "risk": eff_risk,
            })


@mcp.tool(
    description=(
        "List dynamically registered AWCP runtime tools that can be executed "
        "through execute_tool. Returns JSON with each tool name and parameters."
    )
)
def list_runtime_tools() -> str:
    tools: list[dict[str, Any]] = []

    for name, handler in sorted(TOOL_REGISTRY.items()):
        signature = inspect.signature(handler)
        parameters = []
        required = []

        for param_name, param in signature.parameters.items():
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            annotation = None
            if param.annotation is not inspect.Signature.empty:
                annotation = getattr(param.annotation, "__name__", str(param.annotation))

            parameters.append(
                {
                    "name": param_name,
                    "required": param.default is inspect.Signature.empty,
                    "type": annotation,
                }
            )
            if param.default is inspect.Signature.empty:
                required.append(param_name)

        tools.append(
            {
                "name": name,
                "description": inspect.getdoc(handler) or "",
                "parameters": parameters,
                "required": required,
                # Governance metadata so a discovering agent knows which tools are
                # writes (and need operator approval) before it calls execute_tool.
                "risk": get_tool_risk(name),
                "scope": get_tool_scope(name),
            }
        )

    return json.dumps(tools)


@mcp.tool(
    description=(
        "First-attempt LLM call for natural-language questions. Returns JSON "
        "with final=true only when the model can answer without external tools."
    )
)
def call_llm(
    query: Annotated[str, Field(description="Natural-language user query")],
) -> str:
    prompt = f"""
You are the first-pass answerer in a durable Temporal workflow.

Return ONLY JSON with this schema:
{{"final": true|false, "answer": string, "reason": string}}

CRITICAL RULES:
Set final=false when the query requires:
- Research papers or academic publications
- Latest/recent/current information or studies
- Scientific references or technical literature
- Arxiv papers or scholarly articles
- Live data, news, weather, prices, market data
- Rankings, statistics, or date-sensitive facts
- External verification or citations
- Documentation or API references
- Factual information that may have changed

Set final=true ONLY when:
- Pure reasoning, logic, or math problems
- Code writing or explanation tasks
- Creative writing or text generation
- Definitional questions about stable concepts
- Questions that explicitly say "from your knowledge" or "don't search"

When in doubt, set final=false to enable tool discovery.

Examples:
"latest research papers on AI" → final=false (needs research tool)
"find arxiv papers about transformers" → final=false (needs research tool)
"recent studies on quantum computing" → final=false (needs research tool)
"what is recursion" → final=true (stable knowledge)
"write a python function to sort a list" → final=true (code generation)
"who is the current CEO of OpenAI" → final=false (current information)

User query:
{query}
"""

    try:
        raw = ask_ollama(prompt, _DEFAULT_MODEL)
        parsed = extract_json(raw)
        return json.dumps(
            {
                "configured": True,
                "final": bool(parsed.get("final")),
                "answer": str(parsed.get("answer", "")),
                "reason": str(parsed.get("reason", "")),
                "raw": raw,
            }
        )
    except Exception as e:
        return json.dumps(
            {
                "configured": False,
                "final": False,
                "answer": "",
                "reason": f"LLM unavailable or returned invalid JSON: {e}",
            }
        )


@mcp.tool(
    description=(
        "Select registered runtime tools for a natural-language query from a "
        "discovered tool list. Returns JSON selection; no tool is hardcoded."
    )
)
def select_runtime_tools(
    query: Annotated[str, Field(description="Natural-language user query")],
    tools: Annotated[list[dict], Field(description="Discovered runtime tool metadata")],
) -> str:
    if not tools:
        return json.dumps({"tool_calls": [], "reason": "No runtime tools discovered."})

    tool_summary = json.dumps(tools, indent=2)
    prompt = f"""
You choose tools for a workflow. Use only tools from the discovered list.

Return ONLY JSON:
{{
  "tool_calls": [
    {{"tool_name": "name from list", "tool_input": {{"arg": "value"}}, "reason": "why"}}
  ],
  "reason": "brief selection rationale"
}}

Tool Selection Rules:
- For research papers, academic content, scientific queries, or scholarly information: use search_arxiv if available
- For web content, current events, news, general facts, or recent information: use web_search or advanced_web_search
- Prefer tools whose required parameters can be filled from the user query
- For a parameter named 'query', pass the user's query text
- Select at most 3 tools
- If no tool applies, return an empty tool_calls list

Examples:
- "latest research on quantum computing" → search_arxiv
- "papers about machine learning" → search_arxiv  
- "who is the CEO of OpenAI" → web_search or advanced_web_search
- "current weather in London" → web_search or advanced_web_search
- "explain transformer architecture papers" → search_arxiv

User query:
{query}

Discovered tools:
{tool_summary}
"""

    try:
        raw = ask_ollama(prompt, _DEFAULT_MODEL)
        parsed = extract_json(raw)
        calls = parsed.get("tool_calls", [])
        discovered_names = {tool.get("name") for tool in tools}
        valid_calls = [
            call
            for call in calls
            if call.get("tool_name") in discovered_names
            and isinstance(call.get("tool_input"), dict)
        ]
        return json.dumps(
            {
                "tool_calls": valid_calls[:3],
                "reason": str(parsed.get("reason", "")),
                "raw": raw,
            }
        )
    except Exception as e:
        fallback_calls = []
        for tool_info in tools:
            required = set(tool_info.get("required") or [])
            param_names = {
                param.get("name") for param in tool_info.get("parameters", [])
            }
            if "query" in param_names and required.issubset({"query"}):
                fallback_calls.append(
                    {
                        "tool_name": tool_info["name"],
                        "tool_input": {"query": query},
                        "reason": "Fallback selected a discovered query-compatible tool.",
                    }
                )

        return json.dumps(
            {
                "tool_calls": fallback_calls[:3],
                "reason": f"LLM tool selection unavailable; used metadata fallback: {e}",
            }
        )


@mcp.tool(
    description=(
        "Synthesize a final answer from runtime tool results. For research tools, "
        "provides a brief summary. Returns a concise, factual answer."
    )
)
def synthesize_tool_results(
    query: Annotated[str, Field(description="Natural-language user query")],
    tool_results: Annotated[list[dict], Field(description="Tool result records")],
) -> str:
    logger.info(
        "Starting synthesize_tool_results query=%r tool_result_count=%s",
        query,
        len(tool_results or []),
    )
    
    successful = [
        result
        for result in tool_results
        if result.get("status") == "succeeded" and str(result.get("output", "")).strip()
    ]

    if not successful:
        return (
            "I could not find enough tool output to answer this request. "
            "Check the failed tool activity details in Temporal UI."
        )

    # Categorize tools
    research_tools = []
    other_tools = []
    
    for result in successful:
        tool_name = result.get("tool_name", "")
        research_keywords = ["arxiv", "research", "paper", "scholar", "academic", "publication"]
        
        if any(keyword in tool_name.lower() for keyword in research_keywords):
            research_tools.append(result)
        else:
            other_tools.append(result)

    # Build synthesis prompt
    tool_summaries = []
    
    # Add research tool summaries (just counts)
    for result in research_tools:
        tool_name = result.get("tool_name", "unknown_tool")
        output = str(result.get("output", "")).strip()
        tool_summaries.append(f"Tool: {tool_name}\n{output}")
    
    # Add other tool outputs
    for result in other_tools:
        tool_name = result.get("tool_name", "unknown_tool")
        output = str(result.get("output", "")).strip()
        
        # Limit each tool output for synthesis
        if len(output) > 1500:
            output = output[:1500] + "\n[Output truncated]"
        
        tool_summaries.append(f"Tool: {tool_name}\nOutput:\n{output}")

    prompt = f"""
You are a factual QA assistant. Provide a clear, concise answer to the user's query.

RULES:
1. Answer ONLY using the tool outputs provided below
2. Be direct and factual
3. For research tools: Just mention how many papers were found
4. For other tools: Summarize the key findings
5. Keep the answer under 100 words for research queries
6. Do NOT list all paper details - that's in the structured response
7. Do NOT add speculation or external knowledge

User query:
{query}

Tool outputs:
{chr(10).join(tool_summaries)}

Your brief answer:
"""

    try:
        answer = ask_ollama(prompt, _DEFAULT_MODEL)
        logger.info("Completed synthesize_tool_results answer_chars=%s", len(answer))
        return answer.strip()
    except Exception:
        logger.exception("synthesize_tool_results LLM failed")
        raise RuntimeError("Synthesis LLM call failed - fallback will be used")


@mcp.tool(
    description=(
        "Generation/synthesis step. If 'search_results' is provided, the agent "
        "answers strictly from those results (grounded synthesis); otherwise it "
        "answers directly from the model."
    )
)
def agent_generate(
    agent_name: Annotated[str, Field(description="The name of the agent")],
    prompt: Annotated[str, Field(description="The user prompt")],
    search_results: Annotated[
        str | None,
        Field(description="Optional tool/search output to ground the answer in"),
    ] = None,
) -> str:
    spec = _SPECS_BY_NAME.get(agent_name)
    model = spec.model if spec and spec.model else _DEFAULT_MODEL

    try:
        if search_results:
            # Grounded synthesis: answer strictly from the tool output.
            grounded_prompt = build_search_answer_prompt(prompt, str(search_results))
            output = ask_ollama(grounded_prompt, model)
        else:
            # Direct generation using the agent's own declared model.
            output = ask_ollama(prompt, model)
        return output
    except Exception as e:
        return f"Error generating answer: {str(e)}"


# ======================================================================
# HTTP / SSE app (FastMCP supplies /sse + /messages/; we add the dashboard)
# ======================================================================

_DASHBOARD_HTML = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>AWCP MCP Control Plane</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 40px auto; padding: 0 20px; background: #F2EDE1; color: #0F0E0C; }
            h1 { color: #7A2E1E; border-bottom: 2px solid #7A2E1E; padding-bottom: 10px; }
            .card { background: #EDE5D3; border: 1px solid #1A1814; padding: 20px; border-radius: 4px; margin: 20px 0; }
            code { background: #1B1A18; color: #D4D0C8; padding: 2px 6px; border-radius: 4px; font-family: monospace; }
            .tag { display: inline-block; background: rgba(122,46,30,0.1); color: #7A2E1E; padding: 4px 12px; border-radius: 100px; font-size: 0.8em; font-weight: bold; text-transform: uppercase; }
            ul { padding-left: 20px; }
            li { margin-bottom: 10px; }
        </style>
    </head>
    <body>
        <h1>AWCP MCP Control Plane <span class="tag">Active</span></h1>
        <p>This is the <strong>Model Context Protocol (MCP)</strong> server for the Agent Workforce Control Plane.</p>

        <div class="card">
            <h3>🚀 How to use this server</h3>
            <p>This server is designed to be consumed by MCP clients, not browsers. To interact with the agent registry and models, use one of the following methods:</p>
            <ul>
                <li><strong>MCP Inspector:</strong> Run <code>PYTHONPATH=src npx @modelcontextprotocol/inspector ./.venv/bin/python -m awcp.mcp.server stdio</code></li>
                <li><strong>Claude Desktop:</strong> Add this server to your <code>claude_desktop_config.json</code> using the SSE transport.</li>
            </ul>
        </div>

        <div class="card">
            <h3>🛠 Available Control Tools</h3>
            <ul>
                <li><code>list_agents</code> - Query the AWCP Model Registry</li>
                <li><code>get_agent_info</code> - Inspect agent manifests and write scopes</li>
                <li><code>invoke_agent</code> - Directly trigger a model response</li>
                <li><code>agent_route / execute_tool / agent_generate</code> - Decomposed governed loop</li>
                <li><code>read_file / write_file / run_command</code> - Workspace management</li>
            </ul>
        </div>

        <p><small>Endpoints: <code>/sse</code> (Transport) | <code>/messages/</code> (Protocol)</small></p>
    </body>
    </html>
    """


async def get_index(request):
    return HTMLResponse(_DASHBOARD_HTML)


# FastMCP builds the Starlette SSE app (routes: GET /sse, POST /messages/).
# We attach the human-facing dashboard at GET / on the same app. This is the
# object uvicorn serves: `uvicorn awcp.mcp.server:app`.
app = mcp.sse_app()
app.router.routes.append(Route("/", endpoint=get_index, methods=["GET"]))


if __name__ == "__main__":
    # `python -m awcp.mcp.server stdio` -> stdio transport (for Temporal/MCP clients)
    # `python -m awcp.mcp.server`       -> SSE app + dashboard on :8002
    if len(sys.argv) > 1 and sys.argv[1] == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8002)
