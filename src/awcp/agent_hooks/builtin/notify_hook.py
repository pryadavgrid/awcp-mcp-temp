"""NotifyHook — a side-effecting OBSERVER hook (off by default).

Sends a short message to a webhook (Slack / Discord / any "text" endpoint) when
something a human should know about happens:

  * a high-risk action needs approval (``APPROVAL_REQUIRED``)
  * an action was blocked by governance (``ACTION_BLOCKED``)
  * an agent ran out of token budget (``BUDGET_EXHAUSTED``)
  * an agent was degraded (``AUTONOMY_DEGRADED``)

Reference example for an *external-side-effect* hook done safely:
  * it does nothing unless a webhook URL is configured;
  * the HTTP POST runs on a daemon thread so it never blocks the radar's event
    loop or an agent's task;
  * any failure is swallowed (best-effort).
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request

from awcp.agent_hooks.base import Hook
from awcp.agent_hooks.types import HookCategory, HookContext, HookType

log = logging.getLogger("awcp.agent_hooks.notify")

_EMOJI = {
    HookType.APPROVAL_REQUIRED: "✋",
    HookType.ACTION_BLOCKED: "⛔",
    HookType.BUDGET_EXHAUSTED: "💸",
    HookType.AUTONOMY_DEGRADED: "⚠️",
}


class NotifyHook(Hook):
    name = "notify"
    category = HookCategory.OBSERVER
    subscriptions = (
        HookType.APPROVAL_REQUIRED,
        HookType.ACTION_BLOCKED,
        HookType.BUDGET_EXHAUSTED,
        HookType.AUTONOMY_DEGRADED,
    )
    priority = 50

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def handle(self, ctx: HookContext) -> None:
        if not self.webhook_url:
            return  # nothing configured — silent no-op
        emoji = _EMOJI.get(ctx.hook_type, "🔔")
        detail = ctx.get("reason") or ctx.get("detail") or ctx.action or ""
        text = (f"{emoji} *AWCP* `{ctx.hook_type.value}` — agent `{ctx.agent_id or '?'}`"
                + (f" task `{ctx.task_id}`" if ctx.task_id else "")
                + (f"\n{detail}" if detail else ""))
        # Fire-and-forget on a daemon thread so we never block the caller.
        threading.Thread(
            target=self._post, args=(text,), daemon=True, name="awcp-hook-notify"
        ).start()

    def _post(self, text: str) -> None:
        try:
            data = json.dumps({"text": text}).encode()
            req = urllib.request.Request(
                self.webhook_url, data=data,
                headers={"content-type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        except Exception as exc:  # noqa: BLE001 — notifications are best-effort
            log.debug("notify.post_failed error=%r", exc)
