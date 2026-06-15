"""In-memory registry with JSON persistence and scan reconciliation.

Thread-safe enough for one background scanner thread + the FastAPI request
threads (a single lock guards all mutations).
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Iterable

from awcp.radar.models import AgentEntry

# Where self-registered entries are persisted (scanned entries are re-derived).
PERSIST_PATH = os.getenv(
    "AGENT_RADAR_DB",
    os.path.join(os.getcwd(), "agent_radar_registry.json"),
)
# A scanned process that disappears is pruned after this many seconds.
PRUNE_AFTER_SEC = float(os.getenv("AGENT_RADAR_PRUNE_AFTER", "60"))
# A self-registered agent is kept alive by its heartbeat (periodic re-register)
# or by being seen in a scan. Once neither happens for this long it is pruned —
# this stops restarted agents (new pid -> new id) from accumulating forever.
SELF_PRUNE_AFTER_SEC = float(os.getenv("AGENT_RADAR_SELF_PRUNE_AFTER", "180"))


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, AgentEntry] = {}
        self._lock = threading.Lock()
        self.scan_count = 0
        self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(PERSIST_PATH):
            return
        try:
            with open(PERSIST_PATH) as f:
                raw = json.load(f)
            for item in raw.get("agents", []):
                entry = AgentEntry(**item)
                # only self-registered entries survive a restart; scanned ones
                # are re-detected live.
                if entry.source == "self":
                    self._entries[entry.id] = entry
        except Exception:
            pass

    def _persist(self) -> None:
        try:
            tmp = PERSIST_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(
                    {"agents": [e.model_dump() for e in self._entries.values()]},
                    f,
                    indent=2,
                )
            os.replace(tmp, PERSIST_PATH)
        except Exception:
            pass

    # ---- reads -------------------------------------------------------------
    def all(self) -> list[AgentEntry]:
        with self._lock:
            return sorted(
                self._entries.values(),
                key=lambda e: (e.source, e.framework or "", e.name),
            )

    def get(self, agent_id: str) -> AgentEntry | None:
        with self._lock:
            return self._entries.get(agent_id)

    # ---- writes ------------------------------------------------------------
    def patch(self, agent_id: str, **fields) -> AgentEntry | None:
        """Apply field updates to an existing entry (used by onboarding)."""
        with self._lock:
            e = self._entries.get(agent_id)
            if not e:
                return None
            data = e.model_dump()
            data.update(fields)
            updated = AgentEntry(**data)
            self._entries[agent_id] = updated
            self._persist()
            return updated

    def remove(self, agent_id: str) -> bool:
        """Operator action — forget an entry entirely (registry hygiene).
        A scanned process that is still alive will simply be re-detected on the
        next scan; a self/stale entry stays gone."""
        with self._lock:
            existed = self._entries.pop(agent_id, None) is not None
            if existed:
                self._persist()
            return existed

    def register(self, entry: AgentEntry) -> AgentEntry:
        """Self-registration upsert."""
        with self._lock:
            existing = self._entries.get(entry.id)
            if existing:
                entry.first_seen = existing.first_seen
            self._entries[entry.id] = entry
            self._persist()
            return entry

    def reconcile_scan(self, detected: Iterable[AgentEntry]) -> None:
        """Merge a fresh scan: upsert detected procs, age out gone ones."""
        now = time.time()
        seen_ids: set[str] = set()
        with self._lock:
            self.scan_count += 1
            for d in detected:
                seen_ids.add(d.id)
                existing = self._entries.get(d.id)
                if existing and existing.source == "scan":
                    self._entries[d.id] = existing.merged_from_scan(d)
                elif existing and existing.source == "self":
                    # don't clobber a self-registered entry; just touch liveness
                    existing.last_seen = now
                    existing.alive = True
                else:
                    self._entries[d.id] = d

            # age out entries no longer present / no longer heartbeating
            for aid, e in list(self._entries.items()):
                if aid in seen_ids:
                    continue  # detected live in this scan
                if e.source == "scan":
                    # a scanned process that disappeared
                    e.alive = False
                    if now - e.last_seen > PRUNE_AFTER_SEC:
                        del self._entries[aid]
                else:
                    # a self-registered agent: live only while it keeps
                    # heartbeating (re-registering). Stale heartbeat -> dead,
                    # then prune. last_seen is refreshed both by the agent's
                    # heartbeat and by a scan that detects its process, so a
                    # running agent never goes stale; a stopped one does.
                    if now - e.last_seen > SELF_PRUNE_AFTER_SEC:
                        del self._entries[aid]
                    elif now - e.last_seen > PRUNE_AFTER_SEC:
                        e.alive = False

            self._persist()


# module-level singleton
REGISTRY = Registry()
