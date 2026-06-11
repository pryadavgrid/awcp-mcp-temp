"""Detect running agent processes — generically, by the agent/LLM module they use.

Three signals, strongest first. The module match is open-ended (see
base.match_agent_module), so this is not limited to a fixed framework list — any
mainstream SDK or any agentic-looking package is recognised, and the matched
module name becomes the entry's `framework` label.
"""

from __future__ import annotations

import os

import psutil

from awcp.radar.models import AgentEntry, KIND_AGENT_FRAMEWORK
from awcp.radar.detectors.base import match_agent_module, make_entry, talks_to_llm

_MAX_SCRIPT_BYTES = 256 * 1024
_SHELL_NAMES = {"zsh", "bash", "sh", "fish", "dash", "tcsh", "csh", "ksh"}


def _module_from_import_line(line: str) -> str | None:
    """'import openai as x' -> 'openai'; 'from langchain.x import y' -> 'langchain.x'."""
    s = line.strip()
    if s.startswith("import "):
        return s[7:].split()[0].split(",")[0].strip()
    if s.startswith("from "):
        return s[5:].split()[0].strip()
    return None


def _match_cmdline(cmdline_list: list[str]) -> tuple[str | None, str | None]:
    """Strict: `-m <module>` (generic ok) or a console-script named after a known
    module. We do NOT run the generic name pattern on arbitrary script filenames,
    so a script called `agent_runtime.py` is labelled by its imports, not its name."""
    toks = [t.lower() for t in cmdline_list]
    for i, tok in enumerate(toks):
        base = os.path.basename(tok)
        prev = toks[i - 1] if i > 0 else ""
        if prev == "-m":
            fw = match_agent_module(tok, allow_generic=True)   # python -m crewai / -m my_agents
            if fw:
                return fw, "cmdline"
        fw = match_agent_module(base, allow_generic=False)     # console script == known module
        if fw:
            return fw, "cmdline"
    return None, None


def _match_open_files(proc: psutil.Process) -> tuple[str | None, str | None]:
    """A loaded package file points at the module (e.g. .../site-packages/openai/...)."""
    try:
        files = proc.open_files()
    except (psutil.AccessDenied, psutil.NoSuchProcess, NotImplementedError, Exception):
        return None, None
    for of in files:
        p = of.path.lower()
        if "site-packages/" in p:
            pkg = p.split("site-packages/", 1)[1].split("/", 1)[0]
            fw = match_agent_module(pkg, allow_generic=True)
            if fw:
                return fw, "open_files"
        for seg in p.split("/"):                                # editable / local installs
            fw = match_agent_module(seg, allow_generic=False)
            if fw:
                return fw, "open_files"
    return None, None


def _scan_script_for_import(path: str) -> str | None:
    try:
        if not path.endswith(".py") or not os.path.isfile(path):
            return None
        if os.path.getsize(path) > _MAX_SCRIPT_BYTES:
            return None
        with open(path, "r", errors="ignore") as f:
            text = f.read()
    except Exception:
        return None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            fw = match_agent_module(_module_from_import_line(s), allow_generic=True)
            if fw:
                return fw
    return None


def classify(proc: psutil.Process, info: dict) -> AgentEntry | None:
    cmdline_list = info.get("cmdline") or []
    name = (info.get("name") or "").lower()

    framework, detected_via = _match_cmdline(cmdline_list)
    if not framework:
        framework, detected_via = _match_open_files(proc)
    if not framework:
        for token in cmdline_list:
            if token.endswith(".py"):
                fw = _scan_script_for_import(token)
                if fw:
                    framework, detected_via = fw, "script_import"
                    break
    # Universal fallback: nothing in the name/imports gave it away, but it is
    # actively talking to a local LLM — so it IS an agentic runtime, whatever it
    # is built on. This is the framework-agnostic catch-all.
    if not framework and talks_to_llm(proc):
        framework, detected_via = "llm-client", "llm_connection"
    if not framework:
        return None
    if name in _SHELL_NAMES and detected_via == "cmdline":
        return None

    runtime = "python" if detected_via != "llm_connection" else None
    return make_entry(
        info, proc, kind=KIND_AGENT_FRAMEWORK,
        framework=framework, detected_via=detected_via, runtime=runtime,
    )
