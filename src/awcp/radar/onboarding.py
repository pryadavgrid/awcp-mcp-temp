"""Framework-agnostic onboarding logic.

These pure(ish) helpers are shared by the Temporal activities and the
no-Temporal inline fallback, so the admission decision and MCP linking behave
identically either way.
"""

from __future__ import annotations

import getpass
import json
import os

from awcp.radar.models import AgentEntry, KIND_MCP_SERVER


def map_identity_patch(entry: AgentEntry) -> dict:
    """Normalize identity fields (owner/runtime/version) like the magazine's
    'map owner, runtime, and declared scope' step. Fills gaps, never overwrites."""
    patch = {
        "owner": entry.owner or getpass.getuser(),
        "runtime": entry.runtime or entry.framework or "unknown",
        "version": entry.version or "unknown",
    }

    # Load centralized magazine to enforce governance policies
    try:
        magazine_path = os.path.join(os.path.dirname(__file__), "awcp_magazine.json")
        with open(magazine_path, "r") as f:
            magazine = json.load(f)
            
        # Lookup the agent by name, or fallback to __default__
        profile = magazine.get(entry.name) or magazine.get("__default__")
        
        if profile:
            # Overwrite the agent's requested values with the magazine's ground truth
            patch["risk"] = profile.get("risk", "high")
            if "token_budget" in profile:
                patch["token_budget"] = profile["token_budget"]
            if "write_scopes" in profile:
                patch["write_scopes"] = profile["write_scopes"]
    except Exception:
        # If magazine fails to load, fail closed (high risk)
        patch["risk"] = "high"

    return patch


def decide_status(entry: AgentEntry) -> tuple[str, str | None]:
    """AWCP onboarding gate: an agent leaves quarantine only once it has the
    control hooks the magazine requires, "telemetry, flag wiring, and policy
    callbacks ... observed in execution" (Onboarding Quarantine):

      * telemetry      — OBSERVED (entry.telemetry_enabled is a projection of
                         real telemetry arriving, not a declared flag);
      * feature flags  — declared AND OBSERVED: registered, and the agent reports
                         flag state in execution (sets entry.flags_observed);
      * policy callbacks — declared AND OBSERVED: the agent must both register a
                         policy callback and actually exercise its policy hook
                         (consult the gate), which sets entry.policy_observed.

    Each hook's declared-vs-observed strictness is governed by its
    AGENT_RADAR_REQUIRE_OBSERVED_* flag (which controls how the *_observed fields
    are seeded at registration), so this gate stays a pure read of the entry.
    Returns (status, quarantine_reason)."""
    missing: list[str] = []
    if not entry.telemetry_enabled:
        missing.append("telemetry/observability (not observed)")
    if not entry.feature_flags:
        missing.append("feature flags (none declared)")
    elif not entry.flags_observed:
        missing.append("feature flags (declared, not yet observed in execution)")
    if not entry.policy_callbacks:
        missing.append("policy callbacks (none declared)")
    elif not entry.policy_observed:
        missing.append("policy callbacks (declared, not yet observed in execution)")

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
