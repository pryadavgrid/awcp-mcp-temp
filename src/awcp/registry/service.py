import hashlib
import inspect
import os
import getpass

from awcp.agents.base import AgentSpec
from awcp.registry.discovery import discover_agents
from awcp.registry.models import AgentEntry
from awcp.registry.store import populate


def _make_stable_id(route: str) -> str:
    """Generate a stable ID by hashing the agent's route."""
    return f"agt_{hashlib.md5(route.encode()).hexdigest()[:8]}"


def _spec_to_card(spec: AgentSpec, endpoint_url: str, version: str) -> dict:
    """Generate a minimal A2A AgentCard dict from an AgentSpec, so AWCP's own agents
    are discoverable at /.well-known/agent.json. Built via the AgentCard model so
    schema-level defaults (protocol_version, etc.) come from one source (radar/card.py)
    rather than being re-stated here. The governance fields it carries are advisory
    only — same boundary as a fetched card (see radar/card.py)."""
    from awcp.radar.card import AgentCard, AgentSkill
    card = AgentCard(
        name=spec.name,
        description=f"AWCP agent: {spec.name}",
        url=endpoint_url,
        version=version or "unknown",
        skills=[AgentSkill(id=spec.tool, name=spec.tool)] if spec.tool else [],
        capabilities={"streaming": False},
        write_scopes=list(spec.write_scopes or []),
        policy_callbacks=list(getattr(spec, "policy_callbacks", []) or []),
        feature_flags=dict(spec.feature_flags or {}),
        # AWCP's own in-process agents are the most-trusted tier (T0); risk uses the
        # AgentCard default. Both read from the spec when it provides them.
        risk=getattr(spec, "risk", None) or AgentCard.model_fields["risk"].default,
        harness_tier=getattr(spec, "harness_tier", 0),
    )
    return card.model_dump()


def _hash_agent_file(spec: AgentSpec) -> str:
    """
    Locate the physical source file of the agent's handler function
    and hash its contents to produce a deterministic version string.
    """
    try:
        file_path = inspect.getfile(spec.handler)
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        return f"1.0.0-auto+{hashlib.md5(file_bytes).hexdigest()[:7]}"
    except (TypeError, OSError):
        return "1.0.0-auto+unknown"


def build_registry() -> list[AgentSpec]:
    """
    Discover agents, dynamically infer governance metadata, enforce
    admission control, populate the in-memory store, and return the
    raw AgentSpec list so the caller can wire FastAPI routes.

    Environment variables:
      AWCP_TUNNEL_BASE_URL    - Public base URL for endpoint_url construction.
                                Defaults to http://localhost:8001.
      AWCP_DEFAULT_OWNER      - Owner assigned to all agents.
                                Defaults to the current OS username.
      AWCP_TELEMETRY_ENABLED  - Master telemetry flag. Defaults to "true".
                                Agents with write_scopes are quarantined
                                when this is "false".
    """
    base_url: str = os.getenv("AWCP_TUNNEL_BASE_URL", "http://localhost:8001")
    env_owner: str = os.getenv("AWCP_DEFAULT_OWNER", getpass.getuser())
    telemetry_on: bool = os.getenv("AWCP_TELEMETRY_ENABLED", "true").lower() == "true"

    specs: list[AgentSpec] = discover_agents()

    entries: list[AgentEntry] = []

    for spec in specs:
        stable_id = _make_stable_id(spec.route)
        dynamic_version = spec.version if spec.version is not None else _hash_agent_file(spec)
        owner = spec.owner if spec.owner is not None else env_owner
        feature_flags = {**spec.feature_flags, "telemetry_enabled": telemetry_on}

        # Admission control: quarantine agents that declare write scopes
        # but are running without telemetry enabled.
        if len(spec.write_scopes) > 0 and not telemetry_on:
            status = "quarantined"
        else:
            status = "active"

        endpoint_url = f"{base_url.rstrip('/')}{spec.route}"
        card = _spec_to_card(spec, endpoint_url, dynamic_version)
        entries.append(AgentEntry(
            agent_id=stable_id,
            name=spec.name,
            route=spec.route,
            endpoint_url=endpoint_url,
            runtime=spec.runtime,
            version=dynamic_version,
            owner=owner,
            write_scopes=spec.write_scopes,
            feature_flags=feature_flags,
            status=status,
            # AgentCard generated from the spec (served from this process — so
            # card_url / card_fetched_at stay None, unlike a fetched card).
            card=card,
            skills=[spec.tool] if spec.tool else [],
            card_url=None,
            card_fetched_at=None,
        ))

    populate(entries)

    return specs
