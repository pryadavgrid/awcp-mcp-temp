# AWCP governance policy — policy-as-code mirror of
# awcp.radar.policy.evaluate_action, extended with the magazine's Step 03
# "requires approval" outcome.
#
# Division of labour (matches the magazine: Step 03 gate is declarative, Step 04
# degradation is the stateful ladder): AWCP computes the degradation-ladder facts
# in Python — the agent's current rung, whether writes are blocked at this rung,
# and hard stop — and passes them in as input.agent.*; OPA owns the DECLARATIVE
# decision ON TOP: reads pass, quarantine / out-of-scope / ladder deny, and
# high-risk writes require a valid approval token.
#
# Nothing here is hardcoded per agent: the dangerous-tool list and the high-risk
# tiers come from data/awcp.json (data.awcp.*), editable without touching code.
#
# Query path: POST /v1/data/awcp/governance/decision -> {"result": {...}}.
package awcp.governance

import rego.v1

is_write := input.action.write == true

scopes := {s | some s in input.agent.write_scopes}

quarantined if input.agent.status == "quarantined"

out_of_scope if {
	input.action.scope != ""
	not input.action.scope in scopes
}

hard_stop if input.agent.hard_stop == true

write_blocked if input.agent.write_blocked == true

# High risk comes from the agent's risk tier (for any write) OR from the tool
# itself being on the dangerous-tools list — both lists live in data/awcp.json.
high_risk if {
	is_write
	input.agent.risk in {t | some t in data.awcp.high_risk_tiers}
}

high_risk if input.action.tool_name in {t | some t in data.awcp.dangerous_tools}

token_valid if input.approval.token_valid == true

mode := input.agent.autonomy_profile

# Single decision object, evaluated by precedence via an else-chain so exactly one
# branch wins (read -> quarantine -> scope -> hard stop -> write block ->
# approval -> allow). Shape matches the radar's Python gate response.
decision := {
	"decision": "allow",
	"reason": "read-only action — not gated",
	"mode": mode,
	"requires_approval": false,
	"policy_id": "awcp.governance.read_allow",
} if {
	not is_write
} else := {
	"decision": "deny",
	"reason": "agent is quarantined — write actions blocked until onboarded",
	"mode": "quarantined",
	"requires_approval": false,
	"policy_id": "awcp.governance.quarantine_deny",
} if {
	quarantined
} else := {
	"decision": "deny",
	"reason": sprintf("action scope %q is not in the agent's declared write_scopes %v", [input.action.scope, input.agent.write_scopes]),
	"mode": "out_of_scope",
	"requires_approval": false,
	"policy_id": "awcp.governance.scope_deny",
} if {
	out_of_scope
} else := {
	"decision": "deny",
	"reason": "agent at hard stop — no actions permitted",
	"mode": mode,
	"requires_approval": false,
	"policy_id": "awcp.governance.hard_stop_deny",
} if {
	hard_stop
} else := {
	"decision": "deny",
	"reason": "autonomy reduced — recommend, do not execute",
	"mode": mode,
	"requires_approval": false,
	"policy_id": "awcp.governance.write_block_deny",
} if {
	write_blocked
} else := {
	"decision": "deny",
	"reason": "high-risk write requires approval token",
	"mode": mode,
	"requires_approval": true,
	"approval_scope": input.action.scope,
	"policy_id": "awcp.governance.requires_approval",
} if {
	high_risk
	not token_valid
} else := {
	"decision": "allow",
	"reason": "approved by OPA policy",
	"mode": mode,
	"requires_approval": false,
	"policy_id": "awcp.governance.allow",
}
