"""HookManager — owns the hook registry and dispatches lifecycle events to hooks.

Design goals:

  * **Safe** — a hook that raises, hangs logically, or returns garbage can never
    break the caller. Every hook call is wrapped; failures are counted and logged,
    then skipped.
  * **Ordered** — hooks fire in ``priority`` order (low first) so a GUARD can veto
    before observers run.
  * **Veto-correct** — a ``deny`` is only honoured at a ``GUARD_POINT`` and can
    only *tighten* policy: the manager returns deny if ANY guard denies, but it
    never turns a caller's existing deny into an allow.
  * **Observable** — per-hook call/error counts and a ring buffer of recent
    dispatches power the ``/hooks`` and ``/hooks/recent`` endpoints.

There is one process-wide manager (``get_manager()``); the public functions in
``awcp.agent_hooks.__init__`` are thin wrappers over it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Iterable

from awcp.agent_hooks.base import Hook, _FnHook
from awcp.agent_hooks.types import (
    Decision,
    GUARD_POINTS,
    HookCategory,
    HookContext,
    HookOutcome,
    HookType,
)

log = logging.getLogger("awcp.agent_hooks")


class HookManager:
    def __init__(self, recent_max: int = 200) -> None:
        # hook_type -> list[Hook], kept sorted by priority
        self._by_type: dict[HookType, list[Hook]] = {}
        # name -> Hook (for unregister / toggle / stats)
        self._by_name: dict[str, Hook] = {}
        self._stats: dict[str, dict[str, int]] = {}
        self._recent: deque[dict] = deque(maxlen=recent_max)
        self._lock = threading.RLock()
        self._enabled = True

    # ── registration ───────────────────────────────────────────────────────
    def register(self, hook: Hook) -> Hook:
        """Add a hook. Re-registering the same ``name`` replaces the old one
        (so an agent reload doesn't stack duplicates)."""
        with self._lock:
            if hook.name in self._by_name:
                self.unregister(hook.name)
            self._by_name[hook.name] = hook
            self._stats.setdefault(hook.name, {"calls": 0, "errors": 0, "denies": 0})
            for t in hook.types():
                bucket = self._by_type.setdefault(t, [])
                bucket.append(hook)
                bucket.sort(key=lambda h: h.priority)
            log.info("hook.registered name=%s category=%s types=%s priority=%d",
                     hook.name, hook.category.value,
                     [t.value for t in hook.types()], hook.priority)
        return hook

    def register_fn(self, fn, *, types: Iterable[HookType], name: str | None = None,
                    category: HookCategory = HookCategory.OBSERVER,
                    priority: int = 100) -> Hook:
        return self.register(_FnHook(fn, types=types, name=name,
                                     category=category, priority=priority))

    def unregister(self, name: str) -> bool:
        with self._lock:
            hook = self._by_name.pop(name, None)
            if not hook:
                return False
            for t in hook.types():
                bucket = self._by_type.get(t)
                if bucket:
                    self._by_type[t] = [h for h in bucket if h.name != name]
            log.info("hook.unregistered name=%s", name)
            return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            hook = self._by_name.get(name)
            if not hook:
                return False
            hook.enabled = enabled
            return True

    def get(self, name: str) -> Hook | None:
        return self._by_name.get(name)

    # ── dispatch ────────────────────────────────────────────────────────────
    def dispatch(self, hook_type: HookType, ctx: HookContext | None = None,
                 **data) -> HookOutcome:
        """Fire all hooks subscribed to ``hook_type``.

        Pass either a ready ``HookContext`` or keyword ``data`` (a context is
        built for you). Returns the aggregate ``HookOutcome``: ``deny`` iff a
        guard hook denied at a guard point, else ``allow``. Always cheap and safe
        — returns ``allow`` immediately when disabled or nothing is subscribed.
        """
        if not self._enabled:
            return HookOutcome.allow()
        hooks = self._by_type.get(hook_type)
        if not hooks:
            return HookOutcome.allow()

        if ctx is None:
            ctx = HookContext(
                hook_type=hook_type,
                agent_id=str(data.pop("agent_id", "")),
                task_id=data.pop("task_id", None),
                data=data,
            )

        is_guard_point = hook_type in GUARD_POINTS
        final = HookOutcome.allow()
        fired: list[dict] = []

        # snapshot under lock, run hooks outside it (a hook must not deadlock us)
        with self._lock:
            ordered = list(hooks)

        for hook in ordered:
            if not hook.enabled:
                continue
            t0 = time.monotonic()
            try:
                outcome = hook.handle(ctx)
            except Exception as exc:  # noqa: BLE001 — a hook must never break the caller
                self._bump(hook.name, "errors")
                log.warning("hook.error name=%s type=%s error=%r",
                            hook.name, hook_type.value, exc)
                fired.append({"hook": hook.name, "error": repr(exc)[:200]})
                continue
            dur_ms = (time.monotonic() - t0) * 1000
            self._bump(hook.name, "calls")
            if outcome is None:
                outcome = HookOutcome.allow()
            entry = {"hook": hook.name, "decision": outcome.decision.value,
                     "dur_ms": round(dur_ms, 2)}
            if outcome.note:
                entry["note"] = outcome.note[:200]
            if outcome.is_deny:
                entry["reason"] = outcome.reason[:200]
            fired.append(entry)
            # Honour a veto only at a guard point; tighten-only.
            if outcome.is_deny and is_guard_point and not final.is_deny:
                final = HookOutcome.deny(f"{hook.name}: {outcome.reason}")
                self._bump(hook.name, "denies")

        self._recent.appendleft({
            "ts": time.time(),
            "type": hook_type.value,
            "agent_id": ctx.agent_id,
            "task_id": ctx.task_id,
            "decision": final.decision.value,
            "guard_point": is_guard_point,
            "hooks": fired,
        })
        return final

    # ── stats / introspection ───────────────────────────────────────────────
    def _bump(self, name: str, key: str) -> None:
        s = self._stats.setdefault(name, {"calls": 0, "errors": 0, "denies": 0})
        s[key] = s.get(key, 0) + 1

    def list_hooks(self) -> list[dict]:
        with self._lock:
            return [{
                "name": h.name,
                "category": h.category.value,
                "enabled": h.enabled,
                "priority": h.priority,
                "subscriptions": [t.value for t in h.types()],
                "stats": dict(self._stats.get(h.name, {})),
            } for h in sorted(self._by_name.values(), key=lambda h: h.priority)]

    def recent(self, limit: int = 50) -> list[dict]:
        return list(self._recent)[: max(1, min(limit, self._recent.maxlen or 200))]

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self._enabled,
                "hook_count": len(self._by_name),
                "subscriptions": {t.value: len(v) for t, v in self._by_type.items()},
            }


