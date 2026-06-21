import asyncio
import os
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from awcp.temporal.activities.mcp_gateway import (
        mcp_get_agent_info,
        mcp_agent_route,
        mcp_execute_tool,
        mcp_agent_generate,
        policy_gate,
    )
    from awcp.temporal.workflows.base_workflow import (
        FAST_INTERNAL_RETRY,
        AGENT_EXECUTION_RETRY,
    )

# How long the workflow durably pauses waiting for an operator approval signal
# before treating the high-risk write as denied. Env-driven (nothing hardcoded);
# the magazine allows a risky action to "wait without losing the workflow state".
APPROVAL_WAIT_SECONDS = float(os.getenv("AWCP_APPROVAL_WAIT_SECONDS", "900"))


@workflow.defn
class AgentGovernanceWorkflow:
    """Orchestrates the agent loop step-by-step over the MCP server.

    admission -> reason -> policy gate -> tool -> generate.
    Each stage is a separate Temporal activity carrying the OTel trace context
    so all activities appear as children of the originating HTTP span in Tempo.

    Step 03 (policy gate): before any state-changing tool runs, the workflow asks
    the radar's write-action gate. allow -> run the tool; deny -> return blocked;
    requires_approval -> pause DURABLY until an operator sends the `submit_approval`
    signal with a token (or the wait times out), then re-gate carrying the token.
    """

    def __init__(self) -> None:
        # An operator-issued approval token, delivered via signal while the
        # workflow is durably paused on a high-risk write.
        self._approval_token: str = ""

    @workflow.signal
    def submit_approval(self, token: str) -> None:
        """Operator approval: hand the paused workflow a token that unblocks the
        high-risk write it is waiting on."""
        self._approval_token = token or ""

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

        # The radar registry id (parsed from the manifest) drives the policy gate.
        agent_id = registry_entry.get("id", "")

        # STEP 3: Reasoning — SEARCH or ANSWER
        decision = await workflow.execute_activity(
            mcp_agent_route,
            {"agent_name": agent_name, "input": user_input, "_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=FAST_INTERNAL_RETRY,
        )

        # STEP 4: Execution with graceful degradation
        try:
            result = await self._dispatch(
                agent_name, agent_id, user_input, decision, _otel_ctx,
                autonomy_profile="active",
            )
            return {"system_action": "SUCCESS", "result": result}

        except ActivityError as e:
            workflow.logger.warn(
                f"Agent failed in active mode. Degrading to recommendation_only. Error: {str(e)}"
            )
            try:
                degraded = await self._dispatch(
                    agent_name, agent_id, user_input, decision, _otel_ctx,
                    autonomy_profile="recommendation_only",
                )
                return {"system_action": "DEGRADED_SUCCESS", "result": degraded}
            except ActivityError as final_error:
                return {
                    "system_action": "FATAL_FAILURE",
                    "reason": "Agent failed even in recommendation_only mode.",
                    "error": str(final_error),
                }

    async def _dispatch(
        self,
        agent_name: str,
        agent_id: str,
        user_input: str,
        decision: dict,
        _otel_ctx: dict,
        autonomy_profile: str,
    ) -> dict:
        """Run one agent's branch — or, if it chose to hand off, run the sub-agent."""
        if decision.get("action") == "DELEGATE":
            return await self._delegate(agent_name, user_input, decision, _otel_ctx, autonomy_profile)
        return await self._execute_branch(
            agent_name, agent_id, user_input, decision, _otel_ctx, autonomy_profile
        )

    async def _delegate(
        self,
        coordinator: str,
        user_input: str,
        decision: dict,
        _otel_ctx: dict,
        autonomy_profile: str,
    ) -> dict:
        """Single-level handoff: the coordinator hands the task to ONE sub-agent,
        which runs its OWN governed loop (admission -> route -> gate -> tool -> generate).
        Every sub-agent step is its own recorded activity under the same autonomy
        gate. The sub-agent cannot delegate again (its branch only does SEARCH/ANSWER)."""
        sub = decision.get("agent")

        sub_entry = await workflow.execute_activity(
            mcp_get_agent_info,
            {"agent_name": sub, "_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=FAST_INTERNAL_RETRY,
        )
        if not sub or sub_entry.get("status") == "quarantined":
            fallback = await self._execute_branch(
                coordinator, "", user_input, {"action": "ANSWER"}, _otel_ctx, autonomy_profile
            )
            fallback["delegation"] = {
                "coordinator": coordinator,
                "sub_agent": sub,
                "status": "refused (quarantined)" if sub else "refused (no target)",
            }
            return fallback

        sub_id = sub_entry.get("id", "")
        sub_decision = await workflow.execute_activity(
            mcp_agent_route,
            {"agent_name": sub, "input": user_input, "_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=FAST_INTERNAL_RETRY,
        )
        sub_result = await self._execute_branch(
            sub, sub_id, user_input, sub_decision, _otel_ctx, autonomy_profile
        )
        sub_result["delegation"] = {
            "coordinator": coordinator,
            "sub_agent": sub,
            "status": "completed",
        }
        return sub_result

    async def _policy_gate(
        self, agent_id: str, tool_name: str, _otel_ctx: dict
    ) -> dict:
        """Ask the radar gate, and if it requires approval, pause DURABLY until an
        operator sends a token via the `submit_approval` signal (bounded by
        APPROVAL_WAIT_SECONDS), then re-gate carrying the token."""
        gate = await workflow.execute_activity(
            policy_gate,
            {
                "agent_id": agent_id, "tool_name": tool_name, "action": tool_name,
                "write": True, "workflow_id": workflow.info().workflow_id,
                "approval_token": self._approval_token, "_otel_ctx": _otel_ctx,
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=FAST_INTERNAL_RETRY,
        )
        if not gate.get("requires_approval"):
            return gate

        workflow.logger.info(
            f"policy gate requires approval for tool={tool_name}; pausing up to "
            f"{APPROVAL_WAIT_SECONDS:.0f}s for an operator token."
        )
        try:
            await workflow.wait_condition(
                lambda: bool(self._approval_token),
                timeout=timedelta(seconds=APPROVAL_WAIT_SECONDS),
            )
        except asyncio.TimeoutError:
            return {
                "decision": "deny", "mode": "approval_timeout",
                "reason": "no operator approval within the wait window",
                "requires_approval": True,
            }
        # Re-gate carrying the operator's token (single-use, validated by the radar).
        return await workflow.execute_activity(
            policy_gate,
            {
                "agent_id": agent_id, "tool_name": tool_name, "action": tool_name,
                "write": True, "workflow_id": workflow.info().workflow_id,
                "approval_token": self._approval_token, "_otel_ctx": _otel_ctx,
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=FAST_INTERNAL_RETRY,
        )

    async def _execute_branch(
        self,
        agent_name: str,
        agent_id: str,
        user_input: str,
        decision: dict,
        _otel_ctx: dict,
        autonomy_profile: str,
    ) -> dict:
        action = decision.get("action", "ANSWER")
        tool_used = False
        search_results = None
        gate_result: dict | None = None

        if action == "SEARCH" and autonomy_profile == "active":
            search_query = decision.get("search_query", user_input)
            tool_name = decision.get("tool_name", "web_search")
            tool_input = decision.get("tool_input") or {"query": search_query}

            # STEP 03: policy gate BEFORE the state-changing tool runs.
            gate_result = await self._policy_gate(agent_id, tool_name, _otel_ctx)
            if gate_result.get("decision") == "deny":
                return {
                    "input": user_input,
                    "output": f"BLOCKED by policy gate: {gate_result.get('reason', '')}",
                    "agent": agent_name,
                    "autonomy_profile": autonomy_profile,
                    "action": action,
                    "tool_used": False,
                    "tool_name": tool_name,
                    "blocked": True,
                    "gate": gate_result,
                }

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
            "gate": gate_result,
        }
