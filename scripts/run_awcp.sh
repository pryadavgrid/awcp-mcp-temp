#!/bin/bash
# ======================================================================
# AWCP — magazine-mode runner (single command).
#
# Brings up the control plane that sits ABOVE your agents:
#   1. venv + dependencies (first run only)
#   2. telemetry stack        (starts the Docker daemon if needed, then brings up
#                              OTel Collector / Tempo / Prometheus / Loki / Grafana)
#   3. Temporal dev server     (used for the radar's ONBOARDING workflow)
#   4. the AWCP registry (radar) — discovery + onboarding + MCP linking + gate
#
# It deliberately does NOT start the governance worker and does NOT run any
# agents. You run YOUR agent yourself in another terminal; the radar detects it.
#
# Usage:   ./scripts/run_awcp.sh        (Ctrl+C stops the radar + Temporal it started)
# Env:     SKIP_TELEMETRY=1  -> don't start the docker telemetry stack
#          SKIP_INSTALL=1    -> skip the pip install even on first run
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
export PYTHONPATH="$ROOT/src"
LOGDIR="${TMPDIR:-/tmp}/awcp-run"; mkdir -p "$LOGDIR"
TEMPORAL_PID=""

say(){ printf "\033[1;36m▶ %s\033[0m\n" "$*"; }
warn(){ printf "\033[1;33m! %s\033[0m\n" "$*"; }

port_open(){ ./.venv/bin/python - "$1" 2>/dev/null <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1]))); print("open")
except Exception:
    pass
PY
}

cleanup(){
  echo
  say "Shutting down…"
  [ -n "$TEMPORAL_PID" ] && kill "$TEMPORAL_PID" 2>/dev/null || true
  say "Stopped the radar (+ Temporal if this script started it)."
  echo "  Telemetry stack left running — stop it with:"
  echo "    docker compose -f observability/docker-compose.yml down"
}
trap cleanup EXIT INT TERM

# ── 1. venv + dependencies ────────────────────────────────────────────
if [ ! -x ".venv/bin/python" ]; then
  say "Creating virtualenv + installing requirements (first run only)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
elif [ "${SKIP_INSTALL:-0}" != "1" ]; then
  say "venv present — ensuring requirements are installed…"
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

# ── 2. telemetry stack (start the Docker daemon if needed, then the stack) ──
if [ "${SKIP_TELEMETRY:-0}" = "1" ]; then
  warn "SKIP_TELEMETRY=1 — not starting the telemetry stack."
elif ! command -v docker >/dev/null 2>&1; then
  warn "Docker not installed — skipping telemetry stack (OTel exports will warn; harmless)."
else
  # Make sure the Docker daemon is up — start it if it isn't.
  if ! docker info >/dev/null 2>&1; then
    say "Docker daemon not running — starting Docker…"
    if [ "$(uname)" = "Darwin" ]; then
      open -a Docker 2>/dev/null || open -a "Docker Desktop" 2>/dev/null || true
    elif command -v systemctl >/dev/null 2>&1; then
      sudo systemctl start docker 2>/dev/null || true
    fi
    printf "  waiting for Docker daemon"
    for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; printf "."; sleep 2; done
    echo
  fi

  if docker info >/dev/null 2>&1; then
    say "Starting telemetry stack (OTel/Tempo/Prometheus/Loki/Grafana)…"
    docker compose -f observability/docker-compose.yml up -d || \
      warn "docker compose failed — radar still runs; OTel exports will warn until it's up."
  else
    warn "Docker daemon didn't come up — skipping telemetry stack (start Docker Desktop manually)."
  fi
fi

# ── 3. Temporal dev server (must be up BEFORE the radar) ──────────────
if [ -n "$(port_open 7233)" ]; then
  say "Temporal already running on :7233 — reusing it."
elif command -v temporal >/dev/null 2>&1; then
  say "Starting Temporal dev server (engine :7233, UI :8233)…"
  nohup temporal server start-dev --ip 127.0.0.1 > "$LOGDIR/temporal.log" 2>&1 &
  TEMPORAL_PID=$!
  for i in $(seq 1 30); do [ -n "$(port_open 7233)" ] && break; sleep 1; done
  [ -n "$(port_open 7233)" ] && say "Temporal is up." || warn "Temporal didn't come up — radar will onboard inline."
else
  warn "Temporal CLI not found — radar will onboard inline (install: brew install temporal)."
fi

# Temporal Web UI port depends on who started it: dev-server serves :8233,
# docker-compose (auto-setup) UI maps to :8080. Probe the live one and bind
# radar deep-links to it (a pre-set env wins via ':-').
if   [ -n "$(port_open 8233)" ]; then TEMPORAL_UI="http://localhost:8233"
elif [ -n "$(port_open 8080)" ]; then TEMPORAL_UI="http://localhost:8080"
else                                  TEMPORAL_UI="http://localhost:8233"
fi
export AGENT_RADAR_TEMPORAL_UI="${AGENT_RADAR_TEMPORAL_UI:-$TEMPORAL_UI}"
export AWCP_TEMPORAL_UI_BASE="${AWCP_TEMPORAL_UI_BASE:-$TEMPORAL_UI}"

# ── 4. the AWCP registry (radar) — foreground ────────────────────────
echo
echo "  ── AWCP is up (magazine mode) ────────────────────────────────"
echo "     Registry (radar) : http://localhost:8090"
echo "     Temporal UI      : ${TEMPORAL_UI}"
echo "     Grafana          : http://localhost:3000   (admin / admin)"
echo "     Prometheus       : http://localhost:9090"
echo
echo "  ▶ Now run YOUR agent in another terminal — the radar will detect it."
echo "    (Do NOT run the governance worker; agents run on their own here.)"
echo "  ▶ Press Ctrl+C to stop."
echo "  ──────────────────────────────────────────────────────────────"
echo
./.venv/bin/uvicorn awcp.radar.api:app --host 0.0.0.0 --port 8090
