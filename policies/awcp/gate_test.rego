# Unit tests for the AWCP write-action gate. Run with:  opa test policies/
#
# Each case locks in one branch of the decision precedence so the Rego stays a
# faithful mirror of radar/policy.py:evaluate_action (and its Python fallback).
package awcp.gate_test

import data.awcp.gate
import rego.v1

# A standard config matching radar/policy.py defaults; token tier opt-in is "high".
_config := {
	"write_block_stages": ["recommendation_only", "suspended"],
	"token_risk_tiers": ["high"],
	"operator_action_classes": ["cross_system"],
}

_ladder := ["active", "trace_boost", "throttled", "safe_profile", "recommendation_only", "suspended"]

_agent(profile, status, risk, scopes) := {
	"id": "a1",
	"status": status,
	"autonomy_profile": profile,
	"write_scopes": scopes,
	"ladder": _ladder,
	"risk": risk,
}

test_read_is_always_allowed if {
	r := gate.result with input as {
		"is_write": false, "scope": "", "action": "lookup", "action_class": "",
		"agent": _agent("active", "active", "high", []), "config": _config,
	}
	r.decision == "allow"
	r.gate == "auto_authorized"
}

test_quarantined_write_denied if {
	r := gate.result with input as {
		"is_write": true, "scope": "", "action": "post", "action_class": "",
		"agent": _agent("active", "quarantined", "low", []), "config": _config,
	}
	r.decision == "deny"
	r.gate == "denied"
	r.mode == "quarantined"
}

test_out_of_scope_denied if {
	r := gate.result with input as {
		"is_write": true, "scope": "billing", "action": "charge", "action_class": "",
		"agent": _agent("active", "active", "low", ["ticketing"]), "config": _config,
	}
	r.decision == "deny"
	r.mode == "out_of_scope"
}

test_in_scope_low_risk_auto_authorized if {
	r := gate.result with input as {
		"is_write": true, "scope": "ticketing", "action": "create", "action_class": "",
		"agent": _agent("active", "active", "low", ["ticketing"]), "config": _config,
	}
	r.decision == "allow"
	r.gate == "auto_authorized"
}

test_recommendation_only_denied if {
	r := gate.result with input as {
		"is_write": true, "scope": "", "action": "post", "action_class": "",
		"agent": _agent("recommendation_only", "active", "low", []), "config": _config,
	}
	r.decision == "deny"
	r.gate == "denied"
}

test_graceful_rung_still_writes if {
	# throttled is BEFORE recommendation_only — writes still allowed.
	r := gate.result with input as {
		"is_write": true, "scope": "", "action": "post", "action_class": "",
		"agent": _agent("throttled", "active", "low", []), "config": _config,
	}
	r.decision == "allow"
}

test_hard_stop_denied if {
	r := gate.result with input as {
		"is_write": true, "scope": "", "action": "post", "action_class": "",
		"agent": _agent("suspended", "active", "low", []), "config": _config,
	}
	r.decision == "deny"
}

test_high_risk_write_awaits_token if {
	r := gate.result with input as {
		"is_write": true, "scope": "", "action": "deploy", "action_class": "",
		"agent": _agent("active", "active", "high", []), "config": _config,
	}
	r.decision == "deny"
	r.gate == "awaiting_token"
	r.mode == "token_required"
}

test_operator_class_awaits_operator if {
	r := gate.result with input as {
		"is_write": true, "scope": "", "action": "bulk_update", "action_class": "cross_system",
		"agent": _agent("active", "active", "high", []), "config": _config,
	}
	r.decision == "deny"
	r.gate == "awaiting_operator"
}

test_token_tiers_empty_means_parity if {
	# With no risk tier requiring a token, a high-risk write is auto_authorized
	# (exact parity with today's behaviour).
	cfg := {"write_block_stages": ["recommendation_only", "suspended"], "token_risk_tiers": [], "operator_action_classes": []}
	r := gate.result with input as {
		"is_write": true, "scope": "", "action": "deploy", "action_class": "",
		"agent": _agent("active", "active", "high", []), "config": cfg,
	}
	r.decision == "allow"
	r.gate == "auto_authorized"
}
