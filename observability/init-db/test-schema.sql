BEGIN;

-- registry.agents
INSERT INTO registry.agents (id, name) VALUES ('agent-1', 'Test Agent');
SELECT 'registry.agents' AS table_name, id, name, status, risk FROM registry.agents WHERE id = 'agent-1';

-- registry.freeze_journal
INSERT INTO registry.freeze_journal (agent_id, kind, pid, reason)
VALUES ('agent-1', 'process', 1234, 'manual test freeze');
SELECT 'registry.freeze_journal' AS table_name, agent_id, kind, pid FROM registry.freeze_journal WHERE agent_id = 'agent-1';

-- registry.gateway_agents
INSERT INTO registry.gateway_agents (agent_id, name, route, endpoint_url, runtime, version, owner)
VALUES ('agent-1', 'Test Agent', '/test', 'http://localhost:9000', 'python', '1.0.0', 'test-owner');
SELECT 'registry.gateway_agents' AS table_name, agent_id, route, endpoint_url FROM registry.gateway_agents WHERE agent_id = 'agent-1';

-- governance.approval_tokens
INSERT INTO governance.approval_tokens (workflow_id, agent_id, action_class, expires_at)
VALUES ('wf-1', 'agent-1', 'write_scope_test', now() + interval '1 hour');
SELECT 'governance.approval_tokens' AS table_name, workflow_id, agent_id, status FROM governance.approval_tokens WHERE workflow_id = 'wf-1';

-- governance.policy_decisions (partitioned, ts within the existing 2026_06 partition)
INSERT INTO governance.policy_decisions (ts, agent_id, workflow_id, tool_call, decision)
VALUES ('2026-06-15', 'agent-1', 'wf-1', 'test.tool_call', 'auto_authorized');
SELECT 'governance.policy_decisions' AS table_name, agent_id, tool_call, decision FROM governance.policy_decisions WHERE workflow_id = 'wf-1';

-- governance.degradation_events
INSERT INTO governance.degradation_events (ts, agent_id, from_profile, to_profile, trigger)
VALUES ('2026-06-15', 'agent-1', 'active', 'recommendation_only', 'test_trigger');
SELECT 'governance.degradation_events' AS table_name, agent_id, from_profile, to_profile FROM governance.degradation_events WHERE agent_id = 'agent-1';

-- evidence.token_ledger
INSERT INTO evidence.token_ledger (ts, agent_id, task_id, model, input_tokens, output_tokens, cost)
VALUES ('2026-06-15', 'agent-1', 'task-1', 'claude-sonnet-4-6', 100, 50, 0.0123);
SELECT 'evidence.token_ledger' AS table_name, agent_id, model, input_tokens, output_tokens FROM evidence.token_ledger WHERE agent_id = 'agent-1';

-- evidence.ledger
INSERT INTO evidence.ledger (ts, workflow_id, agent_id, event_type)
VALUES ('2026-06-15', 'wf-1', 'agent-1', 'test_event');
SELECT 'evidence.ledger' AS table_name, workflow_id, agent_id, event_type FROM evidence.ledger WHERE workflow_id = 'wf-1';

-- ops.onboarding_runs
INSERT INTO ops.onboarding_runs (workflow_id, agent_id, state)
VALUES ('wf-1', 'agent-1', 'pending');
SELECT 'ops.onboarding_runs' AS table_name, workflow_id, agent_id, state FROM ops.onboarding_runs WHERE workflow_id = 'wf-1';

-- ops.artifacts
INSERT INTO ops.artifacts (id, agent_id, task_id, kind, storage_ref, bytes)
VALUES ('artifact-1', 'agent-1', 'task-1', 'log', 's3://bucket/key', 1024);
SELECT 'ops.artifacts' AS table_name, id, agent_id, kind FROM ops.artifacts WHERE id = 'artifact-1';

ROLLBACK;
