"""Unit tests for the pre-execution budget pre-check layer.

Tests budget.project() (pure function) and bridge.pre_check() (stateful,
so the ledger and bridge are patched to run without real infrastructure).
"""

import pytest

from awcp.laminar import budget as budget_module


# ── budget.project — pure function ────────────────────────────────────────────

class TestBudgetProject:
    def test_within_budget_is_ok(self):
        result = budget_module.project(
            window_total_tokens=100,
            estimated_tokens=50,
            budget_tokens=500,
            warn_ratio=0.8,
        )
        assert result["projected_state"] == "ok"
        assert result["projected_tokens"] == 150
        assert result["allowed"] if "allowed" in result else True  # field not on project()

    def test_projects_to_exhausted(self):
        result = budget_module.project(
            window_total_tokens=450,
            estimated_tokens=150,
            budget_tokens=500,
            warn_ratio=0.8,
        )
        assert result["projected_state"] == "exhausted"
        assert result["projected_tokens"] == 600
        assert result["projected_ratio"] > 1.0

    def test_projects_to_warn(self):
        result = budget_module.project(
            window_total_tokens=380,
            estimated_tokens=30,
            budget_tokens=500,
            warn_ratio=0.8,
        )
        # 410/500 = 0.82 → warn
        assert result["projected_state"] == "warn"

    def test_exactly_at_budget_is_exhausted(self):
        result = budget_module.project(500, 0, 500, warn_ratio=0.8)
        assert result["projected_state"] == "exhausted"

    def test_zero_budget_treated_as_unlimited(self):
        # Mirrors existing evaluate() behaviour: budget=0 → ratio=0.0 → ok.
        # A zero budget means "unset" (unlimited), not "no tokens allowed".
        result = budget_module.project(0, 10, 0, warn_ratio=0.8)
        assert result["projected_state"] == "ok"

    def test_result_shape(self):
        result = budget_module.project(100, 50, 500, warn_ratio=0.8)
        for key in ("current_tokens", "estimated_tokens", "projected_tokens",
                    "budget_tokens", "projected_ratio", "projected_state"):
            assert key in result, f"missing key: {key}"

    def test_current_plus_estimated_equals_projected(self):
        result = budget_module.project(200, 75, 1000)
        assert result["projected_tokens"] == result["current_tokens"] + result["estimated_tokens"]

    def test_warn_ratio_respected(self):
        # With warn_ratio=0.5, 300/500 = 0.6 → warn
        result = budget_module.project(300, 0, 500, warn_ratio=0.5)
        assert result["projected_state"] == "warn"
        # With warn_ratio=0.9, 300/500 = 0.6 → ok
        result2 = budget_module.project(300, 0, 500, warn_ratio=0.9)
        assert result2["projected_state"] == "ok"


# ── bridge.pre_check — integration with mocked ledger ────────────────────────

