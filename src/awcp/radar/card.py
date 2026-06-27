"""A2A AgentCard schema (+ AWCP governance extensions).

An **AgentCard** is the A2A-protocol manifest an agent publishes at
``/.well-known/agent.json`` — a self-describing document covering name, version,
endpoint URL, supported protocols, authentication, and a typed **skills** list. It
is the description layer the AWCP registry lacked: the registry governs *what an
agent may do* (risk / write_scopes / autonomy) but had no structured record of
*what it is for*.

This module keeps the A2A schema OUT of ``models.py`` (the internal registry
model) so the two evolve independently and there is no import cycle when
``onboarding.py`` reads both. The models are intentionally lenient
(``extra="allow"``) so a card authored against a newer A2A spec still parses —
unknown fields are preserved, never rejected.

GOVERNANCE BOUNDARY: the AWCP extension fields below (``write_scopes``,
``policy_callbacks``, ``feature_flags``, ``risk``, ``harness_tier``) are ADVISORY
only. They are stored inside the card blob for operator introspection; the
onboarding pipeline NEVER patches them onto the enforced ``AgentEntry`` governance
fields. A self-published JSON file must not be able to widen its own grants
(hardening gap #5). The magazine (``map_identity``) and the guarded
register/announce paths remain the only routes to enforced governance.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AgentSkill(BaseModel):
    """One typed capability an agent advertises (A2A `skills[*]`)."""
    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    description: str = ""
    tags: list[str] = []
    input_modes: list[str] = ["text"]    # text | file | structured | data
    output_modes: list[str] = ["text"]


class AgentCard(BaseModel):
    """A2A-compatible agent card with AWCP governance extensions (advisory)."""
    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    url: str = ""
    version: str = "unknown"
    protocol_version: str = "0.6"        # A2A spec version
    skills: list[AgentSkill] = []
    capabilities: dict = {}              # A2A: streaming, pushNotifications, etc.
    authentication: dict | None = None   # A2A: schemes the agent accepts

    # AWCP extensions — ADVISORY ONLY (see module docstring's governance boundary).
    harness_tier: int | None = None
    write_scopes: list[str] = []
    policy_callbacks: list[str] = []
    feature_flags: dict[str, bool] = {}
    risk: str = "medium"


def skill_ids(raw: dict) -> list[str]:
    """Denormalize skill ids from a raw card dict — the flat projection stored on
    ``AgentEntry.skills`` for fast in-memory ``?skill=`` filtering. Tolerant of a
    malformed/partial card (skipped entries, non-list skills)."""
    skills = raw.get("skills") if isinstance(raw, dict) else None
    if not isinstance(skills, list):
        return []
    return [s["id"] for s in skills if isinstance(s, dict) and s.get("id")]
