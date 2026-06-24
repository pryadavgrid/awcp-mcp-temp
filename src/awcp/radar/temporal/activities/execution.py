"""Temporal activities for agent task execution.

Each activity corresponds to one logical step in an agent's execution of a prompt.
The workflow calls these in the order the agent actually runs the steps — they are
NOT hardcoded sequences; the workflow dispatches to them dynamically based on
events forwarded from the agent process via the radar API.

All activities are async-safe and idempotent (safe to retry).
"""

from __future__ import annotations

from temporalio import activity

from awcp.radar.temporal.mirror import mirror_activity


@activity.defn
async def execution_setup(params: dict) -> str:
    """Task accepted: agent identified, goal received."""
    result = (
        f"agent={params.get('agent_id', '?')}"
        f" framework={params.get('framework', '?')}"
        f" goal={params.get('goal', '')[:80]}"
    )
    mirror_activity(params, result)
    return result


@activity.defn
async def execution_llm_call(event: dict) -> str:
    """Agent invoked the LLM (sent messages, awaiting a response)."""
    result = (
        f"model={event.get('model', 'unknown')}"
        f" call_n={event.get('call_n', 1)}"
        f" http_status={event.get('http_status', 200)}"
    )
    mirror_activity(event, result)
    return result


@activity.defn
async def execution_web_search(event: dict) -> str:
    """Agent performed a web/API search to gather information."""
    tool = event.get("tool_name", "web_search")
    query = event.get("query", "")
    result = f"tool={tool}" + (f" query={query[:80]}" if query else "")
    mirror_activity(event, result)
    return result


@activity.defn
async def execution_tool_call(event: dict) -> str:
    """Agent invoked a tool (compute, write, or external action)."""
    result = (
        f"tool={event.get('tool_name', '?')}"
        f" risk={event.get('risk', 'low')}"
        f" status={event.get('gate', 'allowed')}"
    )
    mirror_activity(event, result)
    return result


@activity.defn
async def execution_synthesize_answer(event: dict) -> str:
    """Agent synthesized the final answer from gathered context."""
    tools = event.get("tools_used", [])
    result = (
        f"result_len={event.get('result_len', 0)}"
        f" tools_used={','.join(tools) if tools else 'none'}"
    )
    mirror_activity(event, result)
    return result


@activity.defn
async def execution_complete(outcome: dict) -> str:
    """Task execution complete."""
    status = outcome.get("status", "done")
    error = outcome.get("error", "")
    result_len = len(outcome.get("result", ""))
    if status == "failed":
        result = f"status=failed error={error[:120]}"
    else:
        result = f"status={status} result_len={result_len}"
    mirror_activity(outcome, result, status=("failed" if status == "failed" else "completed"))
    return result
