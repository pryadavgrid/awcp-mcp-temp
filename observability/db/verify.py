"""End-to-end verification: service ports + Postgres storage.

Run after migrate + seed. Prints a PASS/FAIL line per check and exits non-zero
if any hard check fails. Port checks are best-effort (a down optional service is
reported WARN, not FAIL); the Postgres storage checks are the hard ones.
"""

from __future__ import annotations

import socket
import sys

from psycopg.rows import dict_row

from connection import connect, safe_dsn

# (label, host, port, hard?) — hard means a failure fails the whole run
PORTS = [
    ("awcp-postgres (canonical)", "127.0.0.1", 5432, False),
    ("awcp-postgres (fallback)", "127.0.0.1", 55432, True),
    ("grafana", "127.0.0.1", 3000, False),
    ("prometheus", "127.0.0.1", 9090, False),
    ("loki", "127.0.0.1", 3100, False),
    ("tempo", "127.0.0.1", 3200, False),
    ("otel-collector gRPC", "127.0.0.1", 4317, False),
    ("temporal engine", "127.0.0.1", 7233, False),
    ("temporal-ui", "127.0.0.1", 8080, False),
    ("laminar UI", "127.0.0.1", 5667, False),
]

EXPECTED_TABLES = [
    ("registry", "agents"),
    ("registry", "freeze_journal"),
    ("registry", "gateway_agents"),
    ("governance", "approval_tokens"),
    ("governance", "policy_decisions"),
    ("governance", "degradation_events"),
    ("evidence", "token_ledger"),
    ("evidence", "ledger"),
    ("ops", "onboarding_runs"),
    ("ops", "artifacts"),
]


def _port_open(host: str, port: int, timeout=1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ports() -> bool:
    print("\n== service ports ==")
    ok = True
    for label, host, port, hard in PORTS:
        up = _port_open(host, port)
        tag = "PASS" if up else ("FAIL" if hard else "WARN")
        if hard and not up:
            ok = False
        print(f"  [{tag}] {label:<28} {host}:{port}")
    return ok


def check_storage() -> bool:
    print("\n== postgres storage ==")
    print(f"  dsn: {safe_dsn()}")
    ok = True
    with connect() as c, c.cursor(row_factory=dict_row) as cur:
        # every expected table exists + is queryable
        for schema, table in EXPECTED_TABLES:
            cur.execute(
                "SELECT to_regclass(%s) AS t", (f"{schema}.{table}",)
            )
            exists = cur.fetchone()["t"] is not None
            n = None
            if exists:
                cur.execute(f"SELECT count(*) AS n FROM {schema}.{table}")
                n = cur.fetchone()["n"]
            tag = "PASS" if exists else "FAIL"
            if not exists:
                ok = False
            print(f"  [{tag}] {schema}.{table:<22} rows={n}")

        # roles present
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('awcp_app','awcp_ro')")
        roles = {r["rolname"] for r in cur.fetchall()}
        for r in ("awcp_app", "awcp_ro"):
            tag = "PASS" if r in roles else "FAIL"
            if r not in roles:
                ok = False
            print(f"  [{tag}] role {r}")

        # storage durability proof: agents actually migrated
        cur.execute("SELECT count(*) AS n FROM registry.agents")
        agents = cur.fetchone()["n"]
        tag = "PASS" if agents > 0 else "FAIL"
        if agents == 0:
            ok = False
        print(f"  [{tag}] registry.agents migrated rows = {agents}")

        # partition routing works (June 2026 partition holds the seeded rows)
        cur.execute("SELECT count(*) AS n FROM evidence.token_ledger_2026_06")
        part = cur.fetchone()["n"]
        print(f"  [INFO] evidence.token_ledger_2026_06 partition rows = {part}")
    return ok


def main() -> None:
    ports_ok = check_ports()
    storage_ok = check_storage()
    print("\n== summary ==")
    print(f"  ports   : {'OK' if ports_ok else 'FAIL (hard)'}")
    print(f"  storage : {'OK' if storage_ok else 'FAIL'}")
    sys.exit(0 if (ports_ok and storage_ok) else 1)


if __name__ == "__main__":
    main()
