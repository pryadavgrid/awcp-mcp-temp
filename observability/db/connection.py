"""Postgres connection helper for the AWCP registry DB.

Single source of truth for how the migration tools + the Postgres-backed
registry bridge reach the `awcp` database created by the observability stack.

Resolution order for connection settings:
  1. AWCP_PG_DSN / DATABASE_URL  (full libpq URL, wins if set)
  2. individual AWCP_PG_* / POSTGRES_* env vars
  3. built-in defaults that match observability/docker-compose.yml

Defaults use host port 55432 — the fallback port published by the
awcp-postgres container so host tools still reach it when a local
(e.g. Homebrew) postgres already owns localhost:5432.
"""

from __future__ import annotations

import os

try:
    import psycopg
except ModuleNotFoundError as exc:  # pragma: no cover - surfaced by setup
    raise SystemExit(
        "psycopg not installed. Run: bash observability/db/setup_venv.sh"
    ) from exc


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


def dsn() -> str:
    """Build a libpq DSN from env, or return the explicit one."""
    explicit = _env("AWCP_PG_DSN", "DATABASE_URL")
    if explicit:
        return explicit

    host = _env("AWCP_PG_HOST", "POSTGRES_HOST", default="127.0.0.1")
    port = _env("AWCP_PG_PORT", "POSTGRES_PORT", default="55432")
    user = _env("AWCP_PG_USER", "POSTGRES_USER", default="awcp")
    password = _env("AWCP_PG_PASSWORD", "POSTGRES_PASSWORD", default="awcppassword")
    db = _env("AWCP_PG_DB", "POSTGRES_DB", default="awcp")
    return f"host={host} port={port} user={user} password={password} dbname={db}"


def connect(autocommit: bool = True) -> "psycopg.Connection":
    """Open a new psycopg connection (autocommit on by default)."""
    conn = psycopg.connect(dsn())
    conn.autocommit = autocommit
    return conn


def safe_dsn() -> str:
    """DSN with the password masked, for logging."""
    d = dsn()
    out = []
    for tok in d.split():
        if tok.lower().startswith("password="):
            out.append("password=***")
        elif "://" in tok and "@" in tok:
            # postgres://user:pass@host -> mask pass
            head, _, tail = tok.partition("@")
            if ":" in head:
                scheme_user, _, _ = head.rpartition(":")
                out.append(f"{scheme_user}:***@{tail}")
            else:
                out.append(tok)
        else:
            out.append(tok)
    return " ".join(out)


if __name__ == "__main__":
    print("DSN:", safe_dsn())
    with connect() as c:
        v = c.execute("select version();").fetchone()[0]
        print("Connected OK ->", v)
