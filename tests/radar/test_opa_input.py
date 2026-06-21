"""Unit tests for awcp.radar.policy_engine.build_opa_input.

The engine computes the stateful degradation-ladder facts in Python (the
magazine's Step 04) and hands OPA the declarative facts it needs for Step 03.
These tests assert that mapping without needing a live OPA server.
"""

from types import SimpleNamespace

import pytest

from awcp.radar import policy_engine
from awcp.radar.models import AgentEntry


def _entry(**kw) -> AgentEntry:
    base = dict(id="agent-1", name="a", status="active", autonomy_profile="active",
                write_scopes=["workspace.write"], risk="medium")
    base.update(kw)
    return AgentEntry(**base)


def _req(**kw) -> SimpleNamespace:
    base = dict(action="write_file", write=True, scope="workspace.write",
                tool_name="write_file", workflow_id="", task_id="", approval_token="")
    base.update(kw)
    return SimpleNamespace(**base)


class TestBuildOpaInput:
    def test_active_agent_is_not_blocked(self):
        doc = policy_engine.build_opa_input(_entry(), _req(), token_valid=False)
        assert doc["agent"]["write_blocked"] is False
        assert doc["agent"]["hard_stop"] is False
        assert doc["agent"]["status"] == "active"
        assert doc["agent"]["write_scopes"] == ["workspace.write"]

    def test_recommendation_only_blocks_writes(self):
        doc = policy_engine.build_opa_input(
            _entry(autonomy_profile="recommendation_only"), _req(), token_valid=False
        )
        assert doc["agent"]["write_blocked"] is True
        assert doc["agent"]["hard_stop"] is False

    def test_suspended_is_hard_stop(self):
        doc = policy_engine.build_opa_input(
            _entry(autonomy_profile="suspended"), _req(), token_valid=False
        )
        assert doc["agent"]["hard_stop"] is True
        assert doc["agent"]["write_blocked"] is True

    def test_action_fields_passthrough(self):
        doc = policy_engine.build_opa_input(
            _entry(), _req(action="run_command", tool_name="run_command", scope="workspace.exec"),
            token_valid=True,
        )
        assert doc["action"]["name"] == "run_command"
        assert doc["action"]["tool_name"] == "run_command"
        assert doc["action"]["scope"] == "workspace.exec"
        assert doc["action"]["write"] is True
        assert doc["approval"]["token_valid"] is True

    def test_read_action_marked_not_write(self):
        doc = policy_engine.build_opa_input(_entry(), _req(write=False), token_valid=False)
        assert doc["action"]["write"] is False
