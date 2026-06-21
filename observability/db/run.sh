#!/usr/bin/env bash
# One-shot: ensure venv, migrate JSON -> Postgres, seed demo rows, verify.
#
# Usage:
#   bash observability/db/run.sh            # full pipeline
#   bash observability/db/run.sh migrate    # just migrate
#   bash observability/db/run.sh seed       # just seed demo rows
#   bash observability/db/run.sh verify     # just verify
#   bash observability/db/run.sh store      # dump registry via the PG bridge
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
PY="$VENV/bin/python"

[ -x "$PY" ] || bash "$HERE/setup_venv.sh"

cmd="${1:-all}"
cd "$HERE"
case "$cmd" in
  migrate) "$PY" migrate_json_to_pg.py ;;
  seed)    "$PY" seed_demo.py ;;
  verify)  "$PY" verify.py ;;
  store)   "$PY" pg_store.py ;;
  all)
    "$PY" migrate_json_to_pg.py
    echo
    "$PY" seed_demo.py
    echo
    "$PY" verify.py
    ;;
  *) echo "unknown cmd: $cmd"; exit 2 ;;
esac
