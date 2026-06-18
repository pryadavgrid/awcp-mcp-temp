"""Governed EXTERNAL write tool — POSTs a summary to an external system. Declared
`high` risk, so the MCP governance plane routes it through the radar write-action
gate before it runs (and, on the agent side, behind an operator approval).

Nothing is agent-specific or hardcoded: the destination URL and optional bearer
token are env-driven, and the risk tier is declared on the @tool decorator.
"""

import json
import os
import urllib.request

from awcp.runtime.tool_runtime import tool

EXTERNAL_WRITE_URL = os.getenv("AGENT_EXTERNAL_WRITE_URL", "https://httpbin.org/post")
EXTERNAL_WRITE_TOKEN = os.getenv("AGENT_EXTERNAL_WRITE_TOKEN", "")
EXTERNAL_WRITE_TIMEOUT = float(os.getenv("AGENT_EXTERNAL_WRITE_TIMEOUT", "15"))


@tool("external_post", risk=os.getenv("AWCP_EXTERNAL_POST_RISK", "high"))
def external_post(summary: str, agent: str = "agent") -> str:
    """Submit/publish a summary to an external system over HTTP.

    HIGH-RISK governed external write (gated by the radar; approval-gated agent-side).

    Args:
        summary: The text to publish externally.
        agent: Name of the originating agent (recorded in the payload).

    Returns:
        The HTTP status line of the external POST.
    """
    body = json.dumps({"agent": agent, "summary": summary}).encode()
    headers = {"content-type": "application/json"}
    if EXTERNAL_WRITE_TOKEN:
        headers["authorization"] = f"Bearer {EXTERNAL_WRITE_TOKEN}"
    req = urllib.request.Request(
        EXTERNAL_WRITE_URL, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=EXTERNAL_WRITE_TIMEOUT) as r:  # noqa: S310
        return f"external POST {EXTERNAL_WRITE_URL} -> HTTP {r.status}"
