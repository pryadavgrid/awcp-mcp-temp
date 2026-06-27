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
# Owner connection used ONLY for the additive AgentCard column migration (DDL the
# least-privileged app role can't do). Falls back to DATABASE_URL. Same pattern as
# awcp.radar.db.DB_ADMIN_URL / ensure_operator_policy_table.
DB_ADMIN_URL = os.getenv("AGENT_RADAR_DB_ADMIN_URL", "").strip() or DATABASE_URL
# A scanned process / self-registered agent that stops being seen is marked
# DEAD after this many seconds (it is no longer pruned — it stays on the radar
# as "stop"; see reconcile_scan). Liveness only; removal is operator-driven.
PRUNE_AFTER_SEC = float(os.getenv("AGENT_RADAR_PRUNE_AFTER", "60"))
# Fold a scanned process row INTO the self-registered agent that is the same
# process — matched by pid, else by a shared endpoint / AgentCard url — so the same
# agent is not listed twice (the scan sighting + its self-registration). On by
# default; AGENT_RADAR_DEDUP_SCAN=false restores the old (always-two-rows) behavior.
DEDUP_SCAN = os.getenv("AGENT_RADAR_DEDUP_SCAN", "true").lower() == "true"


def _norm_url(u: str | None) -> str | None:
    """A comparable form of an endpoint / card url (trim trailing slash, lowercase)."""
    s = (u or "").strip().rstrip("/").lower()
    return s or None


def _entry_url(e: AgentEntry) -> str | None:
    """The agent's identity url: its declared endpoint, else its card's `url`."""
    return _norm_url(getattr(e, "endpoint", None)) or _norm_url((e.card or {}).get("url"))
# DEPRECATED: stopped agents are no longer auto-pruned, so this no longer drives
# any deletion. Kept only so the old env var doesn't error if still set.
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
    # AgentCard (A2A description layer — additive)
    "card", "card_url", "card_fetched_at", "skills",
)
# columns that need a value expression other than a plain :param bind
_PG_VALUE_EXPR = {
    "feature_flags": "CAST(:feature_flags AS jsonb)",
    "last_flags_ts": "to_timestamp(:last_flags_ts)",
    "last_telemetry_ts": "to_timestamp(:last_telemetry_ts)",
    "last_policy_ts": "to_timestamp(:last_policy_ts)",
    "first_seen": "to_timestamp(:first_seen)",
    "last_seen": "to_timestamp(:last_seen)",
    "card": "CAST(:card AS jsonb)",
    "card_fetched_at": "to_timestamp(:card_fetched_at)",
}


# AgentCard columns are additive — on an already-initialised DB they may be absent
# until the migration runs. The backend computes the EFFECTIVE column set at
# startup (base columns + whichever card columns actually exist) so persistence of
# the base record never breaks if the card migration couldn't be applied.
_CARD_COLUMNS = ("card", "card_url", "card_fetched_at", "skills")


def _pg_upsert_sql(cols: tuple[str, ...]) -> str:
    colstr = ", ".join(cols)
    vals = ", ".join(_PG_VALUE_EXPR.get(c, f":{c}") for c in cols)
    # id is the conflict key; created_at is DB-managed; updated_at = now()
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "id")
    return (
        f"INSERT INTO registry.agents ({colstr}, updated_at) "
        f"VALUES ({vals}, now()) "
        f"ON CONFLICT (id) DO UPDATE SET {sets}, updated_at=now()"
    )


def _pg_select_sql(cols: tuple[str, ...]) -> str:
    # map timestamptz back to epoch floats; only self-registered survive a restart
    sel = []
    for c in cols:
        if c in ("last_flags_ts", "last_telemetry_ts", "last_policy_ts",
                 "first_seen", "last_seen", "card_fetched_at"):
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
        # AgentCard columns (card stored as JSON text -> CAST to jsonb in the SQL)
        "card": json.dumps(d["card"]) if d.get("card") else None,
        "card_url": d.get("card_url"),
        "card_fetched_at": d.get("card_fetched_at"),
        "skills": list(d.get("skills") or []),
    }


def _entry_from_row(row: dict) -> AgentEntry:
    d = dict(row)
    d["user"] = d.pop("os_user", None)               # column -> model field
    for k in ("last_flags_ts", "last_telemetry_ts", "last_policy_ts",
              "first_seen", "last_seen", "card_fetched_at"):
        if d.get(k) is not None:
            d[k] = float(d[k])                       # Decimal/epoch -> float
    # JSONB sometimes round-trips as a string depending on the driver — normalize
    # the card back to a dict so AgentEntry.card stays dict | None.
    if isinstance(d.get("card"), str):
        try:
            d["card"] = json.loads(d["card"])
        except Exception:  # noqa: BLE001 — a corrupt card blob shouldn't drop the row
            d["card"] = None
    return AgentEntry(**d)