_MANAGER: HookManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> HookManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = HookManager()
    return _MANAGER


# ── runtime policy-guard control (so the UI can enable/test it, no restart) ──
def configure_guard(deny_tools, enabled: bool = True) -> dict:
    """Register/replace the PolicyGuardHook at runtime with the given deny-list,
    or unregister it when ``enabled`` is false. Returns the resulting config.
    This is what lets the dashboard turn the guard on without a gateway restart."""
    from awcp.agent_hooks.builtin.policy_hook import PolicyGuardHook
    mgr = get_manager()
    if not enabled:
        mgr.unregister("policy-guard")
        return {"enabled": False, "deny_tools": []}
    mgr.register(PolicyGuardHook(list(deny_tools or [])))  # register replaces by name
    h = mgr.get("policy-guard")
    return {"enabled": True, "deny_tools": sorted(getattr(h, "deny_tools", set()))}


def guard_config() -> dict:
    """Current policy-guard state (whether it's loaded + its deny-list)."""
    h = get_manager().get("policy-guard")
    if not h:
        return {"enabled": False, "deny_tools": []}
    return {"enabled": h.enabled, "deny_tools": sorted(getattr(h, "deny_tools", set()))}


def guard_test(agent_id: str, action: str) -> dict:
    """Deterministically exercise the guard: dispatch a GATE_EVALUATED (as the
    radar gate would, starting from an ``allow``) and report whether the guard
    vetoed it. Also fires ACTION_BLOCKED on a deny so the recent-events feed shows
    the same pair the real gate produces. Independent of any agent's budget/state,
    so it always reflects the guard alone."""
    mgr = get_manager()
    out = mgr.dispatch(HookType.GATE_EVALUATED, agent_id=agent_id, action=action,
                       scope=action, write=True, decision="allow", mode="policy")
    if out.is_deny:
        mgr.dispatch(HookType.ACTION_BLOCKED, agent_id=agent_id, action=action,
                     reason=out.reason, mode="hook_guard")
    return {"agent_id": agent_id, "action": action,
            "decision": out.decision.value,
            "mode": "hook_guard" if out.is_deny else "policy",
            "reason": out.reason}
