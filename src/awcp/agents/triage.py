"""A coordinator agent that hands factual questions off to a sub-agent.

Demonstrates single-level, governed delegation:
  - For a factual query, its router returns DELEGATE -> "ollama-search".
  - The control plane (Temporal) then runs the sub-agent's OWN governed loop
    (admission -> route -> tool -> generate), so the sub-agent's web_search call
    is itself a recorded, gated activity.
  - For simple queries it just answers directly (no handoff).

It declares no tool of its own — its capability is the sub-agent it may call.
"""

from typing import Any

from awcp.agents.base import AgentSpec
from awcp.agents.ollama_search import should_search
from awcp.runtime.config import GEMMA_MODEL
from awcp.runtime.ollama_client import ask_ollama
from awcp.runtime.schemas import PromptRequest

SUB_AGENT = "ollama-search"


def route(prompt: str) -> dict[str, Any]:
    """Hand factual/lookup questions to the search sub-agent; answer the rest."""
    if should_search(prompt):
        return {"action": "DELEGATE", "agent": SUB_AGENT}
    return {"action": "ANSWER"}


def run(req: PromptRequest) -> dict[str, Any]:
    """Direct REST-path handler (no delegation here — that lives in the workflow)."""
    output = ask_ollama(req.input, GEMMA_MODEL)
    return {"input": req.input, "model": GEMMA_MODEL, "output": output}


AGENT = AgentSpec(
    name="triage",
    route="/chat/triage",
    request_model=PromptRequest,
    handler=run,
    runtime="ollama",
    model=GEMMA_MODEL,             # its own model for direct answers
    router=route,                  # decides when to hand off
    delegates_to=[SUB_AGENT],      # …and the only sub-agent it may hand off to
)
