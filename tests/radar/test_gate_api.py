"""Gate decision-matrix tests (the magazine's Step 03 contract).

Exercises the full gate brain — policy_engine + approval tokens — under OPA mode,
with a Python mirror of observability/opa/policies/awcp.rego standing in for a
live OPA server. Each case here is one of the README's required gate tests, so
this doubles as the contract awcp.rego must satisfy.
"""

from types import SimpleNamespace

import pytest

from awcp.radar import approval, policy_engine
from awcp.radar.models import AgentEntry

# Mirrors data/awcp.json — keep in sync with the policy's data document.
_HIGH_RISK_TIERS = {"high"}
_DANGEROUS_TOOLS = {"run_command", "write_file"}


def fake_opa(doc: dict) -> dict:
    """Pure-Python mirror of awcp.rego's decision precedence."""
    agent, action, approval_in = doc["agent"], doc["action"], doc["approval"]
    is_write = action["write"]
    mode = agent["autonomy_profile"]
    scopes = set(agent["write_scopes"])
    high_risk = (is_write and agent["risk"] in _HIGH_RISK_TIERS) or \
        action["tool_name"] in _DANGEROUS_TOOLS

    if not is_write:
        return {"decision": "allow", "mode": mode, "requires_approval": False,
                "reason": "read-only", "policy_id": "awcp.governance.read_allow"}
    if agent["status"] == "quarantined":
        return {"decision": "deny", "mode": "quarantined", "requires_approval": False,
                "reason": "quarantined", "policy_id": "awcp.governance.quarantine_deny"}
    if action["scope"] and action["scope"] not in scopes:
        return {"decision": "deny", "mode": "out_of_scope", "requires_approval": False,
                "reason": "out of scope", "policy_id": "awcp.governance.scope_deny"}
    if agent["hard_stop"]:
        return {"decision": "deny", "mode": mode, "requires_approval": False,
                "reason": "hard stop", "policy_id": "awcp.governance.hard_stop_deny"}
    if agent["write_blocked"]:
        return {"decision": "deny", "mode": mode, "requires_approval": False,
                "reason": "write blocked", "policy_id": "awcp.governance.write_block_deny"}
    if high_risk and not approval_in["token_valid"]:
        return {"decision": "deny", "mode": mode, "requires_approval": True,
                "approval_scope": action["scope"], "reason": "needs approval",
                "policy_id": "awcp.governance.requires_approval"}
    return {"decision": "allow", "mode": mode, "requires_approval": False,
            "reason": "approved", "policy_id": "awcp.governance.allow"}


def _entry(**kw) -> AgentEntry:
    base = dict(id="agent-1", name="a", status="active", autonomy_profile="active",
                write_scopes=["workspace.write"], risk="medium")
    base.update(kw)
    return AgentEntry(**base)


def _req(**kw) -> SimpleNamespace:
    base = dict(action="save_note", write=True, scope="workspace.write",
                tool_name="save_note", workflow_id="wf-1", task_id="t-1", approval_token="")
    base.update(kw)
    return SimpleNamespace(**base)


class TestGateMatrix:
    def setup_method(self):
        self._orig = policy_engine.ENGINE_MODE
        policy_engine.ENGINE_MODE = "opa"

    def teardown_method(self):
        policy_engine.ENGINE_MODE = self._orig

    @pytest.fixture(autouse=True)
    def _patch_opa(self, monkeypatch):
        monkeypatch.setattr(policy_engine.opa, "query", fake_opa)

    def test_read_only_allowed(self):
        assert policy_engine.evaluate(_entry(), _req(write=False))["decision"] == "allow"

    def test_quarantined_write_denied(self):
        assert policy_engine.evaluate(_entry(status="quarantined"), _req())["decision"] == "deny"

    def test_undeclared_scope_denied(self):
        assert policy_engine.evaluate(_entry(), _req(scope="billing.write"))["decision"] == "deny"

    def test_active_declared_scope_allowed(self):
        assert policy_engine.evaluate(_entry(), _req())["decision"] == "allow"

    def test_recommendation_only_denied(self):
        d = policy_engine.evaluate(_entry(autonomy_profile="recommendation_only"), _req())
        assert d["decision"] == "deny"

    def test_suspended_denied(self):
        assert policy_engine.evaluate(_entry(autonomy_profile="suspended"), _req())["decision"] == "deny"

    def test_high_risk_requires_approval(self):
        d = policy_engine.evaluate(_entry(risk="high"), _req())
        assert d["decision"] == "deny"
        assert d["requires_approval"] is True

    def test_dangerous_tool_requires_approval(self):
        d = policy_engine.evaluate(_entry(), _req(action="run_command", tool_name="run_command",
                                                  scope="workspace.write"))
        assert d["requires_approval"] is True

    def test_valid_token_allows_exact_action(self):
        grant = approval.issue("agent-1", action="save_note", scope="workspace.write",
                               workflow_id="wf-1", task_id="t-1")
        d = policy_engine.evaluate(_entry(risk="high"), _req(approval_token=grant["token"]))
        assert d["decision"] == "allow"

    def test_expired_token_denied(self):
        grant = approval.issue("agent-1", action="save_note", scope="workspace.write",
                               workflow_id="wf-1", task_id="t-1", ttl=-1)
        d = policy_engine.evaluate(_entry(risk="high"), _req(approval_token=grant["token"]))
        assert d["decision"] == "deny"

    def test_wrong_scope_token_denied(self):
        grant = approval.issue("agent-1", action="save_note", scope="billing.write",
                               workflow_id="wf-1", task_id="t-1")
        d = policy_engine.evaluate(_entry(risk="high"), _req(approval_token=grant["token"]))
        assert d["decision"] == "deny"

    def test_reused_token_denied(self):
        grant = approval.issue("agent-1", action="save_note", scope="workspace.write",
                               workflow_id="wf-1", task_id="t-1")
        req = _req(approval_token=grant["token"])
        assert policy_engine.evaluate(_entry(risk="high"), req)["decision"] == "allow"
        assert policy_engine.evaluate(_entry(risk="high"), req)["decision"] == "deny"
