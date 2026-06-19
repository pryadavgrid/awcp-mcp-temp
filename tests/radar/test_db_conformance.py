"""Conformance tests for the durable governance layer (awcp.radar.db).

Two tiers:

  * Pure / fail-open tests — run anywhere, with no database and even without
    SQLAlchemy installed. They lock in the routing map, the partition-range maths,
    the durable-event whitelist, and the rule that every write is a silent no-op
    when no DB is configured.
  * Integration tests — exercise the real canonical schema (registry / governance
    / evidence / ops from observability/init-db). They are SKIPPED unless
    AGENT_RADAR_TEST_DATABASE_URL points at a reachable Postgres carrying that
    schema, so CI without a database still passes.
"""

from __future__ import annotations

import importlib
import os

import pytest


# ── pure / fail-open (no DB, no SQLAlchemy required) ───────────────────────────

@pytest.fixture()
def db_nodb(monkeypatch):
    """db module reloaded with NO database configured → fail-open mode."""
    monkeypatch.delenv("AGENT_RADAR_DATABASE_URL", raising=False)
    monkeypatch.delenv("AGENT_RADAR_DB_ADMIN_URL", raising=False)
    import awcp.radar.db as db
    importlib.reload(db)
    return db


class TestRoutingAndConstants:
    def test_route_maps_to_canonical_tables(self, db_nodb):
        assert db_nodb._ROUTE["gate_denied"] == "policy_decisions"
        assert db_nodb._ROUTE["degraded"] == "degradation_events"
        assert db_nodb._ROUTE["autonomy"] == "degradation_events"
        # everything else lands in the evidence ledger
        assert db_nodb._DEFAULT_TABLE == "evidence_ledger"

    def test_durable_whitelist_has_no_dead_names(self, db_nodb):
        d = db_nodb.DURABLE_EVENT_TYPES
        for kind in ("approved", "scope_added", "scope_removed", "degraded",
                     "autonomy", "hook_stale", "gate_denied"):
            assert kind in d
        # names that are never emitted must not be whitelisted
        for dead in ("rejected", "autonomy_demoted", "autonomy_restored", "hook_silent"):
            assert dead not in d

    def test_partitioned_tables_are_the_canonical_four(self, db_nodb):
        assert set(db_nodb.PARTITIONED_TABLES) == {
            ("evidence", "ledger"), ("evidence", "token_ledger"),
            ("governance", "policy_decisions"), ("governance", "degradation_events"),
        }


