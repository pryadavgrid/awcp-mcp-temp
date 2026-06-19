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


def magazine_profile(name: str) -> dict | None:
    """Look up an agent's governance profile in the centralized magazine
    (by exact name, else the magazine's __default__ entry). Returns the profile
    dict, or None if the magazine can't be read. Shared by map_identity_patch
    here and policy.assigned_risk_for, so both read the same ground truth."""
    try:
        magazine_path = os.path.join(os.path.dirname(__file__), "awcp_magazine.json")
        with open(magazine_path, "r") as f:
            magazine = json.load(f)
    except Exception:
        return None
    return magazine.get(name) or magazine.get("__default__")


def map_identity_patch(entry: AgentEntry) -> dict:
    """Normalize identity fields (owner/runtime/version) like the magazine's
    'map owner, runtime, and declared scope' step. Fills gaps, never overwrites."""
    patch = {
        "owner": entry.owner or getpass.getuser(),
        "runtime": entry.runtime or entry.framework or "unknown",
        "version": entry.version or "unknown",
    }

    # Load centralized magazine to enforce governance policies
    profile = magazine_profile(entry.name)
    if profile is None:
        # If the magazine can't be read, fail closed (high risk).
        patch["risk"] = "high"
        return patch

    # Risk is resolved to the MORE RESTRICTIVE of the agent's declared tier and
    # the magazine's assigned tier — a self-declared "critical" is never
    # downgraded to the magazine's "low", and a self-declared "low" never escapes
    # the magazine's "high" (hardening gap #1). Lazy import avoids a cycle.
    from awcp.radar import policy
    patch["risk"] = policy.more_restrictive(entry.risk, profile.get("risk"))
    if "token_budget" in profile:
        patch["token_budget"] = profile["token_budget"]
    if "write_scopes" in profile:
        patch["write_scopes"] = profile["write_scopes"]

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
    # Operator approval gate (hardening gap #5) takes precedence over hooks: an
    # agent flagged for re-approval (e.g. it added write_scopes on restart) stays
    # quarantined no matter how complete its hooks are, until an operator clears
    # it. This is what makes a scope_added quarantine STICK through re-onboarding
    # and the observed-hook re-promotion paths.
    if getattr(entry, "approval_state", None) == "pending":
        return "quarantined", (entry.approval_reason
                               or "awaiting operator approval (scope change)")

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

    # SSRF guard: the SSE URL comes from the registration payload, so resolve it
    # and refuse private/link-local targets (e.g. the cloud metadata service)
    # before opening a client connection to it.
    from awcp.radar.netguard import assert_safe_url, UnsafeURLError
    try:
        assert_safe_url(url)
    except UnsafeURLError as e:
        return [], f"link refused (unsafe url): {e}"

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
