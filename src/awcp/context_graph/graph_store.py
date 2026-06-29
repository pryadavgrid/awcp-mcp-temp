"""Neo4j projection of the context graph — an additive read-model.

Postgres ``evidence.ledger`` stays the tamper-evident source of truth (append-only,
hash-chained). This module mirrors each checkpoint into Neo4j as a real graph so
you can run relationship queries and visualise lineage:

    (:Agent {id})-[:PERFORMED]->(:Step {row_hash, ...})<-[:INCLUDES]-(:Workflow {id})
    (:Agent)-[:RAN]->(:Workflow)
    (:Step)-[:NEXT]->(:Step)      # chronological lineage within one workflow
    (:Step)-[:USED]->(:Tool {name})

Everything here is FAIL-OPEN: if the ``neo4j`` driver isn't installed or the
database is unreachable, mirroring is a silent no-op and reads return empty — the
ledger and the rest of the app are unaffected. Nothing depends on Neo4j being up.

Config (env):
  AWCP_NEO4J_URL       bolt://localhost:7687
  AWCP_NEO4J_USER      neo4j
  AWCP_NEO4J_PASSWORD  awcpneo4j
  AWCP_NEO4J_ENABLED   "true"/"false" (default true — still no-ops if unreachable)
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("awcp.context_graph.neo4j")

NEO4J_URL = os.getenv("AWCP_NEO4J_URL", "bolt://localhost:7687")
NEO4J_USER = os.getenv("AWCP_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("AWCP_NEO4J_PASSWORD", "awcpneo4j")
_ENABLED_FLAG = os.getenv("AWCP_NEO4J_ENABLED", "true").lower() == "true"

_driver = None
_init_done = False
_lock = threading.Lock()

# MERGE the agent, workflow, step + relationships, then link the previous step in
# the same workflow (chronological NEXT) and the tool. Null-safe NEXT/USED via the
# FOREACH-on-CASE idiom so a genesis step or a tool-less step just skips the edge.
_MIRROR = """
MERGE (a:Agent {id: $agent_id})
MERGE (w:Workflow {id: $workflow_id})
MERGE (a)-[:RAN]->(w)
MERGE (s:Step {row_hash: $row_hash})
  SET s += $props
MERGE (a)-[:PERFORMED]->(s)
MERGE (w)-[:INCLUDES]->(s)
WITH s
FOREACH (_ IN CASE WHEN $tool = '' THEN [] ELSE [1] END |
  MERGE (t:Tool {name: $tool}) MERGE (s)-[:USED]->(t))
WITH s
// an LLM step links to the Model it called (so the graph shows which model ran)
FOREACH (_ IN CASE WHEN $model = '' THEN [] ELSE [1] END |
  MERGE (m:Model {name: $model}) MERGE (s)-[:USED_MODEL]->(m))
WITH s
// explainability: a denied step links to the Policy (gate rule) that blocked it
FOREACH (_ IN CASE WHEN $blocked_policy = '' THEN [] ELSE [1] END |
  MERGE (pol:Policy {name: $blocked_policy}) MERGE (s)-[:BLOCKED_BY]->(pol))
WITH s
// a failed step gets its own Error node (per-step, no hub)
FOREACH (_ IN CASE WHEN $error_msg = '' THEN [] ELSE [1] END |
  MERGE (e:Error {row_hash: $row_hash}) SET e.message = $error_msg
  MERGE (s)-[:RAISED]->(e))
WITH s
OPTIONAL MATCH (p:Step)
  WHERE p.workflow_id = $workflow_id AND p.ts < $ts AND p.row_hash <> $row_hash
WITH s, p ORDER BY p.ts DESC LIMIT 1
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:NEXT]->(s))
"""

_FETCH = """
MATCH (s:Step)
WHERE ($wf IS NULL OR s.workflow_id = $wf)
  AND ($agent IS NULL OR s.agent_id = $agent)
