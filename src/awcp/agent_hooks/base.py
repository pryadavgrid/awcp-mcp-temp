"""The ``Hook`` base class — what every hook (built-in or custom) subclasses.

A hook is a small object that declares *which* lifecycle points it cares about
(``subscriptions``) and *what to do* when one fires (``handle``). The
``HookManager`` owns the registry of hooks and calls them; a hook never calls the
manager.

Two ways to write a hook:

  1. Subclass ``Hook`` (recommended for anything stateful — see ``builtin/``)::

         class MyHook(Hook):
             name = "my-hook"
             category = HookCategory.OBSERVER
             subscriptions = (HookType.TOOL_CALL,)
             def handle(self, ctx: HookContext) -> HookOutcome | None:
                 ...

  2. Register a bare function with ``agent_hooks.register_fn(...)`` (handy for
     one-off observers) — the manager wraps it in a ``_FnHook`` for you.

A hook's ``handle`` may return ``None`` (treated as allow), a ``HookOutcome``, or
raise — the manager isolates exceptions so a buggy hook can never break an agent.
"""

from __future__ import annotations

from typing import Callable, Iterable

from awcp.agent_hooks.types import (
    HookCategory,
    HookContext,
    HookOutcome,
    HookType,
)


class Hook:
    """Base class for all hooks.

    Subclasses set ``name``, ``subscriptions`` (which ``HookType``s to receive),
    optionally ``category`` and ``priority``, and implement ``handle``.
    """

    #: Stable, human-readable identifier (shown in the ``/hooks`` API + logs).
    name: str = "unnamed-hook"
    #: Informational behaviour label (OBSERVER or GUARD).
    category: HookCategory = HookCategory.OBSERVER
    #: Lifecycle points this hook fires on. Empty = never.
    subscriptions: tuple[HookType, ...] = ()
    #: Lower runs first. Guards usually want a low number so they veto early.
    priority: int = 100
    #: Toggled at runtime via the manager / API without unregistering.
    enabled: bool = True

    def types(self) -> Iterable[HookType]:
        return self.subscriptions

    def handle(self, ctx: HookContext) -> HookOutcome | None:  # pragma: no cover - overridden
        """Run the hook. Return ``None``/``HookOutcome.allow()`` to do nothing,
        or ``HookOutcome.deny(reason)`` at a GUARD point to veto."""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<Hook {self.name} {self.category.value} prio={self.priority} sub={[t.value for t in self.subscriptions]}>"


class _FnHook(Hook):
    """Adapter that turns a plain ``fn(ctx) -> HookOutcome | None`` into a Hook."""

    def __init__(
        self,
        fn: Callable[[HookContext], HookOutcome | None],
        *,
        types: Iterable[HookType],
        name: str | None = None,
        category: HookCategory = HookCategory.OBSERVER,
        priority: int = 100,
    ) -> None:
        self._fn = fn
        self.name = name or getattr(fn, "__name__", "fn-hook")
        self.category = category
        self.subscriptions = tuple(types)
        self.priority = priority

    def handle(self, ctx: HookContext) -> HookOutcome | None:
        return self._fn(ctx)
