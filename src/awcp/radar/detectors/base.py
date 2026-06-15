"""Generic agent detection — module-name based, not a fixed framework list.

We do NOT enumerate a closed set of frameworks. An agent is recognised when the
module it imports / loads is either:
  (a) a known agent / LLM SDK (KNOWN_AGENT_MODULES — a seed list of *labels*,
      easy to extend but not the limit), OR
  (b) anything whose name looks agentic/LLM-ish (_AI_RE) — e.g. a custom or
      home-grown framework like `myco_agents`, `fast_llm`, `acme_copilot`.
So any agent built on any mainstream SDK, or any package whose name signals an
agent/LLM, is detected — plus the self-register webhook covers the rest.
"""

from __future__ import annotations

import os
import re
import time

import psutil

from awcp.radar.models import AgentEntry

# Seed list of agent / LLM ecosystem module names. This is a starting set of
# friendly *labels*, NOT a whitelist — the generic pattern below catches the
# long tail. Extend freely.
KNOWN_AGENT_MODULES: set[str] = {
    # agent frameworks
    "langgraph", "langchain", "crewai", "autogen", "pyautogen", "autogen_agentchat",
    "autogenstudio", "llama_index", "llamaindex", "pydantic_ai", "smolagents",
    "agno", "phidata", "phi", "semantic_kernel", "haystack", "dspy", "guidance",
    "instructor", "letta", "controlflow", "marvin", "griptape", "swarm", "langflow",
    "atomic_agents", "agency_swarm", "metagpt", "camel", "autogpt", "babyagi",
    # llm SDKs / gateways (a process using these is an agentic runtime)
    "openai", "anthropic", "litellm", "groq", "cohere", "mistralai", "ollama",
    "google_genai", "generativeai", "genai", "vertexai", "together", "replicate",
    "fireworks", "anthropic_bedrock",
}
# Seed labels are a STARTING set, not a whitelist. Extend at deploy time via env
# (comma-separated) so the include side is dynamic, never a fixed list.
KNOWN_AGENT_MODULES |= {
    m.strip().lower()
    for m in os.getenv("AGENT_RADAR_KNOWN_MODULES", "").split(",")
    if m.strip()
}

# Generic "this looks like an agent / LLM thing" signal — open-ended, and the
# keyword set is env-extensible so it is not a fixed list either.
_AI_KEYWORDS = [
    "agent", "llm", "genai", "chatbot", "chatgpt", "autogpt", "copilot", "assistant",
] + [k.strip() for k in os.getenv("AGENT_RADAR_AGENT_KEYWORDS", "").split(",") if k.strip()]
_AI_RE = re.compile("(" + "|".join(re.escape(k) for k in _AI_KEYWORDS) + ")", re.I)

_SELF_PID = os.getpid()

# ----------------------------------------------------------------------
# Behavioral, FRAMEWORK-AGNOSTIC detection: a process that holds an established
# connection to a local LLM endpoint is using an LLM — so it is an agentic
# runtime regardless of what library or name it uses. This is what makes
# detection universal (it catches custom/unknown agents that import nothing
# recognizable). LLM ports are env-tunable; default covers Ollama / LM Studio.
# ----------------------------------------------------------------------
LLM_CLIENT_PORTS: set[int] = {
    int(p) for p in os.getenv("AGENT_RADAR_LLM_PORTS", "11434,11435,1234").split(",")
    if p.strip().isdigit()
}
_LLM_CONN_TTL = float(os.getenv("AGENT_RADAR_LLM_CONN_TTL", "4"))
_llm_conn_cache: dict = {"ts": 0.0, "pids": None}


def _llm_client_pids() -> set[int] | None:
    """PIDs with an ESTABLISHED TCP connection to an LLM port, via one cheap lsof
    call (cached). Returns None if lsof is unavailable so callers can fall back."""
    import subprocess
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"],
            capture_output=True, text=True, timeout=4,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "->" not in line:
            continue
        parts = line.split()
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        addr = next((p for p in parts if "->" in p), "")
        rport = addr.split("->")[-1].rsplit(":", 1)[-1]
        if rport.isdigit() and int(rport) in LLM_CLIENT_PORTS:
            pids.add(int(parts[1]))
    return pids


