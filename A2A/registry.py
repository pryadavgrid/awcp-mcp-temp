"""AWCP Registry — the one API all three intake paths converge on.  (stdlib only)

Lifecycle (your onboarding flow):

    register  ──▶  QUARANTINED  ──(approve)──▶  APPROVED + scopes granted
                       │
                       └──(deny)──▶  DENIED

Enforcement rule (your "active + approved → allow, else block"):

    can_execute(agent, scope)  ⇢  allowed  ⟺  status == APPROVED
                                            ∧  agent is ACTIVE (fresh heartbeat)
                                            ∧  scope ∈ granted_scopes

This is intentionally in-memory and dependency-free so it runs anywhere. Swap the
dict for a real store (the registry branch's store.py) without changing callers.
"""

from __future__ import annotations

import os
import time
import uuid
import urllib.request
import json
from dataclasses import dataclass, field

from agent_card import AwcpEnvelope, validate

# A registration token AWCP issued to agents *we* deploy. If set, self-registered
# agents must present a matching awcp.token to be 'verified' (the cheap authenticity
# check for our own agents; external agents would use a signed card instead).
EXPECTED_TOKEN = os.getenv("AWCP_REGISTRY_TOKEN", "")
# How long after the last heartbeat an agent still counts as ACTIVE.
ACTIVE_TTL = float(os.getenv("AWCP_ACTIVE_TTL", "60"))
# Where registry state is persisted so it survives a server restart.
STATE_FILE = os.getenv(
    "AWCP_REGISTRY_STATE", os.path.join(os.path.dirname(__file__), "registry_state.json"))

STATUS_QUARANTINED = "quarantined"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"


@dataclass
class Entry:
    id: str
    card: dict                       # the A2A Agent Card (as dict)
    awcp: dict                       # the governance envelope (as dict)
    intake: str
    status: str = STATUS_QUARANTINED
    granted_scopes: list[str] = field(default_factory=list)
    verified: bool = False           # passed the authenticity check?
    first_seen: float = field(default_factory=time.time)
    heartbeat_at: float = 0.0        # last heartbeat (0 = never)

    @property
    def active(self) -> bool:
        return (time.time() - self.heartbeat_at) <= ACTIVE_TTL

    @property
    def name(self) -> str:
        return self.card.get("name", self.id)

    def view(self) -> dict:
        return {
            "id": self.id, "name": self.name, "status": self.status,
            "active": self.active, "verified": self.verified, "intake": self.intake,
            "owner": self.awcp.get("owner", ""), "risk": self.awcp.get("risk", ""),
            "requested_write_scopes": self.awcp.get("requested_write_scopes", []),
            "granted_scopes": self.granted_scopes,
            "url": self.card.get("url", ""),
            "skills": [s.get("id") for s in self.card.get("skills", [])],
        }


def verify_authentic(registration: dict) -> tuple[bool, str]:
    """The 'genuine vs forged' check, kept separate from schema validation.

    Prototype mechanism = shared token (good for agents WE deploy). The natural
    upgrade for external/vendor agents is a signed card (A2A `signatures`, JWS)
    verified here instead — same call site, stronger proof.
    """
    if not EXPECTED_TOKEN:
        return False, "unverified (no AWCP_REGISTRY_TOKEN configured)"
    token = (registration.get("awcp") or {}).get("token", "")
    if token and token == EXPECTED_TOKEN:
        return True, "token ok"
    return False, "token missing/incorrect"


