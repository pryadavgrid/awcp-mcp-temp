#!/usr/bin/env bash
# Phase 3 — apply the AWCP OpenFGA authorization model + seed role tuples.
#
# Idempotent: find-or-create the "awcp" store, write model.json only if the store
# has none, write tuples.json (tolerating already-present tuples), and persist
# OPENFGA_API_URL + OPENFGA_STORE_ID into ../.env for the gateway (Phase 4).
#
#   Usage:  bash observability/openfga/bootstrap.sh
#   Env:    OPENFGA_API_URL (default http://localhost:8082)
set -euo pipefail

FGA="${OPENFGA_API_URL:-http://localhost:8082}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/../.env"

py() { python3 -c "$1"; }

# 1. find-or-create store named "awcp"
STORE_ID="$(curl -sf "$FGA/stores" | py "import sys,json;print(next((s['id'] for s in json.load(sys.stdin).get('stores',[]) if s['name']=='awcp'),''))")"
if [ -z "$STORE_ID" ]; then
  STORE_ID="$(curl -sf -X POST "$FGA/stores" -H 'content-type: application/json' -d '{"name":"awcp"}' | py "import sys,json;print(json.load(sys.stdin)['id'])")"
  echo "created store  $STORE_ID"
else
  echo "reusing store  $STORE_ID"
fi

# 2. write the model only if the store has none
COUNT="$(curl -sf "$FGA/stores/$STORE_ID/authorization-models?page_size=1" | py "import sys,json;print(len(json.load(sys.stdin).get('authorization_models',[])))")"
if [ "$COUNT" = "0" ]; then
  MODEL_ID="$(curl -sf -X POST "$FGA/stores/$STORE_ID/authorization-models" -H 'content-type: application/json' -d @"$HERE/model.json" | py "import sys,json;print(json.load(sys.stdin)['authorization_model_id'])")"
  echo "wrote model    $MODEL_ID"
else
  echo "model present  (skipped write)"
fi

# 3. seed role tuples (tolerate re-runs where they already exist)
RESP="$(curl -s -X POST "$FGA/stores/$STORE_ID/write" -H 'content-type: application/json' -d @"$HERE/tuples.json")"
if echo "$RESP" | grep -qiE 'already exists|write_failed_due_to_invalid_input|cannot write a tuple which already exists'; then
  echo "tuples present (ok)"
elif [ "$RESP" = "{}" ]; then
  echo "tuples written"
else
  echo "tuples response: $RESP"
fi

# 4. persist runtime config for the gateway (Phase 4) into ../.env
touch "$ENV_FILE"
tmp="$(mktemp)"
grep -vE '^(OPENFGA_API_URL|OPENFGA_STORE_ID)=' "$ENV_FILE" > "$tmp" || true
{
  echo ""
  echo "# --- OpenFGA runtime config (written by openfga/bootstrap.sh) ---"
  echo "OPENFGA_API_URL=$FGA"
  echo "OPENFGA_STORE_ID=$STORE_ID"
} >> "$tmp"
mv "$tmp" "$ENV_FILE"

echo "----------------------------------------"
echo "OPENFGA_API_URL=$FGA"
echo "OPENFGA_STORE_ID=$STORE_ID"
echo "persisted to $ENV_FILE"