class TestMonthRanges:
    def test_current_plus_n_months(self, db_nodb):
        rng = db_nodb._month_ranges(2)
        assert len(rng) == 3                      # current + 2 ahead
        # each entry: (suffix 'YYYY_MM', start 'YYYY-MM-01', end = next month 01)
        for suffix, start, end in rng:
            assert len(suffix) == 7 and suffix[4] == "_"
            assert start.endswith("-01") and end.endswith("-01")

    def test_year_rollover(self, db_nodb, monkeypatch):
        import datetime as _dt

        class _Dec(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date(2026, 12, 15)
        monkeypatch.setattr(_dt, "date", _Dec)
        # reload so _month_ranges picks up patched date
        importlib.reload(db_nodb)
        rng = db_nodb._month_ranges(1)
        assert rng[0][:2] == ("2026_12", "2026-12-01")
        assert rng[0][2] == "2027-01-01"          # December → next is January next year
        assert rng[1][0] == "2027_01"


class TestFailOpen:
    def test_disabled_without_url(self, db_nodb):
        assert db_nodb.init() is False
        assert db_nodb.enabled() is False

    def test_all_writes_are_noops(self, db_nodb):
        db_nodb.init()
        # none of these may raise, and all reads come back empty
        db_nodb.record("approved", "a", {"detail": "x"})
        assert db_nodb.record_token_usage("a", 1, 1) is None
        assert db_nodb.record_approval_request("a", ["s"], {"added": ["s"]}) is None
        assert db_nodb.decide_approval("a", "approved") is False
        assert db_nodb.open_approval_agent_ids() == set()
        assert db_nodb.record_freeze("a", "process", pid=1) is None
        assert db_nodb.record_onboarding_run("wf", "a", "done") is None
        assert db_nodb.query() == []

    def test_guards_reject_invalid_enums(self, db_nodb):
        db_nodb.init()
        # invalid kind / state are rejected even before reaching the DB
        assert db_nodb.record_freeze("a", "bogus") is None
        assert db_nodb.record_onboarding_run("wf", "a", "bogus") is None


# ── integration against the real canonical schema (opt-in) ─────────────────────

_TEST_URL = os.getenv("AGENT_RADAR_TEST_DATABASE_URL", "").strip()


def _reachable(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text
    except Exception:
        return False
    try:
        eng = create_engine(url, connect_args={"connect_timeout": 3})
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM evidence.ledger LIMIT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(
    not (_TEST_URL and _reachable(_TEST_URL)),
    reason="set AGENT_RADAR_TEST_DATABASE_URL to a Postgres with the init-db schema",
)


@pytest.fixture()
def db_pg(monkeypatch):
    monkeypatch.setenv("AGENT_RADAR_DATABASE_URL", _TEST_URL)
    monkeypatch.setenv("AGENT_RADAR_DB_ADMIN_URL", _TEST_URL)
    import awcp.radar.db as db
    importlib.reload(db)
    assert db.init() is True and db.enabled() is True
    # clean the durable tables for a deterministic run (CASCADE clears child rows
    # whose FK points at registry.agents — approval_tokens, onboarding_runs)
    with db._engine.begin() as c:
        c.execute(db._text("TRUNCATE evidence.ledger, evidence.token_ledger, "
                           "governance.policy_decisions, governance.degradation_events, "
                           "governance.approval_tokens, registry.freeze_journal, "
                           "ops.onboarding_runs, registry.agents CASCADE"))
    return db


def _seed_agent(db, agent_id: str) -> None:
    """Insert a minimal registry.agents row so FK-bearing tables (approval_tokens,
    onboarding_runs) can reference it."""
    with db._engine.begin() as c:
        c.execute(db._text(
            "INSERT INTO registry.agents (id, name) VALUES (:id, :name) "
            "ON CONFLICT (id) DO NOTHING"), {"id": agent_id, "name": agent_id})


@pg
class TestCanonicalRouting:
    def test_events_route_to_the_right_tables(self, db_pg):
        db_pg.record("approved", "a1", {"detail": "ok"})
        db_pg.record("gate_denied", "a2", {"action": "external_post"})
        db_pg.record("degraded", "a3", {"from_profile": "active", "to_profile": "trace_boost"})
        kinds = {r["event_type"] for r in db_pg.query()}
        assert kinds == {"approved", "gate_denied", "degraded"}

    def test_gate_denied_uses_canonical_decision(self, db_pg):
        db_pg.record("gate_denied", "a", {"action": "w"})
        with db_pg._engine.connect() as c:
            dec = c.execute(db_pg._text(
                "SELECT decision FROM governance.policy_decisions")).scalar()
        assert dec == "denied"                    # CHECK-legal value

    def test_evidence_is_hash_chained(self, db_pg):
        db_pg.record("approved", "a", {"n": 1})
        db_pg.record("scope_added", "a", {"n": 2})
        with db_pg._engine.connect() as c:
            rows = c.execute(db_pg._text(
                "SELECT prev_hash, row_hash FROM evidence.ledger ORDER BY ts")).all()
        assert rows[0].prev_hash is None
        assert rows[1].prev_hash == rows[0].row_hash   # the chain links

    def test_filters(self, db_pg):
        db_pg.record("approved", "a1", {})
        db_pg.record("approved", "a2", {})
        assert len(db_pg.query(agent_id="a1")) == 1
        assert len(db_pg.query(event_type="approved")) == 2


@pg
class TestApprovalTokens:
    def test_request_then_decide(self, db_pg):
        _seed_agent(db_pg, "reg-x")               # FK: approval_tokens.agent_id
        tid = db_pg.record_approval_request("reg-x", ["a", "b"], {"added": ["b"]})
        assert tid
        assert db_pg.open_approval_agent_ids() == {"reg-x"}
        assert db_pg.decide_approval("reg-x", "approved") is True
        assert db_pg.open_approval_agent_ids() == set()


@pg
class TestTokenLedger:
    def test_token_usage_lands(self, db_pg):
        db_pg.record_token_usage("a", 600, 100, cost=0.0, model="llama3.1:8b",
                                 task_id="t", step="llm_called")
        with db_pg._engine.connect() as c:
            n = c.execute(db_pg._text(
                "SELECT count(*) FROM evidence.token_ledger WHERE agent_id='a'")).scalar()
        assert n == 1


@pg
class TestFreezeAndOnboarding:
    def test_freeze_upsert_and_clear(self, db_pg):
        db_pg.record_freeze("a", "process", pid=9, reason="budget")
        with db_pg._engine.connect() as c:
            assert c.execute(db_pg._text(
                "SELECT count(*) FROM registry.freeze_journal WHERE agent_id='a'")).scalar() == 1
        db_pg.clear_freeze("a")
        with db_pg._engine.connect() as c:
            assert c.execute(db_pg._text(
                "SELECT count(*) FROM registry.freeze_journal WHERE agent_id='a'")).scalar() == 0

    def test_onboarding_run_transitions_to_done(self, db_pg):
        _seed_agent(db_pg, "a")                   # FK: onboarding_runs.agent_id
        db_pg.record_onboarding_run("wf-1", "a", "running")
        db_pg.record_onboarding_run("wf-1", "a", "done")
        with db_pg._engine.connect() as c:
            state, finished = c.execute(db_pg._text(
                "SELECT state, finished_at IS NOT NULL FROM ops.onboarding_runs "
                "WHERE workflow_id='wf-1'")).one()
        assert state == "done" and finished is True


@pg
class TestPartitions:
    def test_ensure_creates_current_and_ahead(self, db_pg):
        db_pg.ensure_partitions(2)
        suffixes = [s for s, _, _ in db_pg._month_ranges(2)]
        with db_pg._engine.connect() as c:
            for suf in suffixes:
                got = c.execute(db_pg._text(
                    "SELECT to_regclass(:n)"), {"n": f"evidence.ledger_{suf}"}).scalar()
                assert got is not None, f"missing partition evidence.ledger_{suf}"
