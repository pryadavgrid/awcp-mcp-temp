#!/bin/bash
# Start the Agent Radar — dynamic registry / discovery + onboarding (port :8090).
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
echo "🛰  Agent Radar -> http://localhost:8090   (API: /agents, /agents/register, /agents/{id}/gate, /healthz)"
./.venv/bin/uvicorn awcp.radar.api:app --host 0.0.0.0 --port 8090 --reload
