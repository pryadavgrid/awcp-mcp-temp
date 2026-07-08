"""Unit tests for the OVERALL session token limit (LMNR_SESSION_TOKEN_LIMIT).

The session ceiling sums LIFETIME usage across ALL agents; once total tokens
reach the limit, bridge.session_exhausted() flips true and the radar's
_token_blocked() hard-blocks every agent and tool call. Tests run against a
fresh TokenLedger patched into the bridge so no real infrastructure is needed.
"""

import pytest

from awcp.laminar import bridge, config
from awcp.laminar.ledger import TokenLedger


@pytest.fixture()
def fresh_session(monkeypatch):
    """A clean ledger wired into the bridge, initialised, with a small session
    limit so tests don't need to record millions of tokens."""
    # Fully sandboxed: no shared JSONL evidence file, no DB mirror — a test run must
    # never leak mock records into the real ledger the radar hydrates from.
    monkeypatch.setattr(config, "LEDGER_PATH", "")
    ledger = TokenLedger()
    monkeypatch.setattr(ledger, "_append_db", lambda rec: None)
    monkeypatch.setattr(bridge, "LEDGER", ledger)
    monkeypatch.setattr(bridge, "_initialized", True)
    monkeypatch.setattr(config, "ENABLED", True)
    monkeypatch.setattr(config, "SESSION_TOKEN_LIMIT", 1_000)
    return ledger


def _spend(ledger, agent_id, tokens):
    """Record one governed LLM call for a (realistically named) agent."""
    ledger.record(agent_id=agent_id, task_id="task-demo", step="llm_call",
                  model="qwen2.5:7b", input_tokens=tokens, output_tokens=0)


class TestSessionState:
    def test_fresh_session_is_ok(self, fresh_session):
        s = bridge.session_state()
        assert s["state"] == "ok"
        assert s["used_tokens"] == 0
        assert s["limit_tokens"] == 1_000
        assert not bridge.session_exhausted()

    def test_limit_sums_across_agents_not_per_agent(self, fresh_session):
        # Three real-world agents each stay FAR below any per-agent budget,
        # but their combined spend crosses the session ceiling.
        _spend(fresh_session, "agent-langgraph-a0fc2bd9", 600)
        _spend(fresh_session, "agent-crewai-200bb5db", 399)
        assert not bridge.session_exhausted()          # 999 < 1000

        _spend(fresh_session, "agent-pydantic_ai-ca81eb4b", 1)
        s = bridge.session_state()
        assert s["used_tokens"] == 1_000
        assert s["state"] == "exhausted"
        assert bridge.session_exhausted()              # 1000 >= 1000 — everyone blocked

    def test_output_tokens_count_too(self, fresh_session):
        fresh_session.record(agent_id="agent-langgraph-a0fc2bd9", task_id="t",
                             step="llm_call", model="qwen2.5:7b",
                             input_tokens=400, output_tokens=600)
        assert bridge.session_exhausted()

    def test_zero_limit_disables_session_control(self, fresh_session, monkeypatch):
        monkeypatch.setattr(config, "SESSION_TOKEN_LIMIT", 0)
        _spend(fresh_session, "agent-langgraph-a0fc2bd9", 10_000_000)
        s = bridge.session_state()
        assert s["state"] == "ok"
        assert not bridge.session_exhausted()

    def test_fail_open_when_uninitialised(self, fresh_session, monkeypatch):
        # Laminar off => no token control at all; the session check must not block.
        _spend(fresh_session, "agent-langgraph-a0fc2bd9", 5_000)
        monkeypatch.setattr(bridge, "_initialized", False)
        assert not bridge.session_exhausted()

    def test_default_limit_is_15_million(self, monkeypatch):
        # The shipped default (no env override) is 15M tokens.
        monkeypatch.delenv("LMNR_SESSION_TOKEN_LIMIT", raising=False)
        import importlib
        assert int(importlib.reload(config).SESSION_TOKEN_LIMIT) == 15_000_000
