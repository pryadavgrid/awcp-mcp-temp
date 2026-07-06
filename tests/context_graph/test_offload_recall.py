"""Context offload → retrieve round-trip (the context-window pressure valve).

Locks in the guarantees that make the offload/recall loop trustworthy:

  * ROUND-TRIP — content offloaded via a checkpoint (payload.content) is stored
    verbatim and comes back BOTH from the raw workflow graph (targeted, by
    row_hash ref) and inside the manager's budget-fitted working set.
  * RANKING — an offloaded chunk is top-weighted carried context; a recall
    marker is near-worthless (it must not crowd real context out of the budget).
  * SUPERSEDE PER LABEL — re-offloading the same label supersedes the older
    snapshot (stale, excluded from recovery); a different label never does.

No Postgres / Neo4j / Letta needed: the store degrades to its in-memory ring,
which is exactly the fail-open path the demo runs on.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import awcp.radar.api as radar_api
    # Plain TestClient (no context manager): requests work without running the
    # app's lifespan, so the background scanner never starts during tests.
    return TestClient(radar_api.app)


def _wf() -> str:
    return f"wf-offload-{uuid.uuid4().hex[:8]}"


def _offload(client, wf: str, label: str, content: str) -> dict:
    r = client.post(
        f"/agents/test-agent/checkpoint",
        json={
            "step": f"offload:{label}",
            "task_id": wf,
            "workflow_id": wf,
            "resume_pointer": f"{wf}:offloaded:{label}",
            "context": content,
            "payload": {"content": content, "label": label,
                        "tokens": max(1, len(content) // 4)},
        },
    )
    body = r.json()
    assert body["ok"] is True
    return body["node"]


def test_offload_roundtrip_by_ref(client):
    wf = _wf()
    content = "FINDINGS: gold rose 12% in Q2; drivers were central-bank buying and ETF inflows."
    node = _offload(client, wf, "findings", content)
    ref = node["row_hash"]
    assert ref

    # Targeted retrieval: the exact chunk comes back verbatim from the graph.
    g = client.get(f"/context-graph/{wf}").json()
    match = next(n for n in g["nodes"] if n["row_hash"] == ref)
    assert match["payload"]["content"] == content
    assert match["resume_pointer"] == f"{wf}:offloaded:findings"


def test_offload_returned_in_working_set(client):
    wf = _wf()
    content = "SOURCES: arxiv:2401.0001, arxiv:2401.0002 — both peer reviewed."
    _offload(client, wf, "sources", content)

    ws = client.get(f"/context-graph/{wf}/working-set?budget=2000").json()
    contents = [s["node"]["payload"].get("content") for s in ws["selected"]]
    assert content in contents
    assert ws["resume_pointer"] == f"{wf}:offloaded:sources"


def test_reoffload_same_label_supersedes(client):
    wf = _wf()
    _offload(client, wf, "draft", "draft v1 — outline only")
    _offload(client, wf, "draft", "draft v2 — full text")
    _offload(client, wf, "notes", "unrelated notes")  # different label

    stale = client.get(f"/context-graph/{wf}/stale").json()
    stale_contents = [s["node"]["payload"].get("content") for s in stale["nodes"]]
    assert "draft v1 — outline only" in stale_contents          # replaced snapshot
    assert "draft v2 — full text" not in stale_contents         # current snapshot
    assert "unrelated notes" not in stale_contents              # other label untouched
    assert stale["nodes"][0]["stale_reasons"] == ["superseded"]

    # …and recovery carries v2 + notes, never v1.
    ws = client.get(f"/context-graph/{wf}/working-set?budget=2000").json()
    contents = [s["node"]["payload"].get("content") for s in ws["selected"]]
    assert "draft v2 — full text" in contents
    assert "unrelated notes" in contents
    assert "draft v1 — outline only" not in contents


def test_step_weights_rank_offload_over_recall():
    from awcp.context_graph.manager import _STEP_WEIGHTS, _step_kind
    assert _STEP_WEIGHTS[_step_kind("offload:findings")] == 1.0
    assert _STEP_WEIGHTS[_step_kind("recall")] < 0.5
