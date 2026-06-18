"""An autonomous, governed arXiv research WORKER runtime (AWCP agent-on-a-runtime).

Pulls research GOALS off a task queue and executes each in multiple steps:
  - read tools: search_arxiv, get_paper (free arXiv API), web_search
  - save_artifact  -> governed LOCAL write  (medium risk, gated)
  - external_post   -> governed EXTERNAL write (high risk, gated + needs approval)
Queue/worker/governance/approval/UI live in awcp_kit; this file supplies the
framework agent + the run_goal() hook.

Run as:  python arxiv_agent.py   (absolute path via run.sh so the detector sees
the `langgraph` import).
"""

import os

from langgraph.graph import StateGraph  # noqa: F401  (marks this as LangGraph)
from langgraph.prebuilt import create_react_agent
from langchain_ollama import ChatOllama

from fastapi import FastAPI
import uvicorn

import awcp_kit as kit

MODEL = os.getenv("ARXIV_MODEL", "llama3.1:8b")
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
PORT = int(os.getenv("ARXIV_PORT", "8103"))
HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = (
    "You are an autonomous research worker. Given a GOAL, use search_arxiv/get_paper "
    "for academic papers and web_search for general facts, deciding for yourself "
    "which tools apply. When you have a result, persist it with save_artifact, and if "
    "the goal asks to submit/send/publish it, call external_post. Cite paper titles "
    "and links. Base your answer on what your tools return."
)


# --- tools: discovered dynamically from the MCP server (NONE defined here) ----
# No tools are declared in this file. At startup the agent asks the MCP server for
# its catalog and binds it; each call runs on the server (governed + traced). The
# combined catalog includes the research tools (search_arxiv, get_paper, web_search)
# this agent's prompt steers it toward.
TOOLS = kit.build_tools("langgraph")
TOOL_NAMES = [t.name for t in TOOLS]

_llm = ChatOllama(model=MODEL, base_url=OLLAMA_BASE, temperature=0)
AGENT = create_react_agent(_llm, tools=TOOLS)


def run_goal(goal: str) -> dict:
    result = AGENT.invoke({"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": goal},
    ]})
    msgs = result["messages"]
    tools_used = [tc["name"] for m in msgs for tc in (getattr(m, "tool_calls", None) or [])]
    return {"result": msgs[-1].content, "tools_used": tools_used}


app = FastAPI(title="arXiv Research Worker Runtime")

if __name__ == "__main__":
    kit.mount(
        app,
        meta={"agent": "arXiv Research Worker", "framework": "langgraph",
              "model": MODEL, "tools": TOOL_NAMES, "dir": HERE,
              "purpose": "Academic research — finds arXiv papers and reports findings with citations and links.",
              "format": "markdown", "accent": "#e0492f", "logo": "\U0001F4DA",
              "examples": ["Find recent papers on retrieval-augmented generation and summarise them.",
                           "Summarise arXiv paper 2401.12345.",
                           "What are the key ideas in recent mixture-of-experts papers?"]},
        run_goal=run_goal,
        port=PORT,
    )
    print(f"📚 arXiv WORKER  →  http://localhost:{PORT}   (model={MODEL})")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
