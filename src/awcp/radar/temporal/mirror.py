"""Mirror each Temporal activity execution into the canonical Postgres.

Operator request: keep Temporal's own (dev-mode SQLite) workflow history AND
additionally record every workflow + activity step into the awcp Postgres, so a
run survives a restart and is queryable in Adminer (ops.workflow_events).

This runs INSIDE the activity (the right place for a non-deterministic side
effect — workflow code stays untouched and deterministic). It reads the ambient
``activity.info()`` for the workflow/activity identity, so each activity only has
to hand over its input and its returned summary. Best-effort and fail-open: any
error here is swallowed so the mirror can never break a workflow.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity

from awcp.radar import db


def mirror_activity(input_obj: Any, output: Any, status: str = "completed") -> None:
    """Record the currently-running activity to ops.workflow_events.

    ``input_obj`` is the activity's argument: a dict (execution events / setup
    params) whose agent_id/task_id are pulled out for the typed columns, or a bare
    string (onboarding activities take the agent_id directly)."""
    try:
        info = activity.info()
    except Exception:  # noqa: BLE001 — not in an activity context; nothing to mirror
        return

    agent_id = ""
    task_id = ""
    if isinstance(input_obj, dict):
        agent_id = str(input_obj.get("agent_id", "") or "")
        task_id = str(input_obj.get("task_id", "") or "")
        payload: Any = input_obj
    else:
        agent_id = str(input_obj or "")
        payload = {"value": input_obj}

    db.record_workflow_event(
        workflow_id=getattr(info, "workflow_id", "") or "",
        run_id=getattr(info, "workflow_run_id", None),
        workflow_type=getattr(info, "workflow_type", None),
        activity_type=getattr(info, "activity_type", None) or "unknown",
        agent_id=agent_id or None,
        task_id=task_id or None,
        attempt=getattr(info, "attempt", None),
        input_obj=payload,
        output=output,
        status=status,
    )
