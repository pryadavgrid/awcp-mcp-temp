"""Framework-agnostic onboarding logic.

These pure(ish) helpers are shared by the Temporal activities and the
no-Temporal inline fallback, so the admission decision and MCP linking behave
identically either way.
"""

from __future__ import annotations

import getpass

from awcp.radar.models import AgentEntry, KIND_MCP_SERVER


def map_identity_patch(entry: AgentEntry) -> dict:
    """Normalize identity fields (owner/runtime/version) like the magazine's
    'map owner, runtime, and declared scope' step. Fills gaps, never overwrites."""
    return {
        "owner": entry.owner or getpass.getuser(),
        "runtime": entry.runtime or entry.framework or "unknown",
        "version": entry.version or "unknown",
    }


def decide_status(entry: AgentEntry) -> tuple[str, str | None]:
    """AWCP onboarding gate: an agent leaves quarantine only once it has the
    control hooks the magazine requires. The magazine names all three:
    "telemetry, flag wiring, and policy callbacks ... observed in execution"
    (Onboarding Quarantine). So we require observability (telemetry) AND feature
    flags AND policy callbacks. Returns (status, quarantine_reason)."""
    missing: list[str] = []
    if not entry.telemetry_enabled:
        missing.append("telemetry/observability")
    if not entry.feature_flags:
        missing.append("feature flags")
    if not entry.policy_callbacks:
        missing.append("policy callbacks")

    if missing:
        return "quarantined", "missing control hooks: " + ", ".join(missing)
    return "active", None


def _sse_url(entry: AgentEntry) -> str | None:
    """Resolve an SSE URL to connect to, or None if not connectable."""
    ep = (entry.endpoint or "").strip()
    if not ep:
        return None
    if not ep.startswith(("http://", "https://")):
        return None
    if "/sse" in ep:
        return ep
    return ep.rstrip("/") + "/sse"


async def link_mcp(entry: AgentEntry) -> tuple[list[str], str | None]:
    """If the entry is an MCP server (or exposes an MCP SSE endpoint), connect as
    a client and enumerate its tools. Returns (capabilities, note).

    Best-effort: stdio servers owned by another process can't be attached to, and
    any connection error is tolerated (empty capabilities + a note)."""
    is_mcp = entry.kind == KIND_MCP_SERVER or bool(entry.endpoint)
    if not is_mcp:
        return [], None

    url = _sse_url(entry)
    if not url:
        if entry.transport == "stdio":
            return [], "stdio MCP server (owned by its parent) — not directly linkable"
        return [], "no SSE endpoint to link"

    try:
        import anyio
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async def _list() -> list[str]:
            async with sse_client(url, headers={"ngrok-skip-browser-warning": "true"}) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return [t.name for t in tools.tools]

        with anyio.fail_after(15):
            caps = await _list()
        return caps, f"linked via {url}"
    except Exception as e:  # noqa: BLE001 - best-effort link
        return [], f"link failed: {type(e).__name__}: {e}"
