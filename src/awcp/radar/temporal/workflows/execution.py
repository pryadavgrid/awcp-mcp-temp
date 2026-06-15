"""AgentExecutionWorkflow — one workflow per task prompt.

Architecture
============
The workflow starts when an agent accepts a task (POST /tasks/execution/start on the
radar). As the agent runs it emits events (llm_called, tool_called, web_search,
synthesize) via POST /tasks/execution/{id}/event; the radar converts them into
``push_event`` signals on this workflow. When the task finishes the agent sends
POST /tasks/execution/{id}/complete which triggers the ``finish`` signal.

The workflow dispatches to a DIFFERENT Temporal activity for each event type so
that every logical step of the agent appears as its own named activity in the
Temporal UI — dynamically, without any hardcoded call sequence. New event types
are automatically ignored (safe forward-compatibility).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from awcp.radar.temporal.activities.execution import (
        execution_setup,
        execution_llm_call,
        execution_web_search,
        execution_tool_call,
        execution_synthesize_answer,
        execution_complete,
    )

# Map event.type → activity function.
# Adding a new event type here is the ONLY change needed to surface a new step
# in the Temporal UI — the workflow loop is generic.
_EVENT_TO_ACTIVITY = {
    "llm_called":  execution_llm_call,
    "web_search":  execution_web_search,
    "tool_called": execution_tool_call,
    "synthesize":  execution_synthesize_answer,
}

_ACTIVITY_TIMEOUT = timedelta(seconds=30)
_TASK_TIMEOUT = timedelta(hours=1)


@workflow.defn
class AgentExecutionWorkflow:
    """Tracks a single agent task — surfaces each execution step as a Temporal activity."""

    def __init__(self) -> None:
        self._pending: list[dict] = []
        self._done: bool = False
        self._outcome: dict = {}

    @workflow.signal
    def push_event(self, event: dict) -> None:
        """Radar calls this for each execution step (llm_called, tool_called, …)."""
        self._pending.append(event)

    @workflow.signal
    def finish(self, outcome: dict) -> None:
        """Radar calls this when the task completes or fails."""
        self._outcome = outcome
        self._done = True

    @workflow.run
    async def run(self, params: dict) -> dict:
        TO = _ACTIVITY_TIMEOUT

        # ── Step 0: setup ────────────────────────────────────────────────
        await workflow.execute_activity(
            execution_setup, params, start_to_close_timeout=TO
        )

        # ── Dynamic steps: run one activity per inbound event ────────────
        while True:
            try:
                await workflow.wait_condition(
                    lambda: bool(self._pending) or self._done,
                    timeout=_TASK_TIMEOUT,
                )
            except Exception:
                # Timeout or unexpected error — wrap up
                break

            # Drain all queued events
            while self._pending:
                event = self._pending.pop(0)
                fn = _EVENT_TO_ACTIVITY.get(event.get("type", ""))
                if fn:
                    await workflow.execute_activity(fn, event, start_to_close_timeout=TO)

            if self._done:
                break

        # Drain any events that arrived with the finish signal
        while self._pending:
            event = self._pending.pop(0)
            fn = _EVENT_TO_ACTIVITY.get(event.get("type", ""))
            if fn:
                await workflow.execute_activity(fn, event, start_to_close_timeout=TO)

        # ── Final step: complete ─────────────────────────────────────────
        await workflow.execute_activity(
            execution_complete, self._outcome, start_to_close_timeout=TO
        )
        return self._outcome
