"""PolicyGuardHook — the reference GUARD hook (off by default).

Most hooks only watch. This one can *veto*. It fires at ``GATE_EVALUATED`` (a
GUARD_POINT) and returns ``deny`` when the action targets a tool on a configured
deny-list — letting an operator block specific capabilities (e.g. ``external_post``)
fleet-wide with one env var, on top of the radar's own policy.

Two safety properties make this safe to ship:

  * **Tighten-only** — the manager honours a guard ``deny`` but never lets a hook
    turn the radar's ``deny`` into an ``allow``. A guard can only *add* restriction.
  * **Fail-open on error** — if the hook raises, the manager skips it (the action
    is not blocked by a broken guard). Guards that must fail *closed* should encode
    that in their own logic; this example chooses availability.

This is also the template for richer guards: rate-limit by agent, block writes
during a freeze window, require a tag on high-risk scopes, etc.
"""

from __future__ import annotations

import logging

from awcp.agent_hooks.base import Hook
from awcp.agent_hooks.types import HookCategory, HookContext, HookOutcome, HookType

log = logging.getLogger("awcp.agent_hooks.policy")


class PolicyGuardHook(Hook):
    name = "policy-guard"
    category = HookCategory.GUARD
    subscriptions = (HookType.GATE_EVALUATED,)
    priority = 5  # run before observers so a veto is recorded first

    def __init__(self, deny_tools: list[str] | None = None) -> None:
        # normalise to a lowercase set for matching
        self.deny_tools = {t.lower() for t in (deny_tools or [])}

    def handle(self, ctx: HookContext) -> HookOutcome:
        # The gate context carries the action and the scope (tool name) the agent
        # is asking to perform.
        target = (ctx.action or ctx.get("scope", "") or ctx.tool_name).lower()
        if target and target in self.deny_tools:
            log.warning("policy.veto agent_id=%s action=%s (deny-list)", ctx.agent_id, target)
            return HookOutcome.deny(f"action '{target}' blocked by policy-guard deny-list")
        return HookOutcome.allow()
