"""Seed the running registry with YOUR five agents — the push path, on their behalf.

The live agents don't self-register yet (that would need an awcp_kit change, which
we are intentionally NOT making). So this script POSTs an Agent Card for each agent
to the registry — exactly what `awcp_kit._radar_register()` *would* send. After it
runs, refresh the panel and all five appear (quarantined), ready to approve.

    python3 server.py        # terminal 1  (http://localhost:8090)
    python3 seed.py          # terminal 2  → refresh the panel

Each entry below mirrors that agent's meta={...} dict + default port from its own
agent_runtime.py. (In production this list goes away — each agent registers itself.)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from agent_card import registration_from_meta

REGISTRY = os.getenv("AWCP_REGISTRY", "http://localhost:8090")
OWNER = os.getenv("AWCP_OWNER", "prateek")

# tools include the governed writes so requested_write_scopes derive correctly.
_WRITES = ["web_search", "save_artifact", "external_post"]

AGENTS = [
    {"port": 8100, "meta": {
        "agent": "LangGraph Orchestrator", "framework": "langgraph",
        "model": "llama3.1:8b", "tools": _WRITES, "format": "markdown",
        "purpose": "General research & compute orchestrator — multi-step web + math, then a clear written answer.",
        "examples": ["What is 25 × 4? Report it.",
                     "Research who won the 2024 Booker Prize and summarise it."]}},
    {"port": 8101, "meta": {
        "agent": "CrewAI Writer", "framework": "crewai",
        "model": "ollama/llama3.1:8b", "tools": _WRITES, "format": "markdown",
        "purpose": "Content & report writer — researches a topic, then drafts a structured write-up.",
        "examples": ["Write a short brief on the benefits of solar energy."]}},
    {"port": 8102, "meta": {
        "agent": "PydanticAI Extractor", "framework": "pydantic_ai",
        "model": "llama3.1:8b", "tools": _WRITES, "format": "json",
        "purpose": "Structured-data extractor — returns clean, validated JSON for any query.",
        "examples": ["Give me {name, capital, population, currency} for France."]}},
    {"port": 8103, "meta": {
        "agent": "arXiv Research Worker", "framework": "langgraph",
        "model": "llama3.1:8b", "tools": _WRITES, "format": "markdown",
        "purpose": "Academic research — finds arXiv papers and reports findings with citations and links.",
        "examples": ["Find recent papers on retrieval-augmented generation and summarise them."]}},
    {"port": 8104, "meta": {
        "agent": "File Inspector", "framework": "langgraph",
        "model": "qwen2.5:7b", "tools": _WRITES, "format": "markdown",
        "purpose": "Universal file inspector — identify any file and explain what's inside.",
        "examples": ["Identify this file and summarise its contents."]}},
]


def post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        REGISTRY + path, data=data,
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:   # noqa: S310
        return json.loads(r.read().decode() or "{}")


def main() -> None:
    print(f"seeding {len(AGENTS)} agents → {REGISTRY}\n")
    for a in AGENTS:
        reg = registration_from_meta(
            a["meta"], url=f"http://localhost:{a['port']}", owner=OWNER,
            intake="self-register")
        try:
            res = post("/v1/agents", reg)
        except urllib.error.URLError as exc:
            print(f"  ✗ could not reach {REGISTRY} — is server.py running?  ({exc})")
            return
        flag = "✓" if res.get("ok") else "✗"
        print(f"  {flag} {a['meta']['agent']:<22} id={res.get('id', res.get('error'))}")
    print("\nDone. Refresh the panel — the agents are QUARANTINED; click Approve to govern them.")


if __name__ == "__main__":
    main()