WITH s ORDER BY s.ts DESC LIMIT $limit
OPTIONAL MATCH (p:Step)-[:NEXT]->(s)
OPTIONAL MATCH (s)-[:BLOCKED_BY]->(pol:Policy)
OPTIONAL MATCH (s)-[:RAISED]->(err:Error)
RETURN s.row_hash AS id, s.step AS step, s.ts AS ts, s.decision AS decision,
       s.outcome AS outcome, s.risk AS risk, s.tool AS tool, s.model AS model,
       s.input_tokens AS input_tokens, s.output_tokens AS output_tokens,
       s.agent_id AS agent, s.workflow_id AS workflow,
       s.resume_pointer AS resume_pointer, s.context_hash AS context_hash,
       p.row_hash AS prev, pol.name AS policy, err.message AS error
ORDER BY ts ASC
"""


# A2A wiring: project an agent's advertised AgentCard skills as graph nodes, so
# the context graph knows what each agent CAN do (capability) next to what it DID.
# Skill nodes are deduped by id (one (:Skill {id:"web_search"}) shared across every
# agent that advertises it) — which is exactly the A2A discovery view.
_MIRROR_SKILLS = """
MERGE (a:Agent {id: $agent_id})
SET a.card_name = $card_name, a.card_description = $card_desc
WITH a
UNWIND $skills AS sk
  MERGE (s:Skill {id: sk})
  MERGE (a)-[:HAS_SKILL]->(s)
