"""Unit tests for awcp.radar.policy_engine — local / opa / shadow modes.

OPA is never contacted for real here: opa.query is monkeypatched so the engine's
mode behaviour and approval-token handling are tested deterministically offline.
"""

from types import SimpleNamespace

import pytest

from awcp.radar import approval, policy_engine
from awcp.radar.models import AgentEntry


def _entry(**kw) -> AgentEntry:
    base = dict(id="agent-1", name="a", status="active", autonomy_profile="active",
                write_scopes=["workspace.write"], risk="high")
    base.update(kw)
    return AgentEntry(**base)


def _req(**kw) -> SimpleNamespace:
    base = dict(action="write_file", write=True, scope="workspace.write",
                tool_name="write_file", workflow_id="wf-1", task_id="t-1", approval_token="")
    base.update(kw)
    return SimpleNamespace(**base)


class TestLocalMode:
    def setup_method(self):
        self._orig = policy_engine.ENGINE_MODE

    def teardown_method(self):
        policy_engine.ENGINE_MODE = self._orig

    def test_active_write_in_scope_allowed(self, monkeypatch):
        monkeypatch.setattr(policy_engine, "ENGINE_MODE", "local")
        d = policy_engine.evaluate(_entry(), _req())
        assert d["decision"] == "allow"

    def test_quarantined_write_denied(self, monkeypatch):
        monkeypatch.setattr(policy_engine, "ENGINE_MODE", "local")
        d = policy_engine.evaluate(_entry(status="quarantined"), _req())
        assert d["decision"] == "deny"

    def test_read_allowed(self, monkeypatch):
        monkeypatch.setattr(policy_engine, "ENGINE_MODE", "local")
        d = policy_engine.evaluate(_entry(), _req(write=False))
        assert d["decision"] == "allow"

    def test_out_of_scope_denied(self, monkeypatch):
        monkeypatch.setattr(policy_engine, "ENGINE_MODE", "local")
        d = policy_engine.evaluate(_entry(), _req(scope="billing.write"))
        assert d["decision"] == "deny"


class TestOpaMode:
    def setup_method(self):
        self._orig = policy_engine.ENGINE_MODE
        policy_engine.ENGINE_MODE = "opa"

    def teardown_method(self):
        policy_engine.ENGINE_MODE = self._orig

    def test_opa_allow_passthrough(self, monkeypatch):
        monkeypatch.setattr(policy_engine.opa, "query",
                            lambda doc: {"decision": "allow", "reason": "ok",
                                         "mode": "active", "requires_approval": False})
        d = policy_engine.evaluate(_entry(), _req())
        assert d["decision"] == "allow"

    def test_requires_approval_without_token(self, monkeypatch):
        monkeypatch.setattr(policy_engine.opa, "query",
                            lambda doc: {"decision": "deny", "reason": "needs approval",
                                         "mode": "active", "requires_approval": True})
        d = policy_engine.evaluate(_entry(), _req())
        assert d["decision"] == "deny"
        assert d["requires_approval"] is True

    def test_valid_token_unblocks(self, monkeypatch):
        monkeypatch.setattr(policy_engine.opa, "query",
                            lambda doc: {"decision": "deny", "reason": "needs approval",
                                         "mode": "active", "requires_approval": True})
        grant = approval.issue("agent-1", action="write_file", scope="workspace.write",
                               workflow_id="wf-1", task_id="t-1")
        d = policy_engine.evaluate(_entry(), _req(approval_token=grant["token"]))
        assert d["decision"] == "allow"
        assert d.get("approval_token_used") is True

    def test_consumed_token_cannot_replay(self, monkeypatch):
        monkeypatch.setattr(policy_engine.opa, "query",
                            lambda doc: {"decision": "deny", "reason": "needs approval",
                                         "mode": "active", "requires_approval": True})
        grant = approval.issue("agent-1", action="write_file", scope="workspace.write",
                               workflow_id="wf-1", task_id="t-1")
        req = _req(approval_token=grant["token"])
        first = policy_engine.evaluate(_entry(), req)
        second = policy_engine.evaluate(_entry(), req)
        assert first["decision"] == "allow"
        assert second["decision"] == "deny"  # token already burned


class TestShadowMode:
    def setup_method(self):
        self._orig = policy_engine.ENGINE_MODE
        policy_engine.ENGINE_MODE = "shadow"

    def teardown_method(self):
        policy_engine.ENGINE_MODE = self._orig

    def test_enforces_local_and_attaches_opa(self, monkeypatch):
        # Local would ALLOW (active, in scope); OPA disagrees with deny.
        monkeypatch.setattr(policy_engine.opa, "query",
                            lambda doc: {"decision": "deny", "reason": "opa says no",
                                         "mode": "active", "requires_approval": False})
        before = policy_engine._SHADOW_MISMATCHES
        d = policy_engine.evaluate(_entry(), _req())
        assert d["decision"] == "allow"           # local is enforced
        assert d["shadow_opa"]["decision"] == "deny"
        assert policy_engine._SHADOW_MISMATCHES == before + 1

    def test_opa_error_does_not_break_gate(self, monkeypatch):
        def boom(doc):
            raise RuntimeError("opa down")
        monkeypatch.setattr(policy_engine.opa, "query", boom)
        d = policy_engine.evaluate(_entry(), _req())
        assert d["decision"] == "allow"  # local still enforced despite OPA error