def _migrate_card_columns() -> None:
    """Best-effort: add the additive AgentCard columns to registry.agents IF NOT
    EXISTS, via the owner connection (mirrors db.ensure_operator_policy_table). On
    an already-initialised volume the canonical init-db SQL won't re-run, so this
    lets the live DB pick up the columns. Failure is tolerated — the backend then
    simply omits any card column that doesn't exist, so base persistence is intact."""
    if not DB_ADMIN_URL:
        return
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(DB_ADMIN_URL, connect_args={"connect_timeout": 3}
                            if DB_ADMIN_URL.startswith(("postgresql", "postgres")) else {})
        with eng.begin() as c:
            c.execute(text(
                "ALTER TABLE registry.agents "
                " ADD COLUMN IF NOT EXISTS card            jsonb,"
                " ADD COLUMN IF NOT EXISTS card_url        text,"
                " ADD COLUMN IF NOT EXISTS card_fetched_at timestamptz,"
                " ADD COLUMN IF NOT EXISTS skills          text[] NOT NULL DEFAULT '{}'"))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_agents_skills_gin "
                           "ON registry.agents USING gin (skills)"))
        eng.dispose()
        log.info("radar.store AgentCard columns ensured on registry.agents")
    except Exception as exc:  # noqa: BLE001 — additive migration is best-effort
        log.warning("radar.store card-column migration skipped (%r) — cards persist "
                    "only if the columns already exist", exc)


def _existing_card_columns(engine, text) -> tuple[str, ...]:
    """Which of the AgentCard columns actually exist on registry.agents right now.
    Used to build the effective column set so a missing migration never breaks the
    base upsert/select."""
    try:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='registry' AND table_name='agents' "
                "  AND column_name = ANY(:cols)"
            ), {"cols": list(_CARD_COLUMNS)}).scalars().all()
        return tuple(col for col in _CARD_COLUMNS if col in set(rows))
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.store card-column probe failed (%r) — omitting card cols", exc)
        return ()


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
            # Apply the additive AgentCard migration, then use only the card columns
            # that exist — so a DB without them still persists the base record.
            _migrate_card_columns()
            base = tuple(c for c in _PG_COLUMNS if c not in _CARD_COLUMNS)
            cols = base + _existing_card_columns(eng, text)
            self._engine = eng
            self._upsert = _pg_upsert_sql(cols)
            self._select = _pg_select_sql(cols)
            self.ok = True
            log.info("radar.store persistence=postgres (registry.agents, %d cols) at %s",
                     len(cols), DATABASE_URL.split("@")[-1])
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

            # Index self-registered agents for dedup correlation: by pid, and by
            # identity url (endpoint / card url). A scanned proc that matches one is
            # the SAME process as a governed agent, so it's folded in rather than
            # listed as its own row.
            by_pid: dict[int, AgentEntry] = {}
            by_url: dict[str, AgentEntry] = {}
            if DEDUP_SCAN:
                for s in self._entries.values():
                    if s.source != "self":
                        continue
                    if s.pid:
                        by_pid[s.pid] = s
                    u = _entry_url(s)
                    if u:
                        by_url[u] = s

            for d in detected:
                # Dedup fold: same pid (primary) or same endpoint/card url
                # (secondary) as a self-registered agent -> treat the scan as a
                # liveness + process-info signal for that agent; do NOT keep a
                # separate proc-* row.
                match = None
                if DEDUP_SCAN:
                    if d.pid and d.pid in by_pid:
                        match = by_pid[d.pid]
                    else:
                        du = _entry_url(d)
                        if du and du in by_url:
                            match = by_url[du]
                if match is not None:
                    match.last_seen = now
                    match.alive = True
                    if match.pid is None and d.pid:
                        match.pid = d.pid
                    # enrich the governed entry with the process facts the scan saw
                    for f in ("cwd", "cmdline", "user", "detected_via"):
                        if not getattr(match, f, None) and getattr(d, f, None):
                            setattr(match, f, getattr(d, f))
                    seen_ids.add(match.id)
                    # drop any stale standalone scan row now represented by the self entry
                    prev = self._entries.get(d.id)
                    if prev is not None and prev.source == "scan":
                        self._entries.pop(d.id, None)
                    continue

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

            # age out entries no longer present / no longer heartbeating.
            # Stopped agents are NEVER pruned automatically — they stay on the
            # radar marked dead (alive=False) so the UI can show them as "stop"
            # (light-red row) instead of making them disappear. Only an explicit
            # operator remove() forgets an entry.
            for aid, e in list(self._entries.items()):
                if aid in seen_ids:
                    continue  # detected live in this scan
                if e.source == "scan":
                    # a scanned process that disappeared -> mark dead, keep it
                    e.alive = False
                else:
                    # a self-registered agent: live only while it keeps
                    # heartbeating (re-registering). Once its heartbeat goes
                    # stale it is marked dead but kept on the radar. last_seen is
                    # refreshed both by the agent's heartbeat and by a scan that
                    # detects its process, so a running agent never goes stale;
                    # a stopped one does and then renders as "stop".
                    if now - e.last_seen > PRUNE_AFTER_SEC:
                        e.alive = False

            self._persist()


# module-level singleton
REGISTRY = Registry()
