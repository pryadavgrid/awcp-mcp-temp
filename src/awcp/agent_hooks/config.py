"""Configuration for the agent-hooks system — all env-driven, nothing hardcoded.

The defaults are demo-friendly: the four read-only observer hooks (logging,
audit, metrics, timing) load automatically; the side-effecting ones (notify,
policy-guard) are OFF until you opt in, because they call out to the network /
change agent behaviour.

| Env var                         | Default | Effect                                   |
|---------------------------------|---------|------------------------------------------|
| AWCP_HOOKS_ENABLED              | true    | master switch for the whole system       |
| AWCP_HOOKS_LOGGING              | true    | structured log line per lifecycle event  |
| AWCP_HOOKS_AUDIT                | true    | append-only JSONL audit trail            |
| AWCP_HOOKS_METRICS              | true    | OTel counters/histograms per event       |
| AWCP_HOOKS_TIMING               | true    | per-task stage timing                    |
| AWCP_HOOKS_NOTIFY               | false   | webhook/Slack on approval + budget breach|
| AWCP_HOOKS_POLICY_GUARD         | false   | veto tools on a deny-list (GUARD hook)   |
| AWCP_HOOKS_AUDIT_PATH           | /tmp/awcp-agent-hooks-audit.jsonl |                |
| AWCP_HOOKS_NOTIFY_WEBHOOK       | (unset) | Slack/Discord/generic webhook URL        |
| AWCP_HOOKS_DENY_TOOLS           | (unset) | comma list of tool names to veto         |
"""

from __future__ import annotations

import os


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


ENABLED = _flag("AWCP_HOOKS_ENABLED", True)

LOAD_LOGGING = _flag("AWCP_HOOKS_LOGGING", True)
LOAD_AUDIT = _flag("AWCP_HOOKS_AUDIT", True)
LOAD_METRICS = _flag("AWCP_HOOKS_METRICS", True)
LOAD_TIMING = _flag("AWCP_HOOKS_TIMING", True)
LOAD_NOTIFY = _flag("AWCP_HOOKS_NOTIFY", False)
LOAD_POLICY_GUARD = _flag("AWCP_HOOKS_POLICY_GUARD", False)

AUDIT_PATH = os.getenv("AWCP_HOOKS_AUDIT_PATH", "/tmp/awcp-agent-hooks-audit.jsonl")
NOTIFY_WEBHOOK = os.getenv("AWCP_HOOKS_NOTIFY_WEBHOOK", "").strip()
DENY_TOOLS = [t.strip() for t in os.getenv("AWCP_HOOKS_DENY_TOOLS", "").split(",") if t.strip()]
