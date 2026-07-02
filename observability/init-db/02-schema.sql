CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS governance;
CREATE SCHEMA IF NOT EXISTS evidence;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE registry.agents (
    id                     text PRIMARY KEY,
    name                   text NOT NULL,
    kind                   text NOT NULL DEFAULT 'agent_framework',
    framework              text,
    source                 text NOT NULL DEFAULT 'scan'
                           CHECK (source IN ('scan','self')),
    status                 text NOT NULL DEFAULT 'quarantined'
                           CHECK (status IN ('quarantined','active')),
    quarantine_reason      text,

    autonomy_profile       text NOT NULL DEFAULT 'active'
                           -- full AWCP degradation ladder (policy.DEFAULT_PROFILE_LADDER):
                           -- active -> trace_boost -> throttled -> safe_profile ->
                           -- recommendation_only -> suspended
                           CHECK (autonomy_profile IN ('active','trace_boost','throttled',
                                                       'safe_profile','recommendation_only','suspended')),
    autonomy_reason        text,
    failure_count          integer NOT NULL DEFAULT 0,

    owner                  text,
    runtime                text,
    version                text,
    write_scopes           text[] NOT NULL DEFAULT '{}',
    feature_flags          jsonb  NOT NULL DEFAULT '{}',
    flags_observed         boolean NOT NULL DEFAULT false,
    last_flags_ts          timestamptz,
    telemetry_enabled      boolean NOT NULL DEFAULT false,
    last_telemetry_ts      timestamptz,
    policy_callbacks       text[] NOT NULL DEFAULT '{}',
    policy_observed        boolean NOT NULL DEFAULT false,
    last_policy_ts         timestamptz,

    risk                   text NOT NULL DEFAULT 'medium'
                           CHECK (risk IN ('low','medium','high')),
    autonomy_ladder        text[] NOT NULL DEFAULT '{}',
    failure_budget         integer,
    token_budget           integer,

    endpoint               text,
    transport              text,
    capabilities           text[] NOT NULL DEFAULT '{}',
    control_endpoint       text,

    pid                    integer,
    os_user                text,
    cwd                    text,
    cmdline                text,
    detected_via           text,

    onboarding_state       text,
    onboarding_workflow_id text,

    first_seen             timestamptz NOT NULL DEFAULT now(),
    last_seen              timestamptz NOT NULL DEFAULT now(),
    alive                  boolean NOT NULL DEFAULT true,

    -- AgentCard (A2A description layer — additive enrichment over governance).
    -- All nullable / defaulted, so existing rows are backward-compatible.
    card                   jsonb,
    card_url               text,
    card_fetched_at        timestamptz,
    skills                 text[] NOT NULL DEFAULT '{}',

    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_agents_status    ON registry.agents (status);
CREATE INDEX idx_agents_owner     ON registry.agents (owner);
CREATE INDEX idx_agents_source    ON registry.agents (source);
CREATE INDEX idx_agents_risk      ON registry.agents (risk);
CREATE INDEX idx_agents_lastseen  ON registry.agents (last_seen);
CREATE INDEX idx_agents_flags_gin ON registry.agents USING gin (feature_flags);
CREATE INDEX idx_agents_skills_gin ON registry.agents USING gin (skills);

-- Migration for an ALREADY-initialised DB (the radar also self-applies this at
-- startup via store._ensure_card_columns, mirroring ensure_operator_policy_table):
-- ALTER TABLE registry.agents
--     ADD COLUMN IF NOT EXISTS card            jsonb,
--     ADD COLUMN IF NOT EXISTS card_url        text,
--     ADD COLUMN IF NOT EXISTS card_fetched_at timestamptz,
--     ADD COLUMN IF NOT EXISTS skills          text[] NOT NULL DEFAULT '{}';
-- CREATE INDEX IF NOT EXISTS idx_agents_skills_gin ON registry.agents USING gin (skills);

CREATE TABLE registry.freeze_journal (
    agent_id     text PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('process','remote')),
    pid          integer,
    create_time  double precision,
    url          text,
    reason       text,
    payload      jsonb NOT NULL DEFAULT '{}',
    frozen_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE registry.gateway_agents (
    agent_id      text PRIMARY KEY,
    name          text NOT NULL,
    route         text NOT NULL,
    endpoint_url  text NOT NULL,
    runtime       text NOT NULL,
    version       text NOT NULL,
    owner         text NOT NULL,
    write_scopes  text[] NOT NULL DEFAULT '{}',
    feature_flags jsonb  NOT NULL DEFAULT '{}',
    status        text NOT NULL DEFAULT 'active',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_gateway_route ON registry.gateway_agents (route);

CREATE TABLE governance.approval_tokens (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id      text NOT NULL,
    branch_id        text,
    agent_id         text REFERENCES registry.agents(id) ON DELETE SET NULL,
    action_class     text NOT NULL,
    write_scopes     text[] NOT NULL DEFAULT '{}',
    risk             numeric(4,3),
    status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','approved','denied','expired','consumed')),
    requested_by     text,
    decided_by       text,
    context_diff     jsonb NOT NULL DEFAULT '{}',
    rollback_pointer text,
    max_uses         integer NOT NULL DEFAULT 1,
    uses             integer NOT NULL DEFAULT 0,
    requested_at     timestamptz NOT NULL DEFAULT now(),
    decided_at       timestamptz,
    expires_at       timestamptz NOT NULL
);

CREATE INDEX idx_tokens_workflow ON governance.approval_tokens (workflow_id);
CREATE INDEX idx_tokens_status   ON governance.approval_tokens (status);
CREATE INDEX idx_tokens_expiry   ON governance.approval_tokens (expires_at)
                                  WHERE status IN ('pending','approved');

CREATE TABLE governance.policy_decisions (
    id           bigint GENERATED ALWAYS AS IDENTITY,
    ts           timestamptz NOT NULL DEFAULT now(),
    agent_id     text,
    workflow_id  text,
    branch_id    text,
    tool_call    text NOT NULL,
    risk         numeric(4,3),
    decision     text NOT NULL
                 CHECK (decision IN ('auto_authorized','awaiting_token','awaiting_operator','denied')),
    scope        text,
    reason       text,
    token_id     uuid,
    payload      jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX idx_poldec_agent ON governance.policy_decisions (agent_id, ts);

CREATE TABLE governance.degradation_events (
    id             bigint GENERATED ALWAYS AS IDENTITY,
    ts             timestamptz NOT NULL DEFAULT now(),
    agent_id       text,
    workflow_id    text,
    from_profile   text,
    to_profile     text,
    trigger        text,
    trace_sampling numeric(4,3),
    reason         text,
    payload        jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX idx_degr_agent ON governance.degradation_events (agent_id, ts);

-- OPA agent (hidden tool-call PDP) durable stores. Replaces the agent's former
-- on-disk JSON tier cache + in-memory decision ring, so the SLM-reasoned tier per
-- tool and every tool-call decision survive a restart and are queryable.
CREATE TABLE governance.tool_tiers (
    tool_name  text PRIMARY KEY,
    tier       text NOT NULL,
    reason     text,
    engine     text,
    model      text,
    ts         double precision,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE governance.tool_call_evaluations (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    task_id    text,
    agent_id   text,
    tool_name  text NOT NULL,
    risk_tier  text,
    decision   text NOT NULL,
    reason     text,
    reasoning  text,
    engine     text,
    question   text
);

CREATE INDEX idx_tooleval_task ON governance.tool_call_evaluations (task_id, ts);
CREATE INDEX idx_tooleval_ts   ON governance.tool_call_evaluations (ts DESC);

-- Operator-authored policy (the Radar "Policy" tab). An operator types a single
-- JSON document that names which detected agents are RECOGNISED (allowed) and at
-- what risk tier, and the same for tools. The OPA agent still assigns a baseline
-- risk tier first; this policy is consulted AFTER, as an operator override /
-- allowlist on top. Append-only + versioned: every save is a new row, the ACTIVE
-- policy is the most-recent row, so prior policies are retained as history. The
-- whole layer is inert when no row exists (detection + governance unchanged).
CREATE TABLE governance.operator_policy (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    version    integer NOT NULL DEFAULT 1,
    updated_by text,
    note       text,
    policy     jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_oppolicy_ts ON governance.operator_policy (ts DESC);

CREATE TABLE evidence.token_ledger (
    id            bigint GENERATED ALWAYS AS IDENTITY,
    ts            timestamptz NOT NULL DEFAULT now(),
    agent_id      text NOT NULL,
    task_id       text,
    step          text,
    model         text NOT NULL DEFAULT 'unknown',
    input_tokens  integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost          numeric(18,6) NOT NULL DEFAULT 0,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX idx_ledger_agent_ts ON evidence.token_ledger (agent_id, ts);
CREATE INDEX idx_ledger_model    ON evidence.token_ledger (model);

CREATE TABLE evidence.ledger (
    id                uuid NOT NULL DEFAULT gen_random_uuid(),
    ts                timestamptz NOT NULL DEFAULT now(),
    workflow_id       text NOT NULL,
    branch_id         text,
    agent_id          text,
    actor             text,
    event_type        text NOT NULL,
    step              text,
    policy_result     text,
    approval_token_id uuid,
    tool_call         jsonb,
    context_hash      text,
    degradation_state text,
    resume_pointer    text,
    rollback_pointer  text,
    prev_hash         text,
    row_hash          text,
    payload           jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX idx_evledger_workflow ON evidence.ledger (workflow_id, ts);
CREATE INDEX idx_evledger_event    ON evidence.ledger (event_type, ts);

CREATE TABLE ops.onboarding_runs (
    workflow_id  text PRIMARY KEY,
    agent_id     text REFERENCES registry.agents(id) ON DELETE CASCADE,
    state        text NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending','running','done')),
    patch_ref    text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    payload      jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE ops.artifacts (
    id          text PRIMARY KEY,
    agent_id    text,
    task_id     text,
    kind        text,
    storage_ref text,
    bytes       integer,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- App-level MIRROR of every Temporal ACTIVITY execution (onboarding + execution
-- workflows). Temporal keeps its own (dev-mode SQLite) workflow history; this is
-- the durable, queryable copy in the canonical schema so a run survives a
-- Temporal/gateway restart and is visible in Adminer. Written best-effort by
-- awcp.radar.db.record_workflow_event from inside each activity.
CREATE TABLE ops.workflow_events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    workflow_id   text NOT NULL,
    run_id        text,
    workflow_type text,
    activity_type text NOT NULL,
    agent_id      text,
    task_id       text,
    attempt       integer,
    input         jsonb NOT NULL DEFAULT '{}',
    output        text,
    status        text NOT NULL DEFAULT 'completed'
);

CREATE INDEX idx_wfevents_wf    ON ops.workflow_events (workflow_id, ts);
CREATE INDEX idx_wfevents_ts    ON ops.workflow_events (ts DESC);
CREATE INDEX idx_wfevents_agent ON ops.workflow_events (agent_id, ts);

-- Sandbox lifecycle + tool-call timeline. The MCP server owns a single
-- OpenSandbox container and records every create/ready/close and
-- read_file/write_file/run_command into an in-memory ring; this is its durable
-- mirror so the UI Sandbox page's timeline survives a server restart. event_ts is
-- the original epoch (ordering matches the ring); payload holds any extra kwargs.
-- Written best-effort by awcp.runtime.sandbox_db.record from record_event (also
-- created on an already-initialised volume via sandbox_db._create_table).
CREATE TABLE ops.sandbox_events (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    event_ts  double precision NOT NULL,
    kind      text NOT NULL,
    detail    text,
    payload   jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_sandbox_events_event_ts ON ops.sandbox_events (event_ts DESC);

-- Per-chat conversation history — one row per completed agent turn. Replaces the
-- ad-hoc artifacts/ folder as the durable record of what a user asked and what an
-- agent answered, and is the backing store for BOTH (a) per-chat context memory
-- (the agent reads prior turns of the same session_id before it runs so it can
-- reference earlier context) and (b) the task console's inline context-window
-- meter (Σ total_tokens per session vs AWCP_CONTEXT_WINDOW_TOKENS). Written
-- best-effort by the gateway (awcp.gateway.chat_store) from POST /user/chat/turn;
-- created on an already-initialised volume via chat_store._create_table().
CREATE TABLE ops.chat_turns (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    session_id    text NOT NULL,        -- the chat this turn belongs to
    seq           integer NOT NULL DEFAULT 0,   -- turn order within the chat
    task_id       text,
    workflow_id   text,
    agent_id      text,
    agent_name    text,
    framework     text,
    model         text,
    input         text,                 -- the user's input prompt
    output        text,                 -- the agent's output / result
    tools_used    jsonb   NOT NULL DEFAULT '[]',   -- the tool calls this turn made
    status        text,                 -- done | blocked | canceled | failed
    input_tokens  integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    total_tokens  integer NOT NULL DEFAULT 0,
    created_ts    double precision,     -- task lifecycle epochs (timing)
    started_ts    double precision,
    finished_ts   double precision,
    duration_ms   integer NOT NULL DEFAULT 0
);

CREATE INDEX idx_chat_turns_session ON ops.chat_turns (session_id, ts);
CREATE INDEX idx_chat_turns_ts      ON ops.chat_turns (ts DESC);

CREATE TABLE evidence.token_ledger_2026_06 PARTITION OF evidence.token_ledger
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE evidence.ledger_2026_06 PARTITION OF evidence.ledger
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE governance.policy_decisions_2026_06 PARTITION OF governance.policy_decisions
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE governance.degradation_events_2026_06 PARTITION OF governance.degradation_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

GRANT USAGE ON SCHEMA registry, governance, evidence, ops TO awcp_app, awcp_ro;

GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA registry, governance, ops TO awcp_app;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA evidence TO awcp_app;

GRANT SELECT ON ALL TABLES IN SCHEMA registry, governance, evidence, ops TO awcp_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA evidence
  GRANT SELECT, INSERT ON TABLES TO awcp_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA registry, governance, ops
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO awcp_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA registry, governance, evidence, ops
  GRANT SELECT ON TABLES TO awcp_ro;
