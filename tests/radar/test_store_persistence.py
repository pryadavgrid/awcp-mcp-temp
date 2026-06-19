"""Registry persistence tests (awcp.radar.store).

Fail-open JSON-file persistence is verified everywhere; the Postgres backend
(registry.agents from observability/init-db) is integration-only and SKIPPED
unless AGENT_RADAR_TEST_DATABASE_URL points at a reachable schema.
"""

from __future__ import annotations

import importlib
import os

import pytest

from awcp.radar.models import AgentEntry


# ── JSON fallback (no DB) ──────────────────────────────────────────────────────

@pytest.fixture()
def store_json(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_RADAR_DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENT_RADAR_DB", str(tmp_path / "registry.json"))
    import awcp.radar.store as store
    importlib.reload(store)
    return store


class TestJsonFallback:
    def test_backend_is_json_not_pg(self, store_json):
        assert store_json.REGISTRY._pg.ok is False

    def test_self_survives_reload_scan_does_not(self, store_json):
        r = store_json.REGISTRY
        r.register(AgentEntry(id="reg-self", name="s", kind="agent", source="self"))
        # a scanned entry is persisted in the snapshot but must NOT be restored
        r.register(AgentEntry(id="proc-1", name="p", kind="agent", source="scan"))
        fresh = store_json.Registry()             # simulate a restart
        ids = {e.id for e in fresh.all()}
        assert "reg-self" in ids
        assert "proc-1" not in ids                # only source='self' survives

    def test_remove_persists(self, store_json):
        r = store_json.REGISTRY
        r.register(AgentEntry(id="reg-x", name="x", kind="agent", source="self"))
        assert r.remove("reg-x") is True
        fresh = store_json.Registry()
        assert fresh.get("reg-x") is None


# ── Postgres backend (opt-in integration) ──────────────────────────────────────

_TEST_URL = os.getenv("AGENT_RADAR_TEST_DATABASE_URL", "").strip()


def _reachable(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text
    except Exception:
        return False
    try:
        eng = create_engine(url, connect_args={"connect_timeout": 3})
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM registry.agents LIMIT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(
    not (_TEST_URL and _reachable(_TEST_URL)),
    reason="set AGENT_RADAR_TEST_DATABASE_URL to a Postgres with the init-db schema",
)


@pytest.fixture()
def store_pg(monkeypatch):
    monkeypatch.setenv("AGENT_RADAR_DATABASE_URL", _TEST_URL)
    import awcp.radar.store as store
    importlib.reload(store)
    assert store.REGISTRY._pg.ok is True
    from sqlalchemy import text
    with store.REGISTRY._pg._engine.begin() as c:   # clean slate
        c.execute(text("DELETE FROM registry.agents"))
    importlib.reload(store)
    return store


@pg
class TestPostgresBackend:
    def test_persist_and_restart_load(self, store_pg):
        r = store_pg.REGISTRY
        r.register(AgentEntry(id="reg-a", name="a", kind="agent", source="self",
                              framework="langgraph", risk="high",
                              write_scopes=["fs:/tmp"], feature_flags={"gw": True},
                              user="ssrivastava"))
        r.register(AgentEntry(id="proc-9", name="p", kind="agent", source="scan"))
        fresh = store_pg.Registry()                # restart
        e = fresh.get("reg-a")
        assert e is not None and e.framework == "langgraph" and e.risk == "high"
        assert e.write_scopes == ["fs:/tmp"] and e.feature_flags == {"gw": True}
        assert e.user == "ssrivastava"
        assert fresh.get("proc-9") is None         # scan not restored

    def test_full_sync_prunes_absent_rows(self, store_pg):
        r = store_pg.REGISTRY
        r.register(AgentEntry(id="reg-keep", name="k", kind="agent", source="self"))
        r.register(AgentEntry(id="reg-drop", name="d", kind="agent", source="self"))
        assert r.remove("reg-drop") is True        # full-sync deletes it from the table
        with r._pg._engine.connect() as c:
            from sqlalchemy import text
            n = c.execute(text("SELECT count(*) FROM registry.agents "
                               "WHERE id='reg-drop'")).scalar()
        assert n == 0
