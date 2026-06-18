"""An autonomous, governed PydanticAI WORKER runtime (AWCP agent-on-a-runtime).

Pulls GOALS off a task queue and executes each in multiple steps:
  - read/compute tools: web_search, multiply, add, word_count, current_time
  - save_artifact  -> governed LOCAL write  (medium risk, gated)
  - external_post   -> governed EXTERNAL write (high risk, gated + needs approval)
Queue/worker/governance/approval/UI live in awcp_kit; this file supplies the
PydanticAI agent + the run_goal() hook.

Run as:  python pydanticai_agent.py   (absolute path via run.sh so the detector sees
the `pydantic_ai` import).
"""

import os

from pydantic_ai import Agent  # noqa: F401  (import marks this as PydanticAI)
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from fastapi import FastAPI
import uvicorn

import awcp_kit as kit

MODEL = os.getenv("PAI_MODEL", "llama3.1:8b")
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
PORT = int(os.getenv("PAI_PORT", "8102"))
HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = (
    "You are a STRUCTURED-DATA EXTRACTION agent. Given a GOAL, gather the needed "
    "facts (use web_search for things you don't know, the math tools for arithmetic), "
    "then return your FINAL answer as a SINGLE valid JSON object that captures the "
    "requested information with clear snake_case keys and concise values. Output ONLY "
    "the JSON object — no prose, no explanation, no markdown code fences. If the goal "
    "asks to save or submit the result, call save_artifact / external_post first.")

_model = OpenAIModel(MODEL, provider=OpenAIProvider(base_url=f"{OLLAMA_BASE}/v1", api_key="ollama"))

# --- tools: discovered dynamically from the MCP server (NONE defined here) ----
# No tools are declared in this file. The agent fetches the MCP server's catalog
# and binds it; every call runs on the server (governed + traced).
_specs = kit.discover_tools()
TOOLS = kit.build_tools("pydantic_ai", _specs)
TOOL_NAMES = [s["name"] for s in _specs]

AGENT = Agent(_model, system_prompt=SYSTEM, tools=TOOLS)


def _tools_from_messages(messages) -> list[str]:
    used: list[str] = []
    for m in messages or []:
        for part in getattr(m, "parts", []) or []:
            if getattr(part, "part_kind", "") == "tool-call":
                n = getattr(part, "tool_name", None)
                if n and n not in used:
                    used.append(n)
    return used


def run_goal(goal: str) -> dict:
    res = AGENT.run_sync(goal)
    out = getattr(res, "output", None)
    if out is None:
        out = getattr(res, "data", None)
    return {"result": str(out), "tools_used": _tools_from_messages(res.all_messages())}


app = FastAPI(title="PydanticAI Worker Runtime")

if __name__ == "__main__":
    kit.mount(
        app,
        meta={"agent": "PydanticAI Extractor", "framework": "pydantic_ai",
              "model": MODEL, "tools": TOOL_NAMES, "dir": HERE,
              "purpose": "Structured-data extractor — returns clean, validated JSON for any query.",
              "format": "json", "accent": "#2a7de1", "logo": "\U0001F537",
              "examples": ["Extract the key facts about the Eiffel Tower as JSON.",
                           "Give me {name, capital, population, currency} for France.",
                           "Summarise the company Anthropic into structured fields."]},
        run_goal=run_goal,
        port=PORT,
    )
    print(f"🔷 PydanticAI WORKER  →  http://localhost:{PORT}   (model={MODEL})")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
