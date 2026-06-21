"""Hook types — the vocabulary of the agent-hooks system.

This module defines:

  * ``HookType``     — every lifecycle point an agent passes through that AWCP can
                       observe centrally (registration, per-task, per-step,
                       governance, and token events). A hook subscribes to one or
                       more of these.
  * ``HookCategory`` — the *behaviour* a hook may have at a point: an OBSERVER
                       hook only watches; a GUARD hook may veto (deny) the action.
  * ``HookContext``  — the immutable payload handed to every hook when it fires:
                       which point, which agent/task, a timestamp, and the
                       event-specific ``data``.
  * ``HookOutcome``  — what a hook returns. For OBSERVER points the return value
                       is ignored; for GUARD points a ``deny`` outcome can stop
                       the action (it can only *tighten* policy, never loosen it).

Nothing here imports the radar or any heavy dependency, so the file is safe to
import from anywhere (agents, tests, the control plane).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HookType(str, Enum):
    """Every agent-lifecycle point AWCP fires a hook on.

    Grouped by phase. The string value is what appears in logs / the ``/hooks``
    API, so keep them stable.
    """

    # ── Agent lifecycle ────────────────────────────────────────────────────
    AGENT_REGISTERED = "agent_registered"      # an agent self-registered / was admitted
    AGENT_DEREGISTERED = "agent_deregistered"  # an agent was removed from the registry

    # ── Task lifecycle (one task = one prompt the agent runs) ───────────────
    TASK_STARTED = "task_started"              # a governed execution workflow began
    TASK_COMPLETED = "task_completed"          # the task finished successfully
    TASK_FAILED = "task_failed"                # the task failed or was blocked

    # ── Per-step events (emitted by the agent as it works) ─────────────────
    STEP = "step"                              # generic: fires for EVERY execution step
    LLM_CALL = "llm_call"                       # the agent called its model
    TOOL_CALL = "tool_call"                     # the agent invoked a tool
    WEB_SEARCH = "web_search"                   # the agent ran a web search
    SYNTHESIZE = "synthesize"                   # the agent synthesised its final answer

    # ── Governance ─────────────────────────────────────────────────────────
    GATE_EVALUATED = "gate_evaluated"          # GUARD: the write-action gate ran
    ACTION_BLOCKED = "action_blocked"          # an action was denied (gate or hard-stop)
    APPROVAL_REQUIRED = "approval_required"    # a high-risk action needs a human
    SIGNAL_RECEIVED = "signal_received"        # an agent reported a task outcome
    AUTONOMY_DEGRADED = "autonomy_degraded"    # the agent was stepped down its ladder

    # ── Token economy (laminar) ────────────────────────────────────────────
    TOKEN_USAGE = "token_usage"                # real token usage was metered
    BUDGET_WARN = "budget_warn"                # the agent crossed its warn threshold
    BUDGET_EXHAUSTED = "budget_exhausted"      # the agent hit/exceeded its budget


# The set of points where a GUARD hook's ``deny`` is actually honoured. At every
# other point a returned ``deny`` is ignored (the event has already happened —
# there is nothing to veto). Keeping this explicit prevents a hook from thinking
# it blocked something it only observed.
GUARD_POINTS: frozenset[HookType] = frozenset({
    HookType.GATE_EVALUATED,
})


class HookCategory(str, Enum):
    """How a hook behaves. Purely informational (shown in ``/hooks``) — the
    manager enforces veto rules via ``GUARD_POINTS``, not via this label."""

    OBSERVER = "observer"   # watches only (logging, metrics, audit, notify)
    GUARD = "guard"         # may veto an action at a GUARD_POINT


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class HookContext:
    """The immutable payload passed to a hook when it fires.

    ``data`` carries event-specific fields (e.g. ``action`` + ``decision`` for a
    gate evaluation, ``tokens`` for token usage). Use the helper accessors for
    common fields so hooks don't all re-implement ``data.get(...)``.
    """

    hook_type: HookType
    agent_id: str = ""
    task_id: str | None = None
    ts: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    # ── convenience accessors (read-only views over ``data``) ──────────────
    @property
    def action(self) -> str:
        return str(self.data.get("action", ""))

    @property
    def risk(self) -> str:
        return str(self.data.get("risk", ""))

    @property
    def decision(self) -> str:
        return str(self.data.get("decision", ""))

    @property
    def tool_name(self) -> str:
        return str(self.data.get("tool_name", ""))

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class HookOutcome:
    """What a hook returns. Default = allow (do nothing).

    A GUARD hook fired at a GUARD_POINT can return ``HookOutcome.deny(reason)`` to
    veto the action. Anywhere else the decision is recorded but ignored.
    """

    decision: Decision = Decision.ALLOW
    reason: str = ""
    # Optional free-form annotations a hook wants to attach (surfaced in /hooks/recent).
    note: str = ""

    @classmethod
    def allow(cls, note: str = "") -> "HookOutcome":
        return cls(decision=Decision.ALLOW, note=note)

    @classmethod
    def deny(cls, reason: str) -> "HookOutcome":
        return cls(decision=Decision.DENY, reason=reason)

    @property
    def is_deny(self) -> bool:
        return self.decision == Decision.DENY
