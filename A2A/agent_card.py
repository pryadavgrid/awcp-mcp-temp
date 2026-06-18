"""A2A-style Agent Card  +  AWCP governance envelope.  (stdlib only)

We BORROW the A2A Agent Card *schema* (its field names / shape) as our
registration format. We are NOT implementing the A2A wire protocol — there is no
JSON-RPC `message/send`, no task state machine, no SSE here. The Card only
describes *what an agent is*; the AWCP envelope adds the governance facts A2A
deliberately leaves out (owner, requested write scopes, risk, intake path).

    registration body  =  { "card": <AgentCard>, "awcp": <AwcpEnvelope> }

The Card is serialised in A2A's own camelCase (protocolVersion, defaultInputModes,
…) so it looks like a real Agent Card; the `awcp` block stays snake_case so it is
visually obvious which part is the borrowed standard and which part is ours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = "0.3.0"                       # the A2A schema version we mirror
RISK_LEVELS = ("low", "medium", "high")
INTAKE_PATHS = ("self-register", "operator", "scan")

# Tools that perform a state-changing (governed) write. web_search etc. are reads
# and need no scope. Used to derive sensible defaults from an agent's tool list.
KNOWN_WRITE_SCOPES = {"save_artifact", "external_post"}


# ───────────────────────────── A2A Agent Card ──────────────────────────────
@dataclass
class Skill:
    """One capability the agent advertises (A2A `skills[]`)."""
    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "tags": list(self.tags), "examples": list(self.examples)}

    @staticmethod
    def from_dict(d: dict) -> "Skill":
        return Skill(id=d.get("id", ""), name=d.get("name", ""),
                     description=d.get("description", ""),
                     tags=list(d.get("tags", [])), examples=list(d.get("examples", [])))


@dataclass
class Provider:
    organization: str = "AWCP"
    url: str = ""


@dataclass
class Capabilities:
    streaming: bool = False
    push_notifications: bool = False


@dataclass
class AgentCard:
    """A2A Agent Card (borrowed shape). Describes the agent itself."""
    name: str
    url: str
    description: str = ""
    version: str = "1.0.0"
    protocol_version: str = PROTOCOL_VERSION
    provider: Provider = field(default_factory=Provider)
    capabilities: Capabilities = field(default_factory=Capabilities)
    default_input_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    skills: list[Skill] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to A2A camelCase — looks like a real .well-known Agent Card."""
        return {
            "protocolVersion": self.protocol_version,
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "provider": {"organization": self.provider.organization,
                         "url": self.provider.url},
            "capabilities": {"streaming": self.capabilities.streaming,
                             "pushNotifications": self.capabilities.push_notifications},
            "defaultInputModes": list(self.default_input_modes),
            "defaultOutputModes": list(self.default_output_modes),
            "skills": [s.to_dict() for s in self.skills],
        }

    @staticmethod
    def from_dict(d: dict) -> "AgentCard":
        prov = d.get("provider") or {}
        caps = d.get("capabilities") or {}
        return AgentCard(
            name=d.get("name", ""),
            url=d.get("url", ""),
            description=d.get("description", ""),
            version=d.get("version", "1.0.0"),
            protocol_version=d.get("protocolVersion", PROTOCOL_VERSION),
            provider=Provider(organization=prov.get("organization", "AWCP"),
                              url=prov.get("url", "")),
            capabilities=Capabilities(streaming=bool(caps.get("streaming", False)),
                                      push_notifications=bool(caps.get("pushNotifications", False))),
            default_input_modes=list(d.get("defaultInputModes", ["text/plain"])),
            default_output_modes=list(d.get("defaultOutputModes", ["text/plain"])),
            skills=[Skill.from_dict(s) for s in d.get("skills", [])],
        )


# ─────────────────────── AWCP governance envelope (ours) ────────────────────
@dataclass
class AwcpEnvelope:
    """Governance facts the A2A card has no place for. This is OUR extension."""
    owner: str
    requested_write_scopes: list[str] = field(default_factory=list)
    risk: str = "medium"
    intake: str = "self-register"
    framework: str = ""               # operational metadata AWCP policies may use
    model: str = ""
    token: str = ""                   # optional shared secret (authenticity, push path)

    def to_dict(self) -> dict:
        return {"owner": self.owner,
                "requested_write_scopes": list(self.requested_write_scopes),
                "risk": self.risk, "intake": self.intake,
                "framework": self.framework, "model": self.model,
                "token": self.token}

    @staticmethod
    def from_dict(d: dict) -> "AwcpEnvelope":
        return AwcpEnvelope(
            owner=d.get("owner", ""),
            requested_write_scopes=list(d.get("requested_write_scopes", [])),
            risk=d.get("risk", "medium"), intake=d.get("intake", "self-register"),
            framework=d.get("framework", ""), model=d.get("model", ""),
            token=d.get("token", ""))


