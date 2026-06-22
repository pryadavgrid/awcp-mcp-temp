"""AWCP Agent Hooks — a central, extensible hook system for the agent fleet.

WHAT THIS IS
------------
A small framework that fires user-pluggable callbacks ("hooks") at every point in
an agent's life that AWCP can observe centrally: registration, each task, each
LLM/tool/web-search/synthesize step, every governance decision (gate, block,
approval, degradation) and every token event (usage, warn, exhausted).

WHY IT LIVES IN THE CONTROL PLANE
---------------------------------
The agents already report their entire lifecycle to the radar (register → start →
per-step events → signals → complete). So the radar is the one place that sees
*all* agents' *all* events — wiring hooks there makes them apply to every agent,
in every framework, with no change to the agent code. It is wired exactly like
``awcp.laminar``: optional, self-contained, and removable (delete this folder and
the radar runs unchanged).

PUBLIC API
----------
    import awcp.agent_hooks as hooks

    hooks.init_hooks()                 # load built-ins from config (radar calls this)
    hooks.dispatch(HookType.TOOL_CALL, agent_id=..., task_id=..., tool_name=...)
    hooks.register(MyHook())           # add a custom Hook subclass
    hooks.register_fn(fn, types=[...]) # add a bare function as an observer
    hooks.router                       # FastAPI APIRouter (/hooks, /hooks/recent)

See ``README.md`` in this folder for the full guide.
"""

from __future__ import annotations

import logging

from awcp.agent_hooks import config
from awcp.agent_hooks.api import router
from awcp.agent_hooks.base import Hook
from awcp.agent_hooks.manager import HookManager, get_manager
from awcp.agent_hooks.types import (
    Decision,
    GUARD_POINTS,
    HookCategory,
    HookContext,
    HookOutcome,
    HookType,
)

log = logging.getLogger("awcp.agent_hooks")

__all__ = [
    "init_hooks",
    "dispatch",
    "register",
    "register_fn",
    "unregister",
    "list_hooks",
    "recent",
    "get_manager",
    "router",
    "Hook",
    "HookManager",
    "HookType",
    "HookCategory",
    "HookContext",
    "HookOutcome",
    "Decision",
    "GUARD_POINTS",
]

_INITIALISED = False


def init_hooks() -> dict:
    """Load the built-in hooks selected in ``config`` into the process-wide
    manager. Idempotent — safe to call more than once (e.g. on a dev reload);
    re-registering a hook by name replaces it rather than stacking duplicates.

    Returns a small summary dict (which hooks loaded) for logging.
    """
    global _INITIALISED
    mgr = get_manager()

    if not config.ENABLED:
        mgr._enabled = False
        log.info("agent_hooks.disabled (AWCP_HOOKS_ENABLED=false)")
        return {"enabled": False, "loaded": []}

    mgr._enabled = True
    loaded: list[str] = []

    # Import lazily so a failure in one optional hook can't stop the others.
    if config.LOAD_LOGGING:
        from awcp.agent_hooks.builtin.logging_hook import LoggingHook
        mgr.register(LoggingHook()); loaded.append("logging")
    if config.LOAD_AUDIT:
        from awcp.agent_hooks.builtin.audit_hook import AuditHook
        mgr.register(AuditHook(config.AUDIT_PATH)); loaded.append("audit")
    if config.LOAD_METRICS:
        from awcp.agent_hooks.builtin.metrics_hook import MetricsHook
        mgr.register(MetricsHook()); loaded.append("metrics")
    if config.LOAD_TIMING:
        from awcp.agent_hooks.builtin.timing_hook import TimingHook
        mgr.register(TimingHook()); loaded.append("timing")
    if config.LOAD_NOTIFY:
        from awcp.agent_hooks.builtin.notify_hook import NotifyHook
        mgr.register(NotifyHook(config.NOTIFY_WEBHOOK)); loaded.append("notify")
    if config.LOAD_POLICY_GUARD:
        from awcp.agent_hooks.builtin.policy_hook import PolicyGuardHook
        mgr.register(PolicyGuardHook(config.DENY_TOOLS)); loaded.append("policy-guard")

    _INITIALISED = True
    log.info("agent_hooks.init loaded=%s audit_path=%s", loaded, config.AUDIT_PATH)
    return {"enabled": True, "loaded": loaded}


# ── thin module-level wrappers over the singleton manager ───────────────────

def dispatch(hook_type: HookType, ctx: HookContext | None = None, **data) -> HookOutcome:
    """Fire all hooks for ``hook_type``. Safe + cheap when nothing is registered.
    Returns the aggregate outcome (``deny`` only possible at a GUARD_POINT)."""
    return get_manager().dispatch(hook_type, ctx, **data)


def register(hook: Hook) -> Hook:
    return get_manager().register(hook)


def register_fn(fn, *, types, name=None, category=HookCategory.OBSERVER, priority=100):
    return get_manager().register_fn(fn, types=types, name=name,
                                     category=category, priority=priority)


def unregister(name: str) -> bool:
    return get_manager().unregister(name)


def list_hooks() -> list[dict]:
    return get_manager().list_hooks()


def recent(limit: int = 50) -> list[dict]:
    return get_manager().recent(limit)