"""

_FETCH_SKILLS = """
MATCH (a:Agent)-[:HAS_SKILL]->(s:Skill)
WHERE a.id IN $agents
RETURN a.id AS agent, s.id AS skill
"""


def _connect():
    """Lazily build the driver; returns it or None (and disables further tries)."""
    global _driver, _init_done
    if _init_done:
        return _driver
    with _lock:
        if _init_done:
            return _driver
        _init_done = True
        if not _ENABLED_FLAG:
            log.info("context_graph.neo4j disabled (AWCP_NEO4J_ENABLED=false)")
            return None
        try:
            from neo4j import GraphDatabase
            drv = GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD))
            drv.verify_connectivity()
            with drv.session() as s:
                s.run("CREATE CONSTRAINT step_rowhash IF NOT EXISTS "
                      "FOR (n:Step) REQUIRE n.row_hash IS UNIQUE")
                s.run("CREATE CONSTRAINT agent_id IF NOT EXISTS "
                      "FOR (n:Agent) REQUIRE n.id IS UNIQUE")
                s.run("CREATE CONSTRAINT workflow_id IF NOT EXISTS "
                      "FOR (n:Workflow) REQUIRE n.id IS UNIQUE")
            _driver = drv
            log.info("context_graph.neo4j connected url=%s", NEO4J_URL)
        except Exception as exc:  # noqa: BLE001 — fail-open, no Neo4j required
            log.info("context_graph.neo4j unavailable (%s) — projection disabled", type(exc).__name__)
            _driver = None
        return _driver


def enabled() -> bool:
    return _connect() is not None


# ── write ────────────────────────────────────────────────────────────────────

def mirror_checkpoint(node) -> None:
    """Mirror one ContextNode into Neo4j. Best-effort; never raises."""
    drv = _connect()
    if drv is None:
        return
    pl = node.payload or {}
    outcome = pl.get("outcome", "")
    decision = pl.get("decision", "")
    props = {
        "step": node.step, "ts": float(node.ts or 0.0), "decision": decision,
        "outcome": outcome, "risk": pl.get("risk", ""),
        "tool": pl.get("tool", ""), "model": pl.get("model", ""),
        "input_tokens": int(pl.get("input_tokens", 0) or 0),
        "output_tokens": int(pl.get("output_tokens", 0) or 0),
        "resume_pointer": node.resume_pointer,
        "context_hash": node.context_hash, "prev_hash": node.prev_hash or "",
        "workflow_id": node.workflow_id, "agent_id": node.agent_id, "task_id": node.task_id,
    }
    # A denied step links to the Policy (gate mode) that blocked it; a failed step
    # carries its error message. Both stay empty for ordinary allowed steps.
    blocked_policy = (pl.get("mode") or "denied") if (outcome == "blocked" or decision == "deny") else ""
    error_msg = pl.get("error", "") if outcome == "error" else ""
    try:
        with drv.session() as s:
            s.run(_MIRROR, agent_id=node.agent_id or "unknown",
                  workflow_id=node.workflow_id or "unknown", row_hash=node.row_hash,
                  props=props, tool=props["tool"] or "", model=props["model"] or "",
                  ts=props["ts"], blocked_policy=blocked_policy, error_msg=error_msg)
            # A2A: enrich the acting agent with its advertised AgentCard skills.
            _attach_skills(s, node.agent_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("context_graph.neo4j.mirror failed row=%s err=%r", node.row_hash[:12], exc)


def _agent_card(agent_id: str):
    """Look up an agent's (skills, card) from the live registry. Returns
    (skills_list, card_dict) or (None, None) if the registry isn't reachable from
    this process (fail-open — the radar process has it; others don't)."""
    if not agent_id:
        return None, None
    try:
        from awcp.radar.store import REGISTRY
        e = REGISTRY.get(agent_id)
    except Exception:  # noqa: BLE001 — registry not importable here
        return None, None
    if e is None:
        return None, None
    skills = [x for x in (getattr(e, "skills", None) or []) if x]
    card = getattr(e, "card", None)
    return skills, (card if isinstance(card, dict) else {})


def _attach_skills(session, agent_id: str) -> None:
    """Attach an agent's AgentCard skills as (:Agent)-[:HAS_SKILL]->(:Skill). No-op
    when the agent has no card/skills or the registry isn't reachable."""
    skills, card = _agent_card(agent_id)
    if not skills and not card:
        return
    try:
        session.run(_MIRROR_SKILLS, agent_id=agent_id, skills=skills or [],
                    card_name=(card or {}).get("name", "") or "",
                    card_desc=(card or {}).get("description", "") or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("context_graph.neo4j.skills failed agent=%s err=%r", agent_id, exc)


def sync_cards() -> dict:
    """Project EVERY registered agent's AgentCard skills (A2A) into the graph —
    independent of whether the agent has recorded a step yet. Idempotent."""
    drv = _connect()
    if drv is None:
        return {"enabled": False, "agents": 0, "skills": 0}
    try:
        from awcp.radar.store import REGISTRY
        entries = list(REGISTRY.all())
    except Exception:  # noqa: BLE001
        return {"enabled": True, "agents": 0, "skills": 0, "note": "registry unavailable"}
    n_agents = n_skills = 0
    try:
        with drv.session() as s:
            for e in entries:
                skills, card = _agent_card(e.id)
                if not skills and not card:
                    continue
                s.run(_MIRROR_SKILLS, agent_id=e.id, skills=skills or [],
                      card_name=(card or {}).get("name", "") or "",
                      card_desc=(card or {}).get("description", "") or "")
                n_agents += 1
                n_skills += len(skills or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("context_graph.neo4j.sync_cards failed err=%r", exc)
    return {"enabled": True, "agents": n_agents, "skills": n_skills}


# ── read (for the UI graph view) ─────────────────────────────────────────────

def fetch_graph(workflow: str | None = None, agent: str | None = None,
                limit: int = 300) -> dict:
    """Return {nodes, edges, stats} for visualization. Empty when Neo4j is off."""
    drv = _connect()
    if drv is None:
        return {"enabled": False, "nodes": [], "edges": [], "stats": {}}
    try:
        with drv.session() as s:
            rows = list(s.run(_FETCH, wf=workflow, agent=agent, limit=limit))
    except Exception as exc:  # noqa: BLE001
        log.debug("context_graph.neo4j.fetch failed err=%r", exc)
        return {"enabled": True, "nodes": [], "edges": [], "stats": {}, "error": type(exc).__name__}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add(node_id: str, ntype: str, label: str, **extra):
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, "type": ntype, "label": label, **extra}

    for r in rows:
        sid = r["id"]
        aid = f"agent:{r['agent']}"
        wid = f"wf:{r['workflow']}"
        add(sid, "step", r["step"] or "step", decision=r["decision"], outcome=r["outcome"],
            risk=r["risk"], tool=r["tool"], model=r["model"],
            input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
            agent=r["agent"], workflow=r["workflow"],
            resume_pointer=r["resume_pointer"], context_hash=r["context_hash"], ts=r["ts"])
        add(aid, "agent", r["agent"] or "agent")
        add(wid, "workflow", r["workflow"] or "workflow")
        edges.append({"source": aid, "target": sid, "type": "PERFORMED"})
        edges.append({"source": wid, "target": sid, "type": "INCLUDES"})
        if r["tool"]:
            tid = f"tool:{r['tool']}"
            add(tid, "tool", r["tool"])
            edges.append({"source": sid, "target": tid, "type": "USED"})
        if r["model"]:
            mid = f"model:{r['model']}"
            add(mid, "model", r["model"])
            edges.append({"source": sid, "target": mid, "type": "USED_MODEL"})
        if r["prev"]:
            edges.append({"source": r["prev"], "target": sid, "type": "NEXT"})
        if r["policy"]:
            pid = f"policy:{r['policy']}"
            add(pid, "policy", r["policy"])
            edges.append({"source": sid, "target": pid, "type": "BLOCKED_BY"})
        if r["error"]:
            eid = f"error:{sid}"
            msg = str(r["error"])
            add(eid, "error", msg[:28] + ("…" if len(msg) > 28 else ""), message=msg)
            edges.append({"source": sid, "target": eid, "type": "RAISED"})

    # A2A capability layer: attach each agent's advertised card skills. Skill nodes
    # are deduped, so a shared skill links every agent that advertises it.
    agent_ids = [n["label"] for n in nodes.values() if n["type"] == "agent" and n["label"]]
    if agent_ids:
        try:
            with drv.session() as s2:
                for r in s2.run(_FETCH_SKILLS, agents=agent_ids):
                    skid = f"skill:{r['skill']}"
                    add(skid, "skill", r["skill"])
                    edges.append({"source": f"agent:{r['agent']}", "target": skid, "type": "HAS_SKILL"})
        except Exception as exc:  # noqa: BLE001
            log.debug("context_graph.neo4j.fetch_skills failed err=%r", exc)

    counts: dict[str, int] = {}
    for n in nodes.values():
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    return {"enabled": True, "nodes": list(nodes.values()), "edges": edges,
            "stats": {"counts": counts, "total_nodes": len(nodes), "total_edges": len(edges)}}


def status() -> dict:
    """Connection + node counts, for the UI status badge."""
    drv = _connect()
    if drv is None:
        return {"enabled": False, "url": NEO4J_URL}
    try:
        with drv.session() as s:
            # count{} subqueries always return exactly one row (0 when empty),
            # unlike chained MATCH...count which collapses to zero rows on an
            # empty graph and would make .single() blow up.
            row = s.run(
                "RETURN count{ (a:Agent) } AS agents, "
                "count{ (w:Workflow) } AS workflows, count{ (st:Step) } AS steps"
            ).single()
        return {"enabled": True, "url": NEO4J_URL,
                "agents": row["agents"], "workflows": row["workflows"], "steps": row["steps"]}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "url": NEO4J_URL, "error": type(exc).__name__}


def backfill(limit: int = 10000) -> dict:
    """Mirror existing checkpoint rows from the ledger into Neo4j (idempotent —
    MERGE-based). Useful after Neo4j is first started, to project history."""
    drv = _connect()
    if drv is None:
        return {"enabled": False, "mirrored": 0}
    from awcp.context_graph import store
    nodes = store.recent_nodes(limit=limit)   # ascending by ts → NEXT links form correctly
    n = 0
    for nd in nodes:
        mirror_checkpoint(nd)
        n += 1
    # also project A2A card skills for every registered agent
    cards = sync_cards()
    return {"enabled": True, "mirrored": n, "cards": cards}
