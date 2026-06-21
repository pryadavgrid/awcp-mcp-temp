"""Seed representative demo rows for the tables the JSON memory never had.

The old JSON memory only ever held registry.agents data. The new schema also
has governance / evidence / ops tables. So that the full stack (and any
dashboard/query) has something to show end-to-end, this seeds a small, clearly
tagged set of demo rows derived from the agents that were actually migrated.

Everything written here carries payload {"demo": true} (or a -demo id suffix)
so it is trivially identifiable and removable:

    DELETE FROM governance.policy_decisions WHERE payload->>'demo' = 'true';

Idempotent: keyed inserts use ON CONFLICT; ledger-style append tables are
cleared of prior demo rows first so re-running does not pile up duplicates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from connection import connect

NOW = datetime.now(timezone.utc)
DEMO = Jsonb({"demo": True})


def seed(conn) -> dict:
    cur = conn.cursor()
    counts: dict[str, int] = {}

    # pick up to 3 real migrated agents to attach demo evidence to
    cur.execute("SELECT id, name, risk, token_budget, endpoint, runtime, owner "
                "FROM registry.agents ORDER BY name LIMIT 3")
    agents = cur.fetchall()
    if not agents:
        agents = [("demo-agent", "Demo Agent", "medium", 1000,
                   "http://localhost:9000", "python", "demo")]

    # ---- registry.gateway_agents (one route per agent) ---------------------
    for i, a in enumerate(agents):
        aid, name, risk, budget, endpoint, runtime, owner = a
        cur.execute(
            "INSERT INTO registry.gateway_agents "
            "(agent_id, name, route, endpoint_url, runtime, version, owner, feature_flags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (agent_id) DO UPDATE SET route = EXCLUDED.route, "
            "endpoint_url = EXCLUDED.endpoint_url, updated_at = now()",
            (aid, name or aid, f"/demo/agent{i}", endpoint or "http://localhost:9000",
             runtime or "python", "demo-1.0", owner or "demo", DEMO),
        )
    counts["registry.gateway_agents"] = len(agents)

    # ---- registry.freeze_journal -------------------------------------------
    aid0 = agents[0][0]
    cur.execute(
        "INSERT INTO registry.freeze_journal (agent_id, kind, pid, reason, payload) "
        "VALUES (%s,'process',%s,'demo freeze sample',%s) "
        "ON CONFLICT (agent_id) DO UPDATE SET reason = EXCLUDED.reason, "
        "frozen_at = now(), payload = EXCLUDED.payload",
        (aid0, 99999, DEMO),
    )
    counts["registry.freeze_journal"] = 1

    # ---- governance.approval_tokens ----------------------------------------
    cur.execute("DELETE FROM governance.approval_tokens WHERE context_diff->>'demo' = 'true'")
    cur.execute(
        "INSERT INTO governance.approval_tokens "
        "(workflow_id, agent_id, action_class, write_scopes, risk, status, "
        " requested_by, context_diff, expires_at) "
        "VALUES (%s,%s,'external_post', ARRAY['net.write'], 0.800, 'pending', "
        "'operator', %s, %s)",
        (f"wf-demo-{aid0}", aid0, DEMO, NOW + timedelta(hours=1)),
    )
    counts["governance.approval_tokens"] = 1

    # ---- governance.policy_decisions (partitioned by ts) -------------------
    cur.execute("DELETE FROM governance.policy_decisions WHERE payload->>'demo' = 'true'")
    decisions = [
        ("call_llm", 0.100, "auto_authorized", "read"),
        ("execute_tool:save_artifact", 0.500, "awaiting_token", "fs.write"),
        ("execute_tool:external_post", 0.800, "awaiting_operator", "net.write"),
        ("execute_tool:run_command", 0.950, "denied", "shell"),
    ]
    for tool, risk, decision, scope in decisions:
        cur.execute(
            "INSERT INTO governance.policy_decisions "
            "(ts, agent_id, workflow_id, tool_call, risk, decision, scope, reason, payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (NOW, aid0, f"wf-demo-{aid0}", tool, risk, decision, scope,
             "demo policy decision", DEMO),
        )
    counts["governance.policy_decisions"] = len(decisions)

    # ---- governance.degradation_events -------------------------------------
    cur.execute("DELETE FROM governance.degradation_events WHERE payload->>'demo' = 'true'")
    cur.execute(
        "INSERT INTO governance.degradation_events "
        "(ts, agent_id, workflow_id, from_profile, to_profile, trigger, "
        " trace_sampling, reason, payload) "
        "VALUES (%s,%s,%s,'active','recommendation_only','failure_budget_exceeded',"
        "0.250,'demo degradation',%s)",
        (NOW, aid0, f"wf-demo-{aid0}", DEMO),
    )
    counts["governance.degradation_events"] = 1

    # ---- evidence.token_ledger (partitioned by ts) -------------------------
    cur.execute("DELETE FROM evidence.token_ledger WHERE task_id LIKE 'demo-%'")
    n = 0
    for i, a in enumerate(agents):
        aid = a[0]
        cur.execute(
            "INSERT INTO evidence.token_ledger "
            "(ts, agent_id, task_id, step, model, input_tokens, output_tokens, cost) "
            "VALUES (%s,%s,%s,'synthesize','llama3.1:8b',%s,%s,%s)",
            (NOW, aid, f"demo-task-{i}", 1200 + i * 100, 350 + i * 40,
             round(0.002 * (i + 1), 6)),
        )
        n += 1
    counts["evidence.token_ledger"] = n

    # ---- evidence.ledger (append-only audit, partitioned) ------------------
    cur.execute("DELETE FROM evidence.ledger WHERE payload->>'demo' = 'true'")
    for ev in ("workflow_started", "tool_called", "policy_decision", "workflow_completed"):
        cur.execute(
            "INSERT INTO evidence.ledger "
            "(ts, workflow_id, agent_id, actor, event_type, step, payload) "
            "VALUES (%s,%s,%s,'control-plane',%s,'demo',%s)",
            (NOW, f"wf-demo-{aid0}", aid0, ev, DEMO),
        )
    counts["evidence.ledger"] = 4

    # ---- ops.onboarding_runs -----------------------------------------------
    cur.execute(
        "INSERT INTO ops.onboarding_runs (workflow_id, agent_id, state, payload) "
        "VALUES (%s,%s,'done',%s) "
        "ON CONFLICT (workflow_id) DO UPDATE SET state = EXCLUDED.state, "
        "finished_at = now(), payload = EXCLUDED.payload",
        (f"onboard-demo-{aid0}", aid0, DEMO),
    )
    counts["ops.onboarding_runs"] = 1

    # ---- ops.artifacts ------------------------------------------------------
    cur.execute(
        "INSERT INTO ops.artifacts (id, agent_id, task_id, kind, storage_ref, bytes) "
        "VALUES (%s,%s,'demo-task-0','report','artifacts/demo-report.md',2048) "
        "ON CONFLICT (id) DO UPDATE SET storage_ref = EXCLUDED.storage_ref",
        (f"artifact-demo-{aid0}", aid0),
    )
    counts["ops.artifacts"] = 1

    return counts


def main() -> None:
    with connect(autocommit=False) as conn:
        counts = seed(conn)
        conn.commit()
    print("[seed] demo rows written:")
    for t, n in counts.items():
        print(f"  {t:<32} +{n}")


if __name__ == "__main__":
    main()
