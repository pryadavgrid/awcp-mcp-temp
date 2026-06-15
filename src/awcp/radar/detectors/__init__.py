"""Detector registry: one process pass, classifiers applied in priority order."""

from __future__ import annotations

import psutil

from awcp.radar.models import AgentEntry, KIND_LLM_RUNTIME
from awcp.radar.detectors.base import is_self, is_excluded
from awcp.radar.detectors import (
    mcp_servers,
    orchestrators,
    llm_runtimes,
    frameworks,
)

# priority order — first classifier that returns an entry wins for a process.
CLASSIFIERS = [
    mcp_servers.classify,
    orchestrators.classify,
    llm_runtimes.classify,
    frameworks.classify,
]


def scan_all() -> list[AgentEntry]:
    """Scan every process once and classify it into at most one env kind."""
    found: list[AgentEntry] = []
    for proc in psutil.process_iter():
        try:
            info = proc.as_dict(
                attrs=["pid", "name", "cmdline", "username", "create_time"]
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if is_self(info) or is_excluded(info):
            continue
        for classify in CLASSIFIERS:
            try:
                entry = classify(proc, info)
            except Exception:
                entry = None
            if entry:
                found.append(entry)
                break

    # An LLM runtime (e.g. the Ollama GUI app + its `serve` child) can surface as
    # several processes sharing one endpoint — collapse those to one entry.
    deduped: list[AgentEntry] = []
    seen_llm_endpoints: set[str] = set()
    for e in found:
        if e.kind == KIND_LLM_RUNTIME and e.endpoint:
            if e.endpoint in seen_llm_endpoints:
                continue
            seen_llm_endpoints.add(e.endpoint)
        deduped.append(e)
    return deduped
