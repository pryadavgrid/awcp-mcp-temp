# AWCP write-action gate — the Policy Decision Point (PDP).
#
# This Rego is a faithful re-expression of radar/policy.py:evaluate_action, plus
# the magazine's richer decision vocabulary used by governance.policy_decisions:
#
#     auto_authorized | awaiting_token | awaiting_operator | denied
#
# The enforcement point (radar/api.py:gate via radar/opa.py) sends an `input`
# document describing ONE action and the agent's resolved governance state, and
# reads `data.awcp.gate.result`. OPA owns NO state of its own — every fact it
# needs (the resolved ladder, authoritative risk, the write-block stages, the
# tiers that require a token) is passed in `input`, so the same Python that builds
# the input also provides the fail-secure fallback. Nothing is hardcoded per agent.
#
# Decision precedence (top wins), mirroring evaluate_action's order exactly:
#   1. read-only action                         -> auto_authorized (allow)
#   2. quarantined agent                        -> denied
#   3. action scope not in declared write_scopes-> denied
#   4. agent at the hard-stop (last) rung       -> denied
#   5. autonomy reduced to a write-block rung   -> denied
#   6. allowed write, operator-class action     -> awaiting_operator (hold)
#   7. allowed write, risk tier requires token  -> awaiting_token (hold)
#   8. otherwise                                -> auto_authorized (allow)
package awcp.gate

import rego.v1

agent := input.agent

ladder := agent.ladder

# Current rung index within the agent's resolved ladder (0 == full autonomy).
# Falls back to 0 when the profile isn't found — same as policy._rung.
cur_idx := i if {
	some i
	ladder[i] == agent.autonomy_profile
}

default cur_idx := 0

# First ladder index from which writes are blocked. Uses the explicit
# write_block_stages when one appears in the ladder (so graceful rungs before it
# keep writing); otherwise index 1 — matching policy._write_block_index.
_block_idxs := [i | some i; ladder[i] in {s | some s in input.config.write_block_stages}]

write_block_idx := sort(_block_idxs)[0] if count(_block_idxs) > 0

default write_block_idx := 1

# The last rung is a hard stop only when the ladder has more than one rung,
# exactly like policy.evaluate_action.
is_hard_stop if {
	count(ladder) > 1
	cur_idx == count(ladder) - 1
}

scope_declared if input.scope in agent.write_scopes

needs_operator if {
	input.action_class != ""
	input.action_class in {c | some c in input.config.operator_action_classes}
}

needs_token if agent.risk in {t | some t in input.config.token_risk_tiers}

# ── the decision, in precedence order via an else-chain ───────────────────────
result := {
	"gate": "auto_authorized",
	"decision": "allow",
	"reason": "read-only action — not gated",
	"mode": agent.autonomy_profile,
} if {
	not input.is_write
} else := {
	"gate": "denied",
	"decision": "deny",
	"reason": "agent is quarantined — write actions blocked until onboarded",
	"mode": "quarantined",
} if {
	agent.status == "quarantined"
} else := {
	"gate": "denied",
	"decision": "deny",
	"reason": sprintf("action scope %q is not in the agent's declared write_scopes", [input.scope]),
	"mode": "out_of_scope",
} if {
	input.scope != ""
	not scope_declared
} else := {
	"gate": "denied",
	"decision": "deny",
	"reason": sprintf("agent at hard stop ('%s') — no actions permitted", [agent.autonomy_profile]),
	"mode": agent.autonomy_profile,
} if {
	is_hard_stop
} else := {
	"gate": "denied",
	"decision": "deny",
	"reason": sprintf("autonomy reduced ('%s') — recommend, do not execute", [agent.autonomy_profile]),
	"mode": agent.autonomy_profile,
} if {
	cur_idx >= write_block_idx
} else := {
	"gate": "awaiting_operator",
	"decision": "deny",
	"reason": sprintf("action class '%s' requires operator approval", [input.action_class]),
	"mode": "operator_required",
} if {
	needs_operator
} else := {
	"gate": "awaiting_token",
	"decision": "deny",
	"reason": sprintf("high-risk write (risk '%s') requires an approval token", [agent.risk]),
	"mode": "token_required",
} if {
	needs_token
} else := {
	"gate": "auto_authorized",
	"decision": "allow",
	"reason": "approved",
	"mode": agent.autonomy_profile,
}
