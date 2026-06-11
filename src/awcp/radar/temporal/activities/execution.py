"""Temporal activities for agent task execution.

Each activity corresponds to one logical step in an agent's execution of a prompt.
The workflow calls these in the order the agent actually runs the steps — they are
NOT hardcoded sequences; the workflow dispatches to them dynamically based on
events forwarded from the agent process via the radar API.

All activities are async-safe and idempotent (safe to retry).
"""

from __future__ import annotations

from temporalio import activity


@activity.defn
async def execution_setup(params: dict) -> str:
    """Task accepted: agent identified, goal received."""
    return (
        f"agent={params.get('agent_id', '?')}"
        f" framework={params.get('framework', '?')}"
        f" goal={params.get('goal', '')[:80]}"
    )


@activity.defn
async def execution_llm_call(event: dict) -> str:
    """Agent invoked the LLM (sent messages, awaiting a response)."""
    return (
        f"model={event.get('model', 'unknown')}"
        f" call_n={event.get('call_n', 1)}"
        f" http_status={event.get('http_status', 200)}"
    )


@activity.defn
async def execution_web_search(event: dict) -> str:
    """Agent performed a web/API search to gather information."""
    tool = event.get("tool_name", "web_search")
    query = event.get("query", "")
    return f"tool={tool}" + (f" query={query[:80]}" if query else "")


@activity.defn
async def execution_tool_call(event: dict) -> str:
    """Agent invoked a tool (compute, write, or external action)."""
    return (
        f"tool={event.get('tool_name', '?')}"
        f" risk={event.get('risk', 'low')}"
        f" status={event.get('gate', 'allowed')}"
    )


@activity.defn
async def execution_synthesize_answer(event: dict) -> str:
    """Agent synthesized the final answer from gathered context."""
    tools = event.get("tools_used", [])
    return (
        f"result_len={event.get('result_len', 0)}"
        f" tools_used={','.join(tools) if tools else 'none'}"
    )


@activity.defn
async def execution_complete(outcome: dict) -> str:
    """Task execution complete."""
    status = outcome.get("status", "done")
    error = outcome.get("error", "")
    result_len = len(outcome.get("result", ""))
    if status == "failed":
        return f"status=failed error={error[:120]}"
    return f"status={status} result_len={result_len}"
