"""Governed-executor remote-tool branch (mocked radar + dispatch; no servers).

Covers the §5.1/§5.2/§5.4 admission and risk rules: owner lifecycle coupling,
capability checks, tighten-only risk resolution, provenance marking, and the
off-by-default feature flag. The real SSE hop is covered by test_wire_path.
"""
import json
import os

os.environ.setdefault("OTEL_SDK_DISABLED", "true")

import pytest

import awcp.mcp.server as s

# FastMCP may wrap the registered tool; the undecorated callable is what we drive.
_fn = getattr(s.execute_tool, "fn", s.execute_tool)

ACTIVE_OWNER = {
    "alive": True, "status": "active", "endpoint": "http://198.51.100.7:9400",
    "capabilities": ["summarize"],
    "card": {"skills": [{"id": "summarize", "risk": "low"}]},
}


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body


@pytest.fixture
def harness(monkeypatch):
    """Remote tools ON; radar HTTP, MCP dispatch, and checkpoints stubbed.
    Returns (owners, recorded): registry entries by id, and what the executor
    dispatched/recorded."""
    owners: dict = {}
    recorded = {"checkpoints": [], "dispatches": []}

    def fake_get(url, timeout=None, **kw):
        agent_id = url.rsplit("/", 1)[-1]
        return _Resp(200, owners[agent_id]) if agent_id in owners else _Resp(404, {})

    def fake_post(url, json=None, timeout=None, **kw):
        if url.endswith("/gate"):
            return _Resp(200, {"decision": "allow", "mode": "test"})
        return _Resp(200, {})

    def fake_invoke(endpoint, tool, tool_input):
        recorded["dispatches"].append((endpoint, tool, tool_input))
        return "remote says hi"

    def fake_checkpoint(agent_id, task_id, tool_name, tool_input, gate, eff_risk,
                        outcome, error="", origin="local", owner=""):
        recorded["checkpoints"].append(
            {"tool": tool_name, "outcome": outcome, "origin": origin,
             "owner": owner, "risk": eff_risk})

    monkeypatch.setattr(s, "REMOTE_TOOLS_ENABLED", True)
    monkeypatch.setattr(s.httpx, "get", fake_get)
    monkeypatch.setattr(s.httpx, "post", fake_post)
    monkeypatch.setattr(s, "_invoke_remote_mcp", fake_invoke)
    monkeypatch.setattr(s, "_record_checkpoint", fake_checkpoint)
    return owners, recorded


def _call(name, tool_input=None, **kw):
    return json.loads(_fn(name, tool_input or {}, **kw))


def test_active_owner_dispatches_with_provenance(harness):
    owners, recorded = harness
    owners["agent-a"] = ACTIVE_OWNER
    out = _call("agent-a/summarize", {"x": 1}, agent_id="caller-1", task_id="t1")
    assert out["status"] == "succeeded"
    assert out["output"] == "remote says hi"
    # card-declared "low" must be floored to the remote default (medium)
    assert out["risk"] == "medium"
    assert recorded["dispatches"] == [
        ("http://198.51.100.7:9400", "summarize", {"x": 1})]
    checkpoint = recorded["checkpoints"][-1]
    assert checkpoint["origin"] == "remote" and checkpoint["owner"] == "agent-a"


def test_quarantined_owner_refused_without_dispatch(harness):
    owners, recorded = harness
    owners["agent-q"] = {**ACTIVE_OWNER, "status": "quarantined",
                         "capabilities": ["t"]}
    out = _call("agent-q/t", agent_id="caller-1")
    assert out["status"] == "blocked" and out["mode"] == "remote_unresolved"
    assert "not active" in out["reason"]
    assert not recorded["dispatches"]
    assert recorded["checkpoints"][-1]["outcome"] == "blocked"


def test_unlinked_tool_refused_with_relink_hint(harness):
    owners, recorded = harness
    owners["agent-a"] = ACTIVE_OWNER
    out = _call("agent-a/not_linked", agent_id="caller-1")
    assert out["status"] == "blocked" and "relink" in out["reason"]
    assert not recorded["dispatches"]


def test_unknown_owner_refused(harness):
    _, recorded = harness
    out = _call("ghost/t", agent_id="caller-1")
    assert out["status"] == "blocked" and "not registered" in out["reason"]
    assert not recorded["dispatches"]


def test_caller_risk_override_tighten_only(harness):
    owners, _ = harness
    owners["agent-a"] = ACTIVE_OWNER
    # a caller must not be able to relax a remote tool below the write-gated tier
    assert _call("agent-a/summarize", agent_id="c", risk="low")["risk"] == "medium"
    assert _call("agent-a/summarize", agent_id="c", risk="high")["risk"] == "high"


def test_local_path_keeps_local_origin(harness):
    _, recorded = harness
    # unknown local tool -> error envelope via the unchanged local path
    out = _call("definitely_not_a_tool", agent_id="c")
    assert out["status"] == "error"
    assert recorded["checkpoints"][-1]["origin"] == "local"
    assert not recorded["dispatches"]


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(s, "REMOTE_TOOLS_ENABLED", False)
    out = json.loads(_fn("agent-a/summarize", {}, agent_id="c"))
    assert out["status"] == "error" and out["mode"] == "remote_disabled"
