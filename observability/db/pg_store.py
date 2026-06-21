"""Postgres-backed registry bridge — a drop-in mirror of radar/store.py:Registry.

The live radar (src/awcp/radar/store.py) keeps an in-memory dict persisted to
agent_radar_registry.json. This class exposes the SAME method surface
(all / get / patch / register / remove / reconcile_scan) but reads/writes
registry.agents in Postgres instead.

It is intentionally standalone (no import of awcp.*) so it can be exercised
without the project's package on sys.path, and so wiring it into the radar later
is a one-line swap (REGISTRY = PgRegistry()) with zero edits elsewhere.

Rows are returned as plain dicts (same keys as the JSON entries, including the
JSON-style `user` alias derived from the os_user column) so existing consumers
that did e.attr can be adapted, or callers can read dict keys directly.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from connection import connect
from migrate_json_to_pg import COLUMNS, build_upsert, to_row, _ts

PRUNE_AFTER_SEC = 60.0
SELF_PRUNE_AFTER_SEC = 180.0


def _row_to_entry(r: dict) -> dict:
    """Normalise a DB row to the JSON-memory shape (os_user -> user, ts->epoch)."""
    if r is None:
        return None
    out = dict(r)
    out["user"] = out.pop("os_user", None)
    for k in ("first_seen", "last_seen", "last_flags_ts", "last_telemetry_ts",
              "last_policy_ts", "created_at", "updated_at"):
        v = out.get(k)
        if hasattr(v, "timestamp"):
            out[k] = v.timestamp()
    return out


class PgRegistry:
    def __init__(self) -> None:
        self.scan_count = 0
        self._upsert_sql = build_upsert()

    # ---- reads -------------------------------------------------------------
    def all(self) -> list[dict]:
        with connect() as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM registry.agents "
                "ORDER BY source, coalesce(framework,''), name"
            )
            return [_row_to_entry(r) for r in cur.fetchall()]

    def get(self, agent_id: str) -> dict | None:
        with connect() as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM registry.agents WHERE id = %s", (agent_id,))
            return _row_to_entry(cur.fetchone())

    # ---- writes ------------------------------------------------------------
    def patch(self, agent_id: str, **fields) -> dict | None:
        existing = self.get(agent_id)
        if not existing:
            return None
        existing.update(fields)
        return self._upsert(existing)

    def register(self, entry: dict) -> dict:
        """Self-registration upsert (keeps original first_seen if present)."""
        prior = self.get(entry["id"]) if entry.get("id") else None
        if prior and prior.get("first_seen"):
            entry["first_seen"] = prior["first_seen"]
        return self._upsert(entry)

    def remove(self, agent_id: str) -> bool:
        with connect() as c, c.cursor() as cur:
            cur.execute("DELETE FROM registry.agents WHERE id = %s", (agent_id,))
            return cur.rowcount > 0

    def reconcile_scan(self, detected: Iterable[dict]) -> None:
        """Upsert detected procs, age out/prune the ones no longer seen."""
        now = time.time()
        seen: set[str] = set()
        self.scan_count += 1
        for d in detected:
            seen.add(d["id"])
            self._upsert(d)
        with connect() as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, source, extract(epoch FROM last_seen) AS ls "
                        "FROM registry.agents")
            for r in cur.fetchall():
                if r["id"] in seen:
                    continue
                age = now - (r["ls"] or 0)
                limit = PRUNE_AFTER_SEC if r["source"] == "scan" else SELF_PRUNE_AFTER_SEC
                if age > limit:
                    cur.execute("DELETE FROM registry.agents WHERE id = %s", (r["id"],))
                else:
                    cur.execute("UPDATE registry.agents SET alive = false "
                                "WHERE id = %s AND %s > %s",
                                (r["id"], age, PRUNE_AFTER_SEC))

    # ---- internal ----------------------------------------------------------
    def _upsert(self, entry: dict) -> dict:
        # accept either JSON-memory shape (user) or SQL shape (os_user)
        e = dict(entry)
        if "os_user" not in e and "user" in e:
            e["user"] = e.get("user")
        row = to_row(e)
        with connect() as c, c.cursor() as cur:
            cur.execute(self._upsert_sql, row)
        return self.get(entry["id"])


REGISTRY = PgRegistry()


if __name__ == "__main__":
    reg = PgRegistry()
    rows = reg.all()
    print(f"[pg_store] registry.agents -> {len(rows)} entries")
    for e in rows:
        print(f"  - {e['id']:<28} {e['name']:<16} "
              f"src={e['source']:<5} risk={e['risk']:<6} status={e['status']}")