class Registry:
    def __init__(self) -> None:
        self._by_id: dict[str, Entry] = {}
        self._load()

    # ── intake (all three paths land here) ──────────────────────────────────
    def register(self, registration: dict, *, intake: str | None = None) -> dict:
        ok, reason = validate(registration)
        if not ok:
            return {"ok": False, "error": reason}

        env = AwcpEnvelope.from_dict(registration.get("awcp") or {})
        intake = intake or env.intake
        verified, _vreason = verify_authentic(registration)

        eid = self._make_id(registration["card"].get("name", "agent"))
        entry = Entry(
            id=eid, card=registration["card"], awcp=registration["awcp"],
            intake=intake, status=STATUS_QUARANTINED, verified=verified,
            heartbeat_at=time.time(),       # it just announced itself → active now
        )
        self._by_id[eid] = entry
        self._persist()
        return {"ok": True, "id": eid, "status": entry.status,
                "verified": verified, "message": "quarantined — awaiting approval"}

    def register_from_url(self, url: str, *, owner: str = "operator") -> dict:
        """OPERATOR path: fetch the agent's published Card and admit it."""
        card_url = url.rstrip("/") + "/.well-known/agent-card.json"
        try:
            with urllib.request.urlopen(card_url, timeout=5) as r:   # noqa: S310
                card = json.loads(r.read().decode())
        except Exception as exc:                                     # noqa: BLE001
            return {"ok": False, "error": f"could not fetch card: {exc}"}
        env = AwcpEnvelope(owner=owner, intake="operator",
                           framework="", model="")
        return self.register({"card": card, "awcp": env.to_dict()}, intake="operator")

    # ── governance transitions ──────────────────────────────────────────────
    def approve(self, eid: str, granted_scopes: list[str] | None = None) -> dict:
        e = self._by_id.get(eid)
        if not e:
            return {"ok": False, "error": "unknown agent"}
        # An incomplete (e.g. scan-discovered) entry stays visible but cannot be
        # approved until its card is completed with an owner.
        if not e.awcp.get("owner"):
            return {"ok": False, "error": "incomplete: owner required before approval"}
        # Default: grant exactly what was requested. Operator may narrow it.
        requested = e.awcp.get("requested_write_scopes", [])
        e.granted_scopes = list(granted_scopes if granted_scopes is not None else requested)
        e.status = STATUS_APPROVED
        self._persist()
        return {"ok": True, "status": e.status, "granted_scopes": e.granted_scopes}

    def deny(self, eid: str) -> dict:
        e = self._by_id.get(eid)
        if not e:
            return {"ok": False, "error": "unknown agent"}
        e.status, e.granted_scopes = STATUS_DENIED, []
        self._persist()
        return {"ok": True, "status": e.status}

    def heartbeat(self, eid: str) -> dict:
        e = self._by_id.get(eid)
        if not e:
            return {"ok": False, "error": "unknown agent"}
        e.heartbeat_at = time.time()
        self._persist()
        return {"ok": True, "active": e.active}

    # ── the enforcement gate ────────────────────────────────────────────────
    def can_execute(self, eid: str, scope: str) -> tuple[bool, str]:
        e = self._by_id.get(eid)
        if not e:
            return False, "unknown agent"
        if e.status != STATUS_APPROVED:
            return False, f"blocked: status={e.status} (must be approved)"
        if not e.active:
            return False, "blocked: agent not active (stale/no heartbeat)"
        if scope not in e.granted_scopes:
            return False, f"blocked: scope '{scope}' not granted"
        return True, "allowed"

    # ── reads ───────────────────────────────────────────────────────────────
    def get(self, eid: str) -> Entry | None:
        return self._by_id.get(eid)

    def list(self) -> list[dict]:
        return [e.view() for e in self._by_id.values()]

    # ── helpers ─────────────────────────────────────────────────────────────
    def _make_id(self, name: str) -> str:
        slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "agent"
        return f"{slug}-{uuid.uuid4().hex[:6]}"

    # ── persistence (survive a server restart) ──────────────────────────────
    @staticmethod
    def _entry_to_state(e: Entry) -> dict:
        return {"id": e.id, "card": e.card, "awcp": e.awcp, "intake": e.intake,
                "status": e.status, "granted_scopes": e.granted_scopes,
                "verified": e.verified, "first_seen": e.first_seen,
                "heartbeat_at": e.heartbeat_at}

    def _persist(self) -> None:
        try:
            data = [self._entry_to_state(e) for e in self._by_id.values()]
            with open(STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
        except Exception:
            return
        for s in data:
            self._by_id[s["id"]] = Entry(
                id=s["id"], card=s["card"], awcp=s["awcp"], intake=s["intake"],
                status=s.get("status", STATUS_QUARANTINED),
                granted_scopes=s.get("granted_scopes", []),
                verified=s.get("verified", False),
                first_seen=s.get("first_seen", time.time()),
                heartbeat_at=s.get("heartbeat_at", 0.0))


# Module-level singleton (the server imports this).
REGISTRY = Registry()
