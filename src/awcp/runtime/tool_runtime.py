import importlib
import os
import pkgutil

from typing import Any, Callable

from awcp.runtime.event_runtime import emit_execution_event, CURRENT_PROFILE


TOOL_REGISTRY: dict[str, Callable[..., Any]] = {}

# Per-tool governance metadata, declared at registration time. Nothing is keyed
# on a specific tool name here: a tool simply declares its own risk/scope via the
# @tool decorator, and the MCP governance plane reads it back through
# get_tool_risk()/get_tool_scope() when it gates a call. Reads default to "low".
TOOL_META: dict[str, dict[str, Any]] = {}

# Default risk tiers that the write-action gate treats as WRITES (everything else
# is a read and is never gated). Env-overridable so the taxonomy is not hardcoded.
_WRITE_RISK_TIERS = frozenset(
    t.strip().lower()
    for t in os.getenv("AWCP_WRITE_RISK_TIERS", "medium,high,critical").split(",")
    if t.strip()
)


def _parse_risk_overrides() -> dict[str, str]:
    """Operator override map from AWCP_TOOL_RISK, e.g.
    "external_post:high,save_artifact:medium". Lets ops retune a tool's risk at
    deploy time without touching code. Empty by default."""
    out: dict[str, str] = {}
    for pair in os.getenv("AWCP_TOOL_RISK", "").split(","):
        if ":" in pair:
            name, _, val = pair.partition(":")
            if name.strip() and val.strip():
                out[name.strip()] = val.strip().lower()
    return out


_RISK_OVERRIDES = _parse_risk_overrides()

# Default risk for a tool that declares none (reads are the common case).
_DEFAULT_RISK = os.getenv("AWCP_DEFAULT_TOOL_RISK", "low").lower()


def register_tool(
    name: str,
    handler: Callable[..., Any],
    *,
    risk: str | None = None,
    scope: str | None = None,
) -> None:

    TOOL_REGISTRY[name] = handler
    TOOL_META[name] = {
        # An explicit per-tool risk; otherwise resolved later against the env
        # override map / default. Storing None keeps "undeclared" distinct from
        # an explicit "low" so get_tool_risk() can apply overrides correctly.
        "risk": (risk.lower() if isinstance(risk, str) and risk.strip() else None),
        # The action's write scope (matched against an agent's declared
        # write_scopes by the radar gate). Defaults to the tool name.
        "scope": (scope if scope else name),
    }


def tool(name: str, *, risk: str | None = None, scope: str | None = None):

    def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:

        register_tool(name, handler, risk=risk, scope=scope)

        return handler

    return decorator


def get_tool_risk(name: str) -> str:
    """Resolve a tool's effective risk tier. Precedence (nothing hardcoded):
    1. the AWCP_TOOL_RISK env override map,
    2. the tool's own declared risk,
    3. the system default (AWCP_DEFAULT_TOOL_RISK, normally "low")."""
    if name in _RISK_OVERRIDES:
        return _RISK_OVERRIDES[name]
    declared = (TOOL_META.get(name) or {}).get("risk")
    return declared or _DEFAULT_RISK


def get_tool_scope(name: str) -> str:
    """The write scope the gate should check for this tool (defaults to its name)."""
    return (TOOL_META.get(name) or {}).get("scope") or name


def is_write_risk(risk: str) -> bool:
    """True when a risk tier denotes a state-changing (gated) action."""
    return (risk or "").lower() in _WRITE_RISK_TIERS


def summarize_tool_output(output: Any) -> dict[str, Any]:

    text = str(output)

    return {
        "type": type(output).__name__,
        "preview": text[:500]
    }


def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any]
) -> Any:

    if CURRENT_PROFILE.get() == "recommendation_only":
        block_msg = (
            f"SYSTEM CONTROL BLOCK: Write permissions are REVOKED. "
            f"Tool '{tool_name}' execution denied. "
            f"You must fall back to recommendation mode based on your existing knowledge."
        )
        emit_execution_event({
            "event_type": "tool_call",
            "tool_name": tool_name,
            "status": "blocked",
            "input": tool_input,
            "error": block_msg,
        })
        return block_msg

    handler = TOOL_REGISTRY.get(tool_name)

    if not handler:
        raise ValueError(f"Unknown tool: {tool_name}")

    event = {
        "event_type": "tool_call",
        "tool_name": tool_name,
        "status": "started",
        "input": tool_input
    }

    emit_execution_event(event.copy())

    try:
        output = handler(**tool_input)

        event["status"] = "succeeded"
        event["output"] = summarize_tool_output(output)
        emit_execution_event(event.copy())

        return output

    except Exception as e:
        event["status"] = "failed"
        event["error"] = str(e)
        emit_execution_event(event.copy())

        raise


def discover_tools(package_name: str = "awcp.tools") -> None:

    package = importlib.import_module(package_name)

    for module in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        importlib.import_module(module.name)
