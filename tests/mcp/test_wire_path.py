"""End-to-end wire path: the governed executor dispatching to a REAL
agent-side FastMCP SSE server (tests/mcp/_throwaway_agent.py, spawned as a
subprocess). Only the radar HTTP lookups are stubbed — the MCP client hop,
the exception-group unwrapping, the output cap, and the call-time SSRF guard
all run for real."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("OTEL_SDK_DISABLED", "true")

import pytest

import awcp.mcp.server as s
import awcp.radar.netguard as ng

PORT = int(os.getenv("AWCP_TEST_REMOTE_PORT", "9411"))
ENDPOINT = f"http://127.0.0.1:{PORT}"
AGENT_SCRIPT = Path(__file__).with_name("_throwaway_agent.py")

_fn = getattr(s.execute_tool, "fn", s.execute_tool)


def _port_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


@pytest.fixture(scope="module")
def agent_server():
    if _port_open():
        pytest.skip(f"port {PORT} already in use")
    proc = subprocess.Popen(
        [sys.executable, str(AGENT_SCRIPT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "AWCP_TEST_REMOTE_PORT": str(PORT)},
    )
    try:
        for _ in range(60):
            if _port_open():
                break
            if proc.poll() is not None:
                pytest.skip("throwaway agent server exited on startup")
            time.sleep(0.25)
        else:
            pytest.skip("throwaway agent server never opened its port")
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=10)


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body


@pytest.fixture
def governed(monkeypatch, agent_server):
    """execute_tool with the radar stubbed (owner active, gate allows) and the
    dispatch left REAL, pointed at the live throwaway server."""
    owner = {"alive": True, "status": "active", "endpoint": ENDPOINT,
             "capabilities": ["echo_upper", "always_fails"],
             "card": {"skills": [{"id": "echo_upper", "risk": "high"}]}}
    monkeypatch.setattr(s, "REMOTE_TOOLS_ENABLED", True)
    monkeypatch.setattr(
        s.httpx, "get",
        lambda url, **kw: _Resp(200, owner) if url.endswith("/agent-real")
        else _Resp(404, {}))
    monkeypatch.setattr(
        s.httpx, "post",
        lambda url, **kw: _Resp(200, {"decision": "allow", "mode": "test"}))
    monkeypatch.setattr(s, "_record_checkpoint", lambda *a, **kw: None)
    return _fn


def test_raw_dispatch_over_sse(agent_server):
    out = s._invoke_remote_mcp(ENDPOINT, "echo_upper", {"text": "hello"})
    assert out == "REMOTE[HELLO]"


def test_governed_executor_end_to_end(governed):
    env = json.loads(governed("agent-real/echo_upper", {"text": "wire path"},
                              agent_id="caller-1", task_id="t1"))
    assert env["status"] == "succeeded"
    assert env["output"] == "REMOTE[WIRE PATH]"
    # card-declared "high" tightens over the medium remote default
    assert env["risk"] == "high"


def test_remote_error_surfaces_leaf_message(governed):
    env = json.loads(governed("agent-real/always_fails", {},
                              agent_id="caller-1", task_id="t1"))
    assert env["status"] == "error"
    # the anyio ExceptionGroup must be unwrapped to the remote tool's message,
    # not "unhandled errors in a TaskGroup"
    assert "intentional failure" in env["reason"]
    assert "TaskGroup" not in env["reason"]


def test_output_capped(agent_server, monkeypatch):
    monkeypatch.setattr(s, "REMOTE_TOOL_MAX_OUTPUT_CHARS", 6)
    out = s._invoke_remote_mcp(ENDPOINT, "echo_upper", {"text": "truncate me"})
    assert out == "REMOTE"


def test_ssrf_guard_fires_at_dispatch(agent_server, monkeypatch):
    monkeypatch.setattr(ng, "ALLOW_LOOPBACK", False)
    with pytest.raises(ng.UnsafeURLError):
        s._invoke_remote_mcp(ENDPOINT, "echo_upper", {"text": "x"})
