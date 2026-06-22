#!/bin/bash
# Start the AWCP Gateway — the single entry point with two route groups:
#   USER  : GET /user/agents , POST /user/ask   (over the external agent bundle)
#   AWCP  : control plane — Registry (radar) mounted at /awcp/registry
# Served on :8000.
#
# The bundle agents live OUTSIDE this repo. Point the gateway at them with
# AWCP_AGENTS_DIR (default: /Users/pryadav/Downloads/awcp-mcp-temp-agents).
# Agents launched by /user/ask are told to report to this gateway's radar via
# AWCP_AGENT_RADAR_URL (default: http://localhost:8000/awcp/registry).
#
# For full Temporal + OTel visibility also run (other terminals / docker):
#   - Temporal server               (temporal server start-dev)
#   - telemetry stack               (docker compose -f observability/docker-compose.yml up -d)
#   - Ollama                        (ollama serve, with the agents' models pulled)
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
export AWCP_AGENTS_DIR="${AWCP_AGENTS_DIR:-/Users/pchandra/CAPSTONE/DEMO1/Agents/awcp-agents}"
echo "🌐 AWCP Gateway -> http://localhost:8000   (docs: /docs)"
echo "   USER : GET /user/agents | POST /user/ask"
echo "   AWCP : /awcp/registry (radar UI + API)"
echo "   agents bundle: $AWCP_AGENTS_DIR"
./.venv/bin/uvicorn awcp.gateway.app:app --host 0.0.0.0 --port 8000 --reload
