#!/usr/bin/env bash
# Reliably stop everything run_everything.sh starts — even when Ctrl+C wedges.
#
# Why this exists
# ---------------
# The gateway runs in the FOREGROUND, and its background scanner enumerates host
# processes to detect agents. On macOS that enumeration can wedge; when it does,
# the gateway doesn't exit on Ctrl+C, so the launcher's cleanup trap never reaches
# the background services (MCP server, OPA agent, Temporal) and they orphan.
#
# This script tears them down by PORT using a TARGETED `lsof` (one port at a
# time), which does NOT trigger the full process-table scan that wedges. So it
# works even while the wedge is happening.
#
# It does NOT touch Docker (telemetry + Postgres), Ollama, or your other projects.
#   --ui      also stop the React/Vite dev server on :5173
#   --docker  also bring the telemetry + Postgres docker stack down
set -u

GATEWAY_PORT="${GATEWAY_PORT:-8000}"
MCP_PORT="${MCP_PORT:-8002}"
OPA_PORT="${OPA_AGENT_PORT:-8105}"
TEMPORAL_UI_PORT="${TEMPORAL_UI_PORT:-8233}"
TEMPORAL_PORT="${TEMPORAL_PORT:-7233}"
UI_PORT="${UI_PORT:-5173}"

STOP_UI=0; STOP_DOCKER=0
for a in "$@"; do
  case "$a" in
    --ui)     STOP_UI=1 ;;
    --docker) STOP_DOCKER=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  esac
done

# (port:label) pairs — only services run_everything.sh starts.
PORTS=(
  "$GATEWAY_PORT:gateway"
  "$MCP_PORT:mcp-server"
  "$OPA_PORT:opa-agent"
  "$TEMPORAL_UI_PORT:temporal-ui"
  "$TEMPORAL_PORT:temporal"
)
[ "$STOP_UI" = 1 ] && PORTS+=("$UI_PORT:react-ui")

kill_port() {
  local port="$1" label="$2" pids
  # lsof -t on ONE port is targeted (no full enumeration → no macOS wedge).
  pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null | sort -u)
  if [ -z "$pids" ]; then
    printf "  :%-5s %-13s already free\n" "$port" "$label"; return
  fi
  # graceful first
  for pid in $pids; do kill -TERM "$pid" 2>/dev/null; done
  for _ in 1 2 3 4 5; do
    sleep 1
    pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null | sort -u)
    [ -z "$pids" ] && break
  done
  # force any stragglers
  if [ -n "$pids" ]; then
    for pid in $pids; do kill -9 "$pid" 2>/dev/null; done
    printf "  :%-5s %-13s killed (forced)\n" "$port" "$label"
  else
    printf "  :%-5s %-13s stopped\n" "$port" "$label"
  fi
}

echo "▶ Stopping AWCP services by port…"
for entry in "${PORTS[@]}"; do
  kill_port "${entry%%:*}" "${entry##*:}"
done

if [ "$STOP_DOCKER" = 1 ]; then
  echo "▶ Bringing the telemetry + Postgres docker stack down…"
  docker compose -f "$(cd "$(dirname "$0")/.." && pwd)/observability/docker-compose.yml" down
else
  echo "▶ Left running (not started here): Docker telemetry + Postgres, Ollama$([ "$STOP_UI" = 1 ] || echo ", React UI :$UI_PORT")."
  echo "  add --ui to also stop the React dev server, --docker to also stop the Docker stack."
fi
echo "✓ Done."
