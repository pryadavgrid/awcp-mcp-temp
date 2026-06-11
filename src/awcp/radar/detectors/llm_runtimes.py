"""Detect local LLM runtimes (Ollama / LM Studio / vLLM)."""

from __future__ import annotations

import os

import psutil

from awcp.radar.models import AgentEntry, KIND_LLM_RUNTIME
from awcp.radar.detectors.base import make_entry
from awcp.radar.detectors.ports import tcp_open

# (signature, framework label, default port)
_LLM_SIGNS: list[tuple[str, str, int]] = [
    ("ollama", "ollama", 11434),
    ("vllm", "vllm", 8000),
    ("lm studio", "lmstudio", 1234),
    ("lmstudio", "lmstudio", 1234),
]
# Extend the seed list via env: AGENT_RADAR_LLM_SIGNS="name:port,other:port".
for _spec in os.getenv("AGENT_RADAR_LLM_SIGNS", "").split(","):
    if ":" in _spec:
        _nm, _, _pt = _spec.partition(":")
        if _nm.strip() and _pt.strip().isdigit():
            _LLM_SIGNS.append((_nm.strip().lower(), _nm.strip().lower(), int(_pt)))


def _is_runtime(sign: str, name: str, cmdline_list: list[str]) -> bool:
    """Strict match so processes that merely *mention* a runtime in a long
    display name (e.g. Cursor's 'extension-host … Ollama' helpers) are excluded."""
    if name == sign:
        return True
    toks = [t.lower() for t in cmdline_list]
    bases = [os.path.basename(t) for t in toks]
    if bases and bases[0] == sign:
        return True
    if sign in bases:                       # a bare token equals the runtime
        return True
    for i, t in enumerate(toks):            # python -m vllm ...
        if t == "-m" and i + 1 < len(toks) and toks[i + 1].split(".")[0] == sign:
            return True
    cmd = " ".join(toks)
    return f"{sign} serve" in cmd


def classify(proc: psutil.Process, info: dict) -> AgentEntry | None:
    name = (info.get("name") or "").lower()
    cmdline_list = info.get("cmdline") or []

    for sign, framework, port in _LLM_SIGNS:
        if _is_runtime(sign, name, cmdline_list):
            endpoint = f"http://127.0.0.1:{port}" if tcp_open(port) else None
            return make_entry(
                info, proc, kind=KIND_LLM_RUNTIME,
                framework=framework,
                detected_via="port" if endpoint else "cmdline",
                endpoint=endpoint, transport="http", runtime=framework,
            )
    return None
