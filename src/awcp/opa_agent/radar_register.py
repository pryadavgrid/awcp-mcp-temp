"""Make the OPA agent VISIBLE on the control-plane radar.

The OPA agent is hidden infra (not a worker agent), but operators want to SEE that
it is running, alongside every other agent on the radar. So it self-registers and
heartbeats to stay live — the exact pattern the worker-agent kit uses: register
once, refresh liveness via `/agents/{id}/signal` (which does NOT re-onboard), and
re-register if the radar pruned/forgot it.

It registers as a normal entry (kind=agent_framework, framework=opa) so it shows in
`GET /agents` and the Radar table. This does NOT add it to the user-UI picker —
`/user/agents` is filesystem-based, so the OPA agent stays infra there.

Env-driven; nothing hardcoded. `OPA_RADAR_REGISTER=false` turns it off (the agent
goes back to fully hidden).
"""

from __future__ import annotations

import os
import threading
import time

import httpx


class RadarPresence:
    """Self-register the OPA agent with the radar and keep it alive (background)."""

    def __init__(self, *, port: int, framework: str = "opa",
                 tier_model: str = "", capabilities: list[str] | None = None) -> None:
        self.enabled = os.getenv("OPA_RADAR_REGISTER", "true").strip().lower() == "true"
        # The radar lives on the gateway; reuse whatever URL the agent already knows.
        self.url = (os.getenv("OPA_RADAR_URL")
                    or os.getenv("AWCP_GATEWAY_URL")
                    or os.getenv("AWCP_RADAR_URL")
                    or "http://localhost:8000").rstrip("/")
        self.agent_id = os.getenv("OPA_RADAR_AGENT_ID", "agent-opa")
        self.name = os.getenv("OPA_RADAR_NAME", "OPA Agent")
        self.framework = os.getenv("OPA_RADAR_FRAMEWORK", framework)
        self.owner = (os.getenv("OPA_RADAR_OWNER")
                      or os.getenv("USER") or os.getenv("LOGNAME") or "")
        self.risk = os.getenv("OPA_RADAR_RISK", "low")
        # Heartbeat well under the radar's PRUNE window (alive→False at 60s, pruned
        # at 180s by default), so a running OPA agent never goes stale.
        self.heartbeat = float(os.getenv("OPA_RADAR_HEARTBEAT", "30"))
        self.timeout = float(os.getenv("OPA_RADAR_TIMEOUT", "3"))
        self.endpoint = os.getenv("OPA_RADAR_ENDPOINT", f"http://localhost:{port}")
        self.tier_model = tier_model
        self.capabilities = capabilities or []

    def _payload(self) -> dict:
        return {
            "id": self.agent_id,
            "name": self.name,
            "kind": "agent_framework",
            "framework": self.framework,
            "runtime": "python",
            "endpoint": self.endpoint,
            "transport": "http",
            # Declared control hooks so onboarding admits it as active (same as the
            # worker-agent kit). It IS a policy decision point, so it genuinely has
            # a policy callback.
            "telemetry_enabled": True,
            "policy_callbacks": [f"{self.endpoint}/health"],
            "feature_flags": {"slm_tiering": True},
            "risk": self.risk,
            "write_scopes": [],                  # the PDP writes nothing
            "owner": self.owner,
            "extra": {"role": "tool-call PDP (SLM risk tiering)",
                      "tier_model": self.tier_model,
                      "capabilities": self.capabilities},
        }

    def _register(self) -> bool:
        try:
            r = httpx.post(f"{self.url}/agents/register", json=self._payload(),
                           timeout=self.timeout)
            r.raise_for_status()
            return bool(r.json().get("id"))
        except Exception:                        # noqa: BLE001 — never crash the agent
            return False

    def _signal_alive(self) -> bool:
        """Heartbeat: a success signal refreshes liveness without re-onboarding.
        Empty/failed ⇒ the radar forgot us (pruned/restarted) ⇒ caller re-registers."""
        try:
            r = httpx.post(f"{self.url}/agents/{self.agent_id}/signal",
                           json={"ok": True, "reason": "heartbeat"}, timeout=self.timeout)
            r.raise_for_status()
            return bool(r.json())
        except Exception:                        # noqa: BLE001
            return False

    def _loop(self) -> None:
        # Register first — retry until the radar (gateway) is up.
        while self.enabled and not self._register():
            time.sleep(self.heartbeat)
        while self.enabled:
            time.sleep(self.heartbeat)
            if not self._signal_alive():
                self._register()

    def start(self) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._loop, name="opa-radar-presence",
                         daemon=True).start()
