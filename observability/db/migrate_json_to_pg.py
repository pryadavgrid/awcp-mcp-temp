"""Migrate the JSON radar memory -> Postgres registry.agents.

Source : awcp-mcp-temp/agent_radar_registry.json  (the old JSON "memory")
Target : registry.agents  (init-db/02-schema.sql)

This is idempotent: every agent is UPSERTed by primary key (id), so re-running
the migration re-syncs rather than duplicating. Read-only against the JSON file;
it never edits the source.

Field alignment notes (JSON model -> SQL column):
  - JSON `user`            -> column `os_user`   (the one true rename)
  - epoch floats           -> timestamptz via to_timestamp()
      first_seen, last_seen, last_flags_ts, last_telemetry_ts, last_policy_ts
  - list[str]              -> text[]   (write_scopes, policy_callbacks,
                                        capabilities, autonomy_ladder)
  - dict                   -> jsonb    (feature_flags)
  - created_at/updated_at  -> left to schema defaults (not in JSON)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from connection import connect, safe_dsn

DEFAULT_JSON = os.path.join(
    os.path.dirname(__file__), "..", "..", "agent_radar_registry.json"
)

# columns we write, in order. Keys are the *SQL column* names.
COLUMNS = [
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
]

# SQL columns that come from an epoch-float in the JSON
TS_COLUMNS = {
    "first_seen", "last_seen", "last_flags_ts", "last_telemetry_ts", "last_policy_ts",
}


def _ts(v):
    if v is None:
        return None
    return datetime.fromtimestamp(float(v), tz=timezone.utc)


def to_row(a: dict) -> dict:
    """Map one JSON agent dict to a dict keyed by SQL column name."""
    row = {
        "id": a["id"],
        "name": a.get("name") or a["id"],
        "kind": a.get("kind", "agent_framework"),
        "framework": a.get("framework"),
        "source": a.get("source", "scan"),
        "status": a.get("status", "quarantined"),
        "quarantine_reason": a.get("quarantine_reason"),
        "autonomy_profile": a.get("autonomy_profile", "active"),
        "autonomy_reason": a.get("autonomy_reason"),
        "failure_count": a.get("failure_count", 0),
        "owner": a.get("owner"),
        "runtime": a.get("runtime"),
        "version": a.get("version"),
        "write_scopes": a.get("write_scopes", []),
        "feature_flags": Jsonb(a.get("feature_flags", {})),
        "flags_observed": a.get("flags_observed", False),
        "last_flags_ts": _ts(a.get("last_flags_ts")),
        "telemetry_enabled": a.get("telemetry_enabled", False),
        "last_telemetry_ts": _ts(a.get("last_telemetry_ts")),
        "policy_callbacks": a.get("policy_callbacks", []),
        "policy_observed": a.get("policy_observed", False),
        "last_policy_ts": _ts(a.get("last_policy_ts")),
        "risk": a.get("risk", "medium"),
        "autonomy_ladder": a.get("autonomy_ladder", []),
        "failure_budget": a.get("failure_budget"),
        "token_budget": a.get("token_budget"),
        "endpoint": a.get("endpoint"),
        "transport": a.get("transport"),
        "capabilities": a.get("capabilities", []),
        "control_endpoint": a.get("control_endpoint"),
        "pid": a.get("pid"),
        "os_user": a.get("user"),          # <-- the rename
        "cwd": a.get("cwd"),
        "cmdline": a.get("cmdline"),
        "detected_via": a.get("detected_via"),
        "onboarding_state": a.get("onboarding_state"),
        "onboarding_workflow_id": a.get("onboarding_workflow_id"),
        "first_seen": _ts(a.get("first_seen")),
        "last_seen": _ts(a.get("last_seen")),
        "alive": a.get("alive", True),
    }
    return row


def build_upsert() -> str:
    cols = ", ".join(COLUMNS)
    placeholders = ", ".join(f"%({c})s" for c in COLUMNS)
    # update everything except id; bump updated_at
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "id")
    return (
        f"INSERT INTO registry.agents ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = now()"
    )


def migrate(json_path: str) -> int:
    with open(json_path) as f:
        data = json.load(f)
    agents = data.get("agents", [])
    rows = [to_row(a) for a in agents]
    sql = build_upsert()
    with connect() as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(sql, r)
            count = cur.execute("SELECT count(*) FROM registry.agents").fetchone()[0]
    return count, len(rows)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(DEFAULT_JSON)
    print(f"[migrate] DSN     : {safe_dsn()}")
    print(f"[migrate] source  : {path}")
    if not os.path.exists(path):
        raise SystemExit(f"[migrate] source JSON not found: {path}")
    total, migrated = migrate(path)
    print(f"[migrate] upserted: {migrated} agent(s) from JSON")
    print(f"[migrate] registry.agents now holds: {total} row(s)")


if __name__ == "__main__":
    main()