class TestBridgePreCheck:
    """
    bridge.pre_check requires the bridge to be initialized (_initialized=True).
    We patch the internal state rather than spinning up a full radar so the
    tests are fast and self-contained.
    """

    def _init_bridge(self, monkeypatch, window_tokens: int, budget_tokens: int):
        from awcp.laminar import bridge

        monkeypatch.setattr(bridge, "_initialized", True)

        # Mock the agent registry: returns an object with risk=None, token_budget=None
        class _FakeEntry:
            risk = None
            token_budget = None

        monkeypatch.setattr(bridge, "_get_agent", lambda aid: _FakeEntry())

        # Override budget_for to return our desired budget
        monkeypatch.setattr(
            "awcp.laminar.budget.budget_for",
            lambda agent_id, risk=None, agent_budget=None: budget_tokens,
        )

        # Override LEDGER.window_usage to return controlled window total
        from awcp.laminar.ledger import LEDGER
        monkeypatch.setattr(
            LEDGER, "window_usage",
            lambda aid, window_s=None: {
                "input_tokens": window_tokens,
                "output_tokens": 0,
                "total_tokens": window_tokens,
                "cost": 0.0,
                "calls": 1,
                "last_model": "test-model",
            },
        )

    def test_allows_when_within_budget(self, monkeypatch):
        self._init_bridge(monkeypatch, window_tokens=100, budget_tokens=500)
        from awcp.laminar import bridge
        result = bridge.pre_check("agent-1", estimated_tokens=50)
        assert result["allowed"] is True
        assert result["reason"] == "within_budget"

    def test_denies_when_projected_exhausts(self, monkeypatch):
        self._init_bridge(monkeypatch, window_tokens=450, budget_tokens=500)
        from awcp.laminar import bridge
        result = bridge.pre_check("agent-1", estimated_tokens=150)
        assert result["allowed"] is False
        assert result["reason"] == "projected_exhaustion"
        assert result["projected_tokens"] == 600

    def test_skipped_when_laminar_disabled(self, monkeypatch):
        from awcp.laminar import bridge, config
        monkeypatch.setattr(config, "ENABLED", False)
        result = bridge.pre_check("agent-1", estimated_tokens=100)
        assert result["allowed"] is True
        assert result["reason"] == "pre_check_skipped"
        monkeypatch.setattr(config, "ENABLED", True)

    def test_skipped_when_not_initialized(self, monkeypatch):
        from awcp.laminar import bridge
        monkeypatch.setattr(bridge, "_initialized", False)
        result = bridge.pre_check("agent-1", estimated_tokens=100)
        assert result["allowed"] is True
        assert result["reason"] == "pre_check_skipped"

    def test_skipped_when_estimated_zero(self, monkeypatch):
        from awcp.laminar import bridge
        monkeypatch.setattr(bridge, "_initialized", True)
        result = bridge.pre_check("agent-1", estimated_tokens=0)
        assert result["allowed"] is True
        assert result["reason"] == "pre_check_skipped"

    def test_skipped_when_no_agent_id(self, monkeypatch):
        from awcp.laminar import bridge
        monkeypatch.setattr(bridge, "_initialized", True)
        result = bridge.pre_check("", estimated_tokens=100)
        assert result["allowed"] is True

    def test_fail_open_on_internal_error(self, monkeypatch):
        from awcp.laminar import bridge
        monkeypatch.setattr(bridge, "_initialized", True)
        monkeypatch.setattr(bridge, "_get_agent", lambda aid: (_ for _ in ()).throw(RuntimeError("boom")))
        result = bridge.pre_check("agent-1", estimated_tokens=50)
        assert result["allowed"] is True
        assert result["reason"] == "pre_check_error"

    def test_projected_tokens_in_response(self, monkeypatch):
        self._init_bridge(monkeypatch, window_tokens=200, budget_tokens=1000)
        from awcp.laminar import bridge
        result = bridge.pre_check("agent-1", estimated_tokens=75)
        assert result["projected_tokens"] == 275
        assert result["current_tokens"] == 200
        assert result["estimated_tokens"] == 75
        assert result["budget_tokens"] == 1000


# ── scenario: the example from the problem statement ─────────────────────────

class TestProblemStatementScenario:
    """
    Budget = 500, Current = 450, Next call estimate = 150.
    Desired: denied BEFORE execution, usage stays at 450.
    """

    def test_overshoot_is_prevented(self, monkeypatch):
        from awcp.laminar import bridge, config

        # Patch bridge into initialized state
        monkeypatch.setattr(bridge, "_initialized", True)
        monkeypatch.setattr(config, "ENABLED", True)

        class _FakeEntry:
            risk = None
            token_budget = None

        monkeypatch.setattr(bridge, "_get_agent", lambda aid: _FakeEntry())
        monkeypatch.setattr(
            "awcp.laminar.budget.budget_for",
            lambda agent_id, risk=None, agent_budget=None: 500,
        )
        from awcp.laminar.ledger import LEDGER
        monkeypatch.setattr(
            LEDGER, "window_usage",
            lambda aid, window_s=None: {"total_tokens": 450, "input_tokens": 450,
                                        "output_tokens": 0, "cost": 0.0, "calls": 5,
                                        "last_model": "test"},
        )

        result = bridge.pre_check("agent-x", estimated_tokens=150)

        assert result["allowed"] is False, "Should be denied: 450+150=600 > 500"
        assert result["projected_tokens"] == 600
        assert result["budget_tokens"] == 500
        assert result["current_tokens"] == 450
        assert result["estimated_tokens"] == 150
