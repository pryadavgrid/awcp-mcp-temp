#!/bin/bash
# Start the Agent Radar — dynamic registry / discovery + onboarding (port :8090).
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
# Load .env if present (LMNR_PROJECT_API_KEY, etc.)
[ -f .env ] && set -a && source .env && set +a
echo "🛰  Agent Radar -> http://localhost:8090   (API: /agents, /agents/announce, /agents/register, /agents/{id}/gate, /healthz)"
./.venv/bin/uvicorn awcp.radar.api:app --host 0.0.0.0 --port 8090 --reload
