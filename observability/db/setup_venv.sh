#!/usr/bin/env bash
# Create an ISOLATED venv for the migration toolkit (keeps the project .venv clean).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"

if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$HERE/requirements.txt"
echo "[setup] venv ready -> $VENV"
"$VENV/bin/python" -c "import psycopg; print('[setup] psycopg', psycopg.__version__)"