def talks_to_llm(proc) -> bool:
    """True if the process is an LLM client (established connection to an LLM
    port). Best-effort + cached; falls back to per-process psutil if lsof is
    missing, and returns False on any access error."""
    now = time.time()
    if now - _llm_conn_cache["ts"] > _LLM_CONN_TTL:
        _llm_conn_cache["pids"] = _llm_client_pids()
        _llm_conn_cache["ts"] = now
    cached = _llm_conn_cache["pids"]
    if cached is not None:
        return proc.pid in cached
    try:                                            # per-process fallback
        getter = getattr(proc, "net_connections", None) or proc.connections
        for c in getter(kind="tcp"):
            if c.status == psutil.CONN_ESTABLISHED and c.raddr and c.raddr.port in LLM_CLIENT_PORTS:
                return True
    except Exception:
        return False
    return False


def match_agent_module(modname: str, allow_generic: bool = True) -> str | None:
    """Return a framework label if `modname` is an agent/LLM module, else None.

    Not a closed list: a known module returns its friendly label; any other
    module whose name looks agentic/LLM-ish (when allow_generic) is matched too.
    """
    if not modname:
        return None
    full = modname.lower().strip().strip(",")
    for part in full.split("."):
        if part in KNOWN_AGENT_MODULES:
            return part
    if full in KNOWN_AGENT_MODULES:
        return full.split(".")[0]
    if allow_generic and _AI_RE.search(full):
        return full.split(".")[0]
    return None


# Back-compat alias used by older callers.
def match_framework(text: str) -> str | None:
    return match_agent_module(text)


def is_self(info: dict) -> bool:
    """Skip the radar's own process(es) so it never registers itself."""
    if info.get("pid") == _SELF_PID:
        return True
    cmd = " ".join(info.get("cmdline") or []).lower()
    return "awcp.radar" in cmd or "agent_radar" in cmd


# Desktop apps / IDE helpers that merely *bundle* an LLM library but are not
# governable agents. Generic detection would otherwise flag them. Matched only
# against the process name + its executable (not script arguments), so a real
# python agent launched from such a folder is unaffected. Extend with the
# AGENT_RADAR_EXCLUDE env var (comma-separated, case-insensitive).
EXCLUDE_HINTS: tuple[str, ...] = tuple(
    h for h in (
        "claude.app", "claude helper", "cursor.app", "cursor helper",
        "visual studio code", "code helper", "code.app", "vscode", "electron",
        "shipit", "slack", "discord", "notion", "obsidian", "spotify",
        "whatsapp", "telegram", "chatgpt.app", "ollama.app",
        # developer CLIs that embed an LLM SDK but are not governable agents
        "cloudcode_cli", "claude-code", "claude code",
    ) + tuple(
        x.strip().lower()
        for x in os.getenv("AGENT_RADAR_EXCLUDE", "").split(",")
        if x.strip()
    )
)


def is_excluded(info: dict) -> bool:
    """True for known desktop apps / IDE helpers we don't want in the registry."""
    cmdline = info.get("cmdline") or []
    exe = cmdline[0] if cmdline else ""               # the app binary, not its args
    hay = f"{info.get('name', '')} {exe}".lower()
    return any(h in hay for h in EXCLUDE_HINTS)


def label_for(info: dict) -> str:
    for tok in info.get("cmdline") or []:
        if tok.endswith(".py"):
            return os.path.basename(tok)
    return info.get("name") or f"pid-{info.get('pid')}"


def make_entry(
    info: dict,
    proc: psutil.Process,
    *,
    kind: str,
    framework: str | None,
    detected_via: str,
    endpoint: str | None = None,
    transport: str | None = None,
    runtime: str | None = None,
) -> AgentEntry:
    try:
        cwd = proc.cwd()
    except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
        cwd = None
    ct = info.get("create_time") or time.time()
    return AgentEntry(
        id=f"proc-{info['pid']}-{int(ct)}",
        name=label_for(info),
        kind=kind,
        framework=framework,
        source="scan",
        status="quarantined",
        runtime=runtime,
        endpoint=endpoint,
        transport=transport,
        pid=info["pid"],
        user=info.get("username"),
        cwd=cwd,
        cmdline=" ".join(info.get("cmdline") or [])[:1000],
        detected_via=detected_via,
        first_seen=ct,
        last_seen=time.time(),
        alive=True,
    )
