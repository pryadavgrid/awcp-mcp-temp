"""Data model for a detected / registered agentic environment."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


def _now() -> float:
    return time.time()


# Environment kinds the radar tracks (aligned to the AWCP magazine layers).
KIND_AGENT_FRAMEWORK = "agent_framework"
KIND_MCP_SERVER = "mcp_server"
KIND_LLM_RUNTIME = "llm_runtime"
KIND_ORCHESTRATOR = "orchestrator"


class AgentEntry(BaseModel):
    """A single entry in the radar registry.

    Sources:
      - "scan": auto-detected running process on this machine.
      - "self": announced itself via POST /agents/register.

    Status follows the AWCP "onboarding quarantine" idea: anything without
    declared telemetry/governance hooks is `quarantined` (visible, not trusted)
    until onboarding completes.
    """

    id: str
    name: str
    kind: str = KIND_AGENT_FRAMEWORK
    framework: str | None = None          # langgraph / crewai / ollama / temporal / ...
    source: str = "scan"                   # "scan" | "self"
    status: str = "quarantined"            # "quarantined" | "active"
    quarantine_reason: str | None = None

    # --- write-action gate + degradation ladder (ported from awcp_agents) ---
    # autonomy_profile mirrors awcp_agents' active -> recommendation_only path,
    # extended with a hard-stop rung. The gate uses it to allow/deny write actions;
    # the degradation ladder steps it down as failures accrue.
    autonomy_profile: str = "active"        # "active" | "recommendation_only" | "suspended"
    autonomy_reason: str | None = None
    failure_count: int = 0                  # toward the failure budget

    # --- magazine governance properties ---
    owner: str | None = None
    runtime: str | None = None
    version: str | None = None
    write_scopes: list[str] = Field(default_factory=list)
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    telemetry_enabled: bool = False        # observability / OTel hook present
    policy_callbacks: list[str] = Field(default_factory=list)

    # --- per-agent degradation policy (magazine: "each workflow can override
    # thresholds and ladders by risk"). Empty/None => fall back to the system
    # defaults in policy.py. autonomy_ladder[0] is full autonomy, [-1] is the
    # hard stop, anything in between is recommendation-only. ---
    risk: str = "medium"                    # low | medium | high (risk tier)
    autonomy_ladder: list[str] = Field(default_factory=list)
    failure_budget: int | None = None
    token_budget: int | None = None         # magazine-assigned token budget

    # --- connectivity / capability ---
    endpoint: str | None = None
    transport: str | None = None           # stdio | sse | http
    capabilities: list[str] = Field(default_factory=list)  # MCP tools, etc.

    # --- process facts (scan source) ---
    pid: int | None = None
    user: str | None = None
    cwd: str | None = None
    cmdline: str | None = None
    detected_via: str | None = None        # cmdline | open_files | script_import | port

    # --- onboarding lifecycle (Temporal) ---
    onboarding_state: str | None = None    # None/pending | running | done
    onboarding_workflow_id: str | None = None

    # --- liveness ---
    first_seen: float = Field(default_factory=_now)
    last_seen: float = Field(default_factory=_now)
    alive: bool = True

    # fields a fresh scan is allowed to refresh on an existing entry
    _SCAN_REFRESH = (
        "name", "kind", "framework", "pid", "user", "cwd", "cmdline",
        "detected_via", "endpoint", "transport", "runtime", "last_seen",
    )

    def merged_from_scan(self, other: "AgentEntry") -> "AgentEntry":
        """Refresh process/liveness fields from a fresh scan; keep onboarding +
        first_seen + any enriched governance/capability state."""
        data = self.model_dump()
        for f in self._SCAN_REFRESH:
            data[f] = getattr(other, f)
        data["alive"] = True
        return AgentEntry(**data)


class RegisterRequest(BaseModel):
    """Body for POST /agents/register (self-registration webhook)."""

    name: str
    kind: str = KIND_AGENT_FRAMEWORK
    framework: str | None = None
    runtime: str | None = None
    version: str | None = None
    owner: str | None = None
    endpoint: str | None = None
    transport: str | None = None
    write_scopes: list[str] = Field(default_factory=list)
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    policy_callbacks: list[str] = Field(default_factory=list)
    telemetry_enabled: bool = False
    # per-agent risk tier + optional override of the degradation ladder/budget
    risk: str = "medium"                    # low | medium | high
    autonomy_ladder: list[str] = Field(default_factory=list)
    failure_budget: int | None = None
    id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
