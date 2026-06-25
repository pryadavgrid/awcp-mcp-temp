"""Dynamic discovery + lifecycle for the external task-worker agents.

The agents live in their OWN bundle folder (AWCP_AGENTS_DIR, default the
Downloads 'awcp-mcp-temp-agents' bundle), NOT inside this repo. Each sub-folder
that contains a `run.sh` is one agent — the exact convention the bundle's own
control_panel.py uses — so the agent set is discovered at request time and never
hardcoded: drop a new agent folder in and it appears automatically, whether
there are 4 of them or 400.

Each agent is a standalone FastAPI task-worker (see the bundle's awcp_kit.py)
that already:
  * exposes  POST /tasks {goal} -> task,  GET /tasks/{id} -> task (poll),
             GET /info, GET /health, POST /tasks/{id}/approve, and
  * emits its OWN OTel spans/metrics/logs AND pushes execution events to the
    AWCP radar, which turns each event into a Temporal activity dynamically.

So this module never needs to know an agent's tools or steps. It only:
  1. discovers which agents exist (folder scan),
  2. finds whether each is running and on which port (live process inspection,
     never an assumed port), and
  3. can launch one — wiring its telemetry env at the gateway's control plane.
"""

from __future__ import annotations

import os
import re
import subprocess

# The bundle of standalone agents. Override with AWCP_AGENTS_DIR.
AGENTS_DIR = "/Users/ssrivastava/Desktop/capstone-awcp/awcp-agents"


# Where launched agents should send governance + execution events. Points at
# THIS gateway's mounted radar so the agent -> radar -> Temporal/OTel pipeline is
# wired up end to end. (The agent kit reads AGENT_RADAR_URL at import time, so it
# must be in the child env BEFORE the agent starts.)
AGENT_RADAR_URL = os.getenv(
    "AWCP_AGENT_RADAR_URL", "http://localhost:8000"
)
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
AGENT_HOST = os.getenv("AWCP_AGENT_HOST", "localhost")

# Infra agents that live in the bundle but are NOT user-facing task workers (e.g.
# the hidden OPA tool-policy PDP). They must not appear in the user UI's agent
# picker or be polled for /info. Env-driven (comma list) so nothing is hardcoded;
# defaults to the OPA agent. Same spirit as AGENT_RADAR_EXCLUDE (the radar side).
_EXCLUDED = {
    n.strip() for n in os.getenv("AWCP_USER_AGENTS_EXCLUDE", "opa_agent").split(",")
    if n.strip()
}


def discover() -> list[dict]:
    """Every sub-folder with a run.sh is an agent. id = folder name. Infra agents
    in AWCP_USER_AGENTS_EXCLUDE (e.g. the OPA agent) are skipped — not user-facing."""
    agents: list[dict] = []
    if not os.path.isdir(AGENTS_DIR):
        return agents
    for name in sorted(os.listdir(AGENTS_DIR)):
        if name in _EXCLUDED:
            continue
        d = os.path.join(AGENTS_DIR, name)
        run = os.path.join(d, "run.sh")
        if os.path.isdir(d) and os.path.isfile(run):
            agents.append(
                {
                    "id": name,
                    "dir": d,
                    "run": run,
                    # Each agent's runtime is named after its folder (e.g.
                    # arxiv_agent/arxiv_agent.py) — the same convention the
                    # bundle's run.sh and control_panel.py use.
                    "runtime": os.path.join(d, name + ".py"),
                }
            )
    return agents


def find(agent_id: str) -> dict | None:
    return next((a for a in discover() if a["id"] == agent_id), None)


def _pids(agent: dict) -> list[int]:
    """PIDs whose command line references this agent's own <folder>.py runtime."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", agent["runtime"]], capture_output=True, text=True
        )
        return [int(p) for p in out.stdout.split() if p.strip()]
    except Exception:
        return []


def _listening_port(pid: int) -> int | None:
    """Discover the TCP port a running agent is actually listening on (not assumed)."""
    try:
        out = subprocess.run(
            # -a ANDs the selectors so we get ONLY this pid's LISTEN sockets.
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
        )
        m = re.search(r":(\d+)\s*\(LISTEN\)", out.stdout)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def running_state(agent: dict) -> dict:
    """Live state for one agent: running?, pids, and the discovered listen port."""
    pids = _pids(agent)
    port = _listening_port(pids[0]) if pids else None
    return {"running": bool(pids), "pids": pids, "port": port}


def base_url(port: int) -> str:
    return f"http://{AGENT_HOST}:{port}"


def start(agent: dict) -> None:
    """Launch the agent via its own run.sh, injecting telemetry env so its OTel
    exports and the radar-driven Temporal workflow both point at THIS gateway's
    control plane. Best-effort and non-blocking (Popen returns immediately)."""
    env = dict(os.environ)
    env["AGENT_RADAR_URL"] = AGENT_RADAR_URL
    env.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", OTEL_ENDPOINT)
    subprocess.Popen(
        ["bash", agent["run"]],
        cwd=agent["dir"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
