"""In-memory registry with Postgres-exclusive durable persistence + scan reconciliation.

The live working set stays in memory (a dict guarded by one lock — enough for the
single background scanner thread plus the FastAPI request threads). Durability is
**Postgres only** — the canonical control-plane DB. The registry is mirrored to the
registry.agents table defined in observability/init-db, so a restart restores
self-registered agents from the DB. There is NO JSON fallback: data is never
written to a local file. If Postgres is unreachable, the registry simply runs in
memory (no persistence) and logs a warning — it never falls back to JSON.

Only self-registered entries survive a restart (scanned processes are re-detected
live). NOTE: approval_state / approval_reason are not persisted — registry.agents
has no such columns; the operator-approval gate lives in governance.approval_tokens.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Iterable

from awcp.radar.models import AgentEntry

log = logging.getLogger("awcp.radar")

# Canonical control-plane DB — the ONLY persistence backend. When set and
# reachable the registry persists to registry.agents; there is no JSON fallback.
DATABASE_URL = os.getenv("AGENT_RADAR_DATABASE_URL", "").strip()
# A scanned process that disappears is pruned after this many seconds.
PRUNE_AFTER_SEC = float(os.getenv("AGENT_RADAR_PRUNE_AFTER", "60"))
# A self-registered agent is kept alive by its heartbeat (periodic re-register)
# or by being seen in a scan. Once neither happens for this long it is pruned —
# this stops restarted agents (new pid -> new id) from accumulating forever.
SELF_PRUNE_AFTER_SEC = float(os.getenv("AGENT_RADAR_SELF_PRUNE_AFTER", "180"))


# ── Postgres persistence (registry.agents) ────────────────────────────────────
# Raw SQL via SQLAlchemy Core so the table's exact columns/types (text[], jsonb,
# timestamptz) are honored without re-declaring an ORM model that could drift from
# the canonical init-db schema. The AgentEntry.user field maps to the os_user
# column; epoch floats map to timestamptz via to_timestamp() / EXTRACT(EPOCH ...).
_PG_COLUMNS = (
    "id", "name", "kind", "framework", "source", "status", "quarantine_reason",
    "autonomy_profile", "autonomy_reason", "failure_count",
    "owner", "runtime", "version", "write_scopes", "feature_flags",
    "flags_observed", "last_flags_ts", "telemetry_enabled", "last_telemetry_ts",
    "policy_callbacks", "policy_observed", "last_policy_ts",
    "risk", "autonomy_ladder", "failure_budget", "token_budget",
    "endpoint", "transport", "capabilities", "control_endpoint",
    "pid", "os_user", "cwd", "cmdline", "detected_via",
    "onboarding_state", "onboarding_workflow_id",
    "first_seen", "last_seen", "alive",
)
# columns that need a value expression other than a plain :param bind
_PG_VALUE_EXPR = {
    "feature_flags": "CAST(:feature_flags AS jsonb)",
    "last_flags_ts": "to_timestamp(:last_flags_ts)",
    "last_telemetry_ts": "to_timestamp(:last_telemetry_ts)",
    "last_policy_ts": "to_timestamp(:last_policy_ts)",
    "first_seen": "to_timestamp(:first_seen)",
    "last_seen": "to_timestamp(:last_seen)",
}


def _pg_upsert_sql() -> str:
    cols = ", ".join(_PG_COLUMNS)
    vals = ", ".join(_PG_VALUE_EXPR.get(c, f":{c}") for c in _PG_COLUMNS)
    # id is the conflict key; created_at is DB-managed; updated_at = now()
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in _PG_COLUMNS if c != "id")
    return (
        f"INSERT INTO registry.agents ({cols}, updated_at) "
        f"VALUES ({vals}, now()) "
        f"ON CONFLICT (id) DO UPDATE SET {sets}, updated_at=now()"
    )


def _pg_select_sql() -> str:
    # map timestamptz back to epoch floats; only self-registered survive a restart
    sel = []
    for c in _PG_COLUMNS:
        if c in ("last_flags_ts", "last_telemetry_ts", "last_policy_ts",
                 "first_seen", "last_seen"):
            sel.append(f"EXTRACT(EPOCH FROM {c}) AS {c}")
        else:
            sel.append(c)
    return f"SELECT {', '.join(sel)} FROM registry.agents WHERE source='self'"


def _row_params(e: AgentEntry) -> dict:
    d = e.model_dump()
    return {
        "id": d["id"], "name": d["name"], "kind": d["kind"],
        "framework": d["framework"], "source": d["source"], "status": d["status"],
        "quarantine_reason": d["quarantine_reason"],
        "autonomy_profile": d["autonomy_profile"], "autonomy_reason": d["autonomy_reason"],
        "failure_count": d["failure_count"],
        "owner": d["owner"], "runtime": d["runtime"], "version": d["version"],
        "write_scopes": list(d["write_scopes"] or []),
        "feature_flags": json.dumps(d["feature_flags"] or {}),
        "flags_observed": d["flags_observed"], "last_flags_ts": d["last_flags_ts"],
        "telemetry_enabled": d["telemetry_enabled"], "last_telemetry_ts": d["last_telemetry_ts"],
        "policy_callbacks": list(d["policy_callbacks"] or []),
        "policy_observed": d["policy_observed"], "last_policy_ts": d["last_policy_ts"],
        "risk": d["risk"], "autonomy_ladder": list(d["autonomy_ladder"] or []),
        "failure_budget": d["failure_budget"], "token_budget": d["token_budget"],
        "endpoint": d["endpoint"], "transport": d["transport"],
        "capabilities": list(d["capabilities"] or []), "control_endpoint": d["control_endpoint"],
        "pid": d["pid"], "os_user": d["user"], "cwd": d["cwd"],
        "cmdline": d["cmdline"], "detected_via": d["detected_via"],
        "onboarding_state": d["onboarding_state"], "onboarding_workflow_id": d["onboarding_workflow_id"],
        "first_seen": d["first_seen"], "last_seen": d["last_seen"], "alive": d["alive"],
    }


def _entry_from_row(row: dict) -> AgentEntry:
    d = dict(row)
    d["user"] = d.pop("os_user", None)               # column -> model field
    for k in ("last_flags_ts", "last_telemetry_ts", "last_policy_ts",
              "first_seen", "last_seen"):
        if d.get(k) is not None:
            d[k] = float(d[k])                       # Decimal/epoch -> float
    return AgentEntry(**d)


class _PgBackend:
    """Postgres mirror of the registry. Built lazily; any failure (no URL, no
    driver, unreachable DB, missing table) leaves it disabled and the Registry
    falls back to the JSON file — durability never breaks the radar."""

    def __init__(self) -> None:
        self.ok = False
        self._engine = None
        self._upsert = ""
        self._select = ""
        if not DATABASE_URL:
            return
        try:
            from sqlalchemy import create_engine, text
            self._text = text
            eng = create_engine(
                DATABASE_URL, pool_pre_ping=True, pool_recycle=300,
                connect_args={"connect_timeout": 3}
                if DATABASE_URL.startswith(("postgresql", "postgres")) else {},
            )
            with eng.connect() as c:                 # require the canonical table
                c.execute(text("SELECT 1 FROM registry.agents LIMIT 1"))
            self._engine = eng
            self._upsert = _pg_upsert_sql()
            self._select = _pg_select_sql()
            self.ok = True
            log.info("radar.store persistence=postgres (registry.agents) at %s",
                     DATABASE_URL.split("@")[-1])
        except Exception as exc:  # noqa: BLE001 — fall back to JSON
            log.warning("radar.store postgres unavailable (%r) — using JSON file", exc)

    def load_self(self) -> list[AgentEntry]:
        out: list[AgentEntry] = []
        with self._engine.connect() as c:
            for row in c.execute(self._text(self._select)).mappings():
                try:
                    out.append(_entry_from_row(row))
                except Exception as exc:  # noqa: BLE001 — skip a bad row, keep the rest
                    log.warning("radar.store skip bad registry row: %r", exc)
        return out

    def sync(self, entries: list[AgentEntry]) -> None:
        ids = [e.id for e in entries]
        with self._engine.begin() as c:
            for e in entries:
                c.execute(self._text(self._upsert), _row_params(e))
            if ids:
                c.execute(self._text("DELETE FROM registry.agents WHERE id <> ALL(:ids)"),
                          {"ids": ids})
            else:
                c.execute(self._text("DELETE FROM registry.agents"))


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, AgentEntry] = {}
        self._lock = threading.Lock()
        self.scan_count = 0
        self._pg = _PgBackend()
        self._load()

    # ---- persistence (Postgres only — no JSON fallback) --------------------
    def _load(self) -> None:
        if not self._pg.ok:
            log.warning("radar.store persistence DISABLED — Postgres unavailable; "
                        "registry runs in memory only (no JSON fallback)")
            return
        try:
            for entry in self._pg.load_self():
                self._entries[entry.id] = entry
        except Exception as exc:  # noqa: BLE001 — never crash startup on a load error
            log.warning("radar.store postgres load failed (%r) — starting empty", exc)

    def _persist(self) -> None:
        if not self._pg.ok:
            return  # no DB -> in-memory only; data is never written to JSON
        try:
            self._pg.sync(list(self._entries.values()))
        except Exception as exc:  # noqa: BLE001 — never break a request on persistence
            log.warning("radar.store postgres persist failed (%r)", exc)

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
