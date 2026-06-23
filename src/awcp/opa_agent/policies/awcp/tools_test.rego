# Unit tests for the AWCP tool-call tier gate. Run with:  opa test policies/
#
# Each case locks in the block/allow rule so the Rego stays a faithful mirror of
# the OPA agent's Python fallback (block iff tier ∈ block set).
package awcp.tools_test

import data.awcp.tools
import rego.v1

_block := ["high", "severe"]

test_low_is_allowed if {
	r := tools.result with input as {"tool": "read_file", "risk_tier": "low", "block_tiers": _block}
	r.block == false
	r.decision == "allow"
}

test_medium_is_allowed if {
	r := tools.result with input as {"tool": "search_arxiv", "risk_tier": "medium", "block_tiers": _block}
	r.block == false
}

test_high_is_blocked if {
	r := tools.result with input as {"tool": "web_search", "risk_tier": "high", "block_tiers": _block}
	r.block == true
	r.decision == "block"
}

test_severe_is_blocked if {
	r := tools.result with input as {"tool": "run_command", "risk_tier": "severe", "block_tiers": _block}
	r.block == true
}

test_block_set_is_honored if {
	# When only "severe" blocks, a high-risk tool passes.
	r := tools.result with input as {"tool": "web_search", "risk_tier": "high", "block_tiers": ["severe"]}
	r.block == false
}
