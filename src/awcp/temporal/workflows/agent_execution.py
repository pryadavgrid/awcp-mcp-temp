from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from awcp.temporal.activities.mcp_gateway import (
        mcp_get_agent_info,
        mcp_agent_route,
        mcp_execute_tool,
        mcp_agent_generate,
    )
    from awcp.temporal.workflows.base_workflow import (
        FAST_INTERNAL_RETRY,
        AGENT_EXECUTION_RETRY,
    )


@workflow.defn
class AgentGovernanceWorkflow:
    """Orchestrates the agent loop step-by-step over the MCP server.

    admission -> reason -> [policy gate] -> tool -> generate.
    Each stage is a separate Temporal activity carrying the OTel trace context
    so all activities appear as children of the originating HTTP span in Tempo.
    """

    @workflow.run
    async def run(self, workflow_input: dict) -> dict:
        agent_name = workflow_input["agent_name"]
        user_input = workflow_input["input"]
        _otel_ctx = workflow_input.get("_otel_ctx", {})

        # STEP 1: Admission Control
        registry_entry = await workflow.execute_activity(
            mcp_get_agent_info,
            {"agent_name": agent_name, "_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=FAST_INTERNAL_RETRY,
        )

        # STEP 2: Quarantine Gate
        if registry_entry.get("status") == "quarantined":
            return {
                "system_action": "BLOCKED",
                "reason": f"Agent {agent_name} is in QUARANTINE. Execution denied by Control Plane.",
                "agent_details": registry_entry,
            }

        # STEP 3: Reasoning — SEARCH or ANSWER
        decision = await workflow.execute_activity(
            mcp_agent_route,
            {"agent_name": agent_name, "input": user_input, "_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=FAST_INTERNAL_RETRY,
        )

        # STEP 4: Execution with graceful degradation
        try:
            result = await self._execute_branch(
                agent_name, user_input, decision, _otel_ctx, autonomy_profile="active"
            )
            return {"system_action": "SUCCESS", "result": result}

        except ActivityError as e:
            workflow.logger.warn(
                f"Agent failed in active mode. Degrading to recommendation_only. Error: {str(e)}"
            )
            try:
                degraded = await self._execute_branch(
                    agent_name, user_input, decision, _otel_ctx,
                    autonomy_profile="recommendation_only",
                )
                return {"system_action": "DEGRADED_SUCCESS", "result": degraded}
            except ActivityError as final_error:
                return {
                    "system_action": "FATAL_FAILURE",
                    "reason": "Agent failed even in recommendation_only mode.",
                    "error": str(final_error),
                }

    async def _execute_branch(
        self,
        agent_name: str,
        user_input: str,
        decision: dict,
        _otel_ctx: dict,
        autonomy_profile: str,
    ) -> dict:
        action = decision.get("action", "ANSWER")
        tool_used = False
        search_results = None

        if action == "SEARCH" and autonomy_profile == "active":
            search_query = decision.get("search_query", user_input)
            tool_name = decision.get("tool_name", "web_search")
            tool_input = decision.get("tool_input") or {"query": search_query}
            search_results = await workflow.execute_activity(
                mcp_execute_tool,
                {"tool_name": tool_name, "tool_input": tool_input, "_otel_ctx": _otel_ctx},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=AGENT_EXECUTION_RETRY,
            )
            tool_used = True

        answer = await workflow.execute_activity(
            mcp_agent_generate,
            {
                "agent_name": agent_name,
                "input": user_input,
                "search_results": search_results,
                "_otel_ctx": _otel_ctx,
            },
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=AGENT_EXECUTION_RETRY,
        )

        return {
            "input": user_input,
            "output": answer,
            "agent": agent_name,
            "autonomy_profile": autonomy_profile,
            "action": action,
            "tool_used": tool_used,
            "tool_name": decision.get("tool_name", "web_search") if tool_used else None,
            "search_query": decision.get("search_query") if tool_used else None,
        }
