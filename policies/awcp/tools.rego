# AWCP tool-call tier gate — the per-tool Policy Decision Point.
#
# The OPA Agent (src/awcp/opa_agent) sends an `input` describing ONE tool call
# and reads `data.awcp.tools.result`. Like gate.rego, OPA owns NO state: the tool's
# resolved risk tier and the set of blocking tiers are passed in `input`, so the
# Python fallback in the OPA agent stays a faithful mirror. Nothing is hardcoded
# per tool — the tier is REASONED by a small language model in the OPA agent.
#
#   input.tool         "web_search"               the tool being called
#   input.risk_tier    "low|medium|high|severe"   its SLM-reasoned tier
#   input.block_tiers  ["high","severe"]          tiers that block the answer
#
# Decision:  block iff the tool's tier is in the block set; otherwise allow.
package awcp.tools

import rego.v1

# Is this tool's tier one of the blocking tiers?
blocked if input.risk_tier in {t | some t in input.block_tiers}

result := {
	"block": true,
	"decision": "block",
	"reason": sprintf("tool %q is %q risk — blocked by OPA tool policy", [input.tool, input.risk_tier]),
} if {
	blocked
} else := {
	"block": false,
	"decision": "allow",
	"reason": sprintf("tool %q is %q risk — allowed", [input.tool, input.risk_tier]),
}
