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


def synthesize_card(entry) -> tuple[dict, list[str]]:
    """Build a fallback AgentCard for an agent that never published one.

    Projected purely from what the registry ALREADY knows about the agent (its
    name, framework/runtime, endpoint, and declared write_scopes / capabilities) —
    so no information is invented and the same governance boundary as a fetched
    card holds: these fields are a READ-ONLY projection of already-registered data,
    never a new grant. The card is tagged ``source="synthesized"`` so it is always
    distinguishable from a genuinely fetched (agent-declared) one.

    Returns ``(card_dict, skill_ids)``. The skill ids live INSIDE the card only;
    callers deliberately do NOT copy them onto ``AgentEntry.skills`` (which feeds
    skill-based operator policy) — a synthesized card is for display, not control.
    Accepts the entry duck-typed (via getattr) to avoid importing the registry
    model here and creating an import cycle."""
    name = getattr(entry, "name", None) or getattr(entry, "id", "") or "agent"
    framework = getattr(entry, "framework", None) or getattr(entry, "runtime", None) or ""

    # Skills = declared write_scopes ∪ MCP capabilities, de-duped, order-preserved.
    raw_skills = list(getattr(entry, "write_scopes", None) or []) + \
        list(getattr(entry, "capabilities", None) or [])
    seen: set[str] = set()
    sk: list[str] = []
    for s in raw_skills:
        if isinstance(s, str) and s and s not in seen:
            seen.add(s)
            sk.append(s)

    base = f"{framework} agent: {name}".strip(" :") if framework else f"agent: {name}"
    card = AgentCard(
        name=name,
        description=f"{base} — card synthesized by the registry (agent published none).",
        url=getattr(entry, "endpoint", None) or "",
        version=getattr(entry, "version", None) or "unknown",
        skills=[AgentSkill(id=s, name=s, tags=["declared"]) for s in sk],
        capabilities={"streaming": False},
        write_scopes=list(getattr(entry, "write_scopes", None) or []),
        policy_callbacks=list(getattr(entry, "policy_callbacks", None) or []),
        feature_flags=dict(getattr(entry, "feature_flags", None) or {}),
        risk=getattr(entry, "risk", None) or AgentCard.model_fields["risk"].default,
    ).model_dump()
    card["source"] = "synthesized"
    return card, sk
