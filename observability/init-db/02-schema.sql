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

    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_agents_status    ON registry.agents (status);
CREATE INDEX idx_agents_owner     ON registry.agents (owner);
CREATE INDEX idx_agents_source    ON registry.agents (source);
CREATE INDEX idx_agents_risk      ON registry.agents (risk);
CREATE INDEX idx_agents_lastseen  ON registry.agents (last_seen);
CREATE INDEX idx_agents_flags_gin ON registry.agents USING gin (feature_flags);

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