# ──────────────────────────────── validation ───────────────────────────────
def validate(registration: dict) -> tuple[bool, str]:
    """Schema/sanity check of a `{card, awcp}` registration body.

    This is the cheap 'well-formed?' gate. Authenticity ('genuine vs forged') is a
    separate, stronger step — see registry.verify_authentic().
    """
    card = registration.get("card") or {}
    awcp = registration.get("awcp") or {}

    if not card.get("name"):
        return False, "card.name is required"
    url = card.get("url", "")
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "card.url must be an http(s) URL"
    if not card.get("protocolVersion"):
        return False, "card.protocolVersion is required"
    intake = awcp.get("intake", "self-register")
    if intake not in INTAKE_PATHS:
        return False, f"awcp.intake must be one of {INTAKE_PATHS}"
    # Scan-discovered stubs are admitted INCOMPLETE so they stay visible in the
    # registry (quarantined); the owner/scopes are required only to *approve* them.
    if not awcp.get("owner") and intake != "scan":
        return False, "awcp.owner is required"
    if awcp.get("risk", "medium") not in RISK_LEVELS:
        return False, f"awcp.risk must be one of {RISK_LEVELS}"
    if not isinstance(awcp.get("requested_write_scopes", []), list):
        return False, "awcp.requested_write_scopes must be a list"
    return True, "ok"


# ─────────────────── builders: your existing meta dict → card ───────────────
def card_from_meta(meta: dict, *, url: str) -> AgentCard:
    """Turn an agent's existing `meta={...}` dict (see each agent_runtime.py) into
    an A2A Agent Card — no change to the agents required to try this."""
    tools = list(meta.get("tools", []))
    skill = Skill(
        id=(meta.get("agent", "agent").lower().replace(" ", "-")),
        name=meta.get("agent", "agent"),
        description=meta.get("purpose", ""),
        tags=tools,
        examples=list(meta.get("examples", [])),
    )
    out_fmt = meta.get("format", "")
    out_modes = ["text/markdown"] if out_fmt == "markdown" else ["text/plain"]
    return AgentCard(
        name=meta.get("agent", "agent"),
        url=url,
        description=meta.get("purpose", ""),
        default_output_modes=out_modes,
        skills=[skill],
    )


def registration_from_meta(
    meta: dict, *, url: str, owner: str,
    requested_write_scopes: list[str] | None = None,
    risk: str | None = None, intake: str = "self-register", token: str = "",
) -> dict:
    """Full `{card, awcp}` body built from an agent's meta dict.

    If write scopes aren't given, derive them from the agent's tools (only the
    state-changing ones). Risk defaults to 'high' when an external write is asked
    for, else 'medium' — a sensible policy default the operator can override.
    """
    tools = list(meta.get("tools", []))
    if requested_write_scopes is None:
        requested_write_scopes = [t for t in tools if t in KNOWN_WRITE_SCOPES]
    if risk is None:
        risk = "high" if "external_post" in requested_write_scopes else "medium"
    env = AwcpEnvelope(
        owner=owner, requested_write_scopes=requested_write_scopes, risk=risk,
        intake=intake, framework=meta.get("framework", ""),
        model=meta.get("model", ""), token=token,
    )
    return {"card": card_from_meta(meta, url=url).to_dict(), "awcp": env.to_dict()}


def scan_stub(name: str, *, url: str = "", pid: int | None = None) -> dict:
    """A scan-discovered process can't DECLARE its owner/scopes/skills — those
    aren't visible from the OS. So the pull path can only produce this thin,
    unverified stub that MUST be completed (operator fills the card, or the agent
    serves one) before it can be approved."""
    card = AgentCard(name=name, url=url or "http://unknown",
                     description="(discovered by scan — card not yet provided)")
    env = AwcpEnvelope(owner="", intake="scan", risk="high")
    body = {"card": card.to_dict(), "awcp": env.to_dict()}
    if pid is not None:
        body["awcp"]["pid"] = pid
    return body
