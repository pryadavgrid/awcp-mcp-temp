"""Tests for the OPA adapter (awcp.radar.opa) — no OPA server required.

These lock in the three guarantees that make the OPA integration safe to ship:

  * PARITY — with AWCP_OPA_URL unset and no token tiers, opa.evaluate_action is
    exactly policy.evaluate_action plus the 4-value gate kind. This is the
    "nothing changes until you opt in" promise.
  * APPROVAL TOKENS — with a risk tier opted in, an allowed high-risk write is
    held as awaiting_token; an operator action class is held as awaiting_operator.
  * FAIL-SECURE — when an OPA URL is set but unreachable, the decision falls back
    to policy.py (the gate never fails open because OPA is down).

The Rego itself is tested by policies/awcp/gate_test.rego (opa test). An
OPA-vs-policy agreement sweep against a live server is covered there + in CI.
"""

from __future__ import annotations

import importlib

import pytest

from awcp.radar.models import AgentEntry

_LADDER = ["active", "trace_boost", "throttled", "safe_profile",
           "recommendation_only", "suspended"]


def _reload_opa(monkeypatch, **env):
    """Reload awcp.radar.opa with a controlled environment (settings are read at
    import, mirroring policy.py / laminar config)."""
    for k in ("AWCP_OPA_URL", "AWCP_OPA_SHADOW", "AWCP_OPA_TOKEN_RISK_TIERS",
              "AWCP_OPA_OPERATOR_ACTION_CLASSES", "AWCP_OPA_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import awcp.radar.opa as opa
    return importlib.reload(opa)


def _agent(name="ollama", risk="low", profile="active", status="active",
           scopes=("ticketing",)):
    return AgentEntry(id=name, name=name, status=status, autonomy_profile=profile,
                      risk=risk, autonomy_ladder=list(_LADDER), write_scopes=list(scopes))


# ── parity: OPA disabled, no token tiers ──────────────────────────────────────

class TestParity:
    def test_disabled_by_default(self, monkeypatch):
        opa = _reload_opa(monkeypatch)
        assert opa.enabled() is False
        assert opa.TOKEN_RISK_TIERS == []

    def test_read_always_allowed(self, monkeypatch):
        opa = _reload_opa(monkeypatch)
        d = opa.evaluate_action(_agent(), action="look", is_write=False)
        assert d["decision"] == "allow"
        assert d["gate"] == "auto_authorized"
        assert d["engine"] == "policy"

    def test_in_scope_write_auto_authorized(self, monkeypatch):
        opa = _reload_opa(monkeypatch)
        d = opa.evaluate_action(_agent(), action="create", is_write=True, scope="ticketing")
        assert d["decision"] == "allow"
        assert d["gate"] == "auto_authorized"

    def test_out_of_scope_denied(self, monkeypatch):
        opa = _reload_opa(monkeypatch)
        d = opa.evaluate_action(_agent(), action="charge", is_write=True, scope="billing")
        assert d["decision"] == "deny"
        assert d["gate"] == "denied"
        assert d["mode"] == "out_of_scope"

    def test_quarantined_denied(self, monkeypatch):
        opa = _reload_opa(monkeypatch)
        d = opa.evaluate_action(_agent(status="quarantined"), action="x", is_write=True)
        assert d["decision"] == "deny"
        assert d["gate"] == "denied"

    def test_recommendation_only_denied(self, monkeypatch):
        opa = _reload_opa(monkeypatch)
        d = opa.evaluate_action(_agent(profile="recommendation_only"), action="x", is_write=True)
        assert d["decision"] == "deny"

    def test_graceful_rung_still_writes(self, monkeypatch):
        opa = _reload_opa(monkeypatch)
        d = opa.evaluate_action(_agent(profile="throttled"), action="x", is_write=True)
        assert d["decision"] == "allow"

    def test_high_risk_write_is_parity_without_token_tiers(self, monkeypatch):
        """A high-risk write is auto_authorized when no tier opts into tokens —
        EXACTLY today's behaviour (the magazine __default__ is high, so this also
        proves an unknown/high agent isn't newly blocked by merely shipping OPA)."""
        opa = _reload_opa(monkeypatch)
        d = opa.evaluate_action(_agent(name="x", risk="high"), action="deploy", is_write=True)
        assert d["decision"] == "allow"
        assert d["gate"] == "auto_authorized"


# ── approval tokens opted in ──────────────────────────────────────────────────

class TestApprovalTokens:
    def test_high_risk_write_awaits_token(self, monkeypatch):
        opa = _reload_opa(monkeypatch, AWCP_OPA_TOKEN_RISK_TIERS="high")
        d = opa.evaluate_action(_agent(name="x", risk="high"), action="deploy", is_write=True)
        assert d["decision"] == "deny"
        assert d["gate"] == "awaiting_token"
        assert d["mode"] == "token_required"

    def test_low_risk_unaffected_by_token_tier(self, monkeypatch):
        opa = _reload_opa(monkeypatch, AWCP_OPA_TOKEN_RISK_TIERS="high")
        d = opa.evaluate_action(_agent(name="ollama", risk="low"), action="x", is_write=True)
        assert d["gate"] == "auto_authorized"

    def test_operator_class_awaits_operator(self, monkeypatch):
        opa = _reload_opa(monkeypatch, AWCP_OPA_TOKEN_RISK_TIERS="high",
                          AWCP_OPA_OPERATOR_ACTION_CLASSES="cross_system")
        d = opa.evaluate_action(_agent(name="x", risk="high"), action="bulk",
                                is_write=True, action_class="cross_system")
        assert d["gate"] == "awaiting_operator"

    def test_deny_beats_token_requirement(self, monkeypatch):
        """A hard deny (out of scope) is never softened to awaiting_token."""
        opa = _reload_opa(monkeypatch, AWCP_OPA_TOKEN_RISK_TIERS="high")
        d = opa.evaluate_action(_agent(name="x", risk="high", scopes=("ticketing",)),
                                action="charge", is_write=True, scope="billing")
        assert d["decision"] == "deny"
        assert d["gate"] == "denied"


# ── fail-secure fallback ──────────────────────────────────────────────────────

class TestFallback:
    def test_unreachable_opa_falls_back_to_policy(self, monkeypatch):
        opa = _reload_opa(monkeypatch, AWCP_OPA_URL="http://127.0.0.1:9",
                          AWCP_OPA_TIMEOUT="0.2")
        assert opa.enabled() is True
        d = opa.evaluate_action(_agent(), action="create", is_write=True, scope="ticketing")
        assert d["decision"] == "allow"
        assert d["engine"] == "policy(opa_error)"

    def test_build_input_uses_resolved_facts(self, monkeypatch):
        """The OPA input carries policy.py's RESOLVED ladder + authoritative risk,
        so OPA and the fallback always reason over identical facts."""
        opa = _reload_opa(monkeypatch)
        doc = opa.build_input(_agent(name="x", risk="low"), "a", True, "ticketing")
        # 'x' is not in the magazine → authoritative risk is the fail-secure high
        assert doc["agent"]["risk"] == "high"
        assert doc["agent"]["ladder"] == _LADDER
        assert doc["config"]["write_block_stages"] == sorted(opa.policy.WRITE_BLOCK_STAGES)
