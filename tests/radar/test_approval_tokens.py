"""Unit tests for awcp.radar.approval — HMAC approval tokens.

Tokens must be signed, expiring, single-use, and bound to the exact
agent/action/scope/workflow step (the magazine's narrow approval token).
"""

import time

import pytest

from awcp.radar import approval


class TestIssueVerify:
    def test_valid_token_verifies(self):
        grant = approval.issue("agent-1", action="write_file", scope="workspace.write")
        ok, reason = approval.verify(
            grant["token"], agent_id="agent-1", action="write_file", scope="workspace.write"
        )
        assert ok, reason

    def test_empty_token_is_rejected(self):
        ok, reason = approval.verify("", agent_id="agent-1")
        assert not ok
        assert "no approval token" in reason

    def test_malformed_token_is_rejected(self):
        ok, reason = approval.verify("not-a-token", agent_id="agent-1")
        assert not ok

    def test_tampered_signature_is_rejected(self):
        grant = approval.issue("agent-1", action="write_file")
        payload_b64, _sig = grant["token"].split(".", 1)
        forged = f"{payload_b64}.{'0' * 64}"
        ok, reason = approval.verify(forged, agent_id="agent-1", action="write_file")
        assert not ok
        assert "signature" in reason


class TestBinding:
    def test_wrong_scope_is_rejected(self):
        grant = approval.issue("agent-1", action="write_file", scope="workspace.write")
        ok, reason = approval.verify(
            grant["token"], agent_id="agent-1", action="write_file", scope="billing.write"
        )
        assert not ok
        assert "scope" in reason

    def test_wrong_agent_is_rejected(self):
        grant = approval.issue("agent-1", action="write_file")
        ok, reason = approval.verify(grant["token"], agent_id="agent-2", action="write_file")
        assert not ok
        assert "agent_id" in reason


class TestExpiryAndReplay:
    def test_expired_token_is_rejected(self):
        grant = approval.issue("agent-1", action="write_file", ttl=-1)
        ok, reason = approval.verify(grant["token"], agent_id="agent-1", action="write_file")
        assert not ok
        assert "expired" in reason

    def test_single_use_token_cannot_replay(self):
        grant = approval.issue("agent-1", action="write_file")
        first_ok, _ = approval.verify(
            grant["token"], agent_id="agent-1", action="write_file", consume=True
        )
        second_ok, reason = approval.verify(
            grant["token"], agent_id="agent-1", action="write_file", consume=True
        )
        assert first_ok
        assert not second_ok
        assert "replay" in reason

    def test_dry_run_does_not_burn(self):
        grant = approval.issue("agent-1", action="write_file")
        approval.verify(grant["token"], agent_id="agent-1", action="write_file", consume=False)
        ok, _ = approval.verify(
            grant["token"], agent_id="agent-1", action="write_file", consume=True
        )
        assert ok  # still usable after a dry-run check
