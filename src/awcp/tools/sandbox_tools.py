"""Workspace sandbox tools exposed to AGENTS through the runtime registry.

The MCP server also defines read_file / write_file / run_command as static
@mcp.tool functions for direct MCP clients (the Inspector, a raw SSE script).
These @tool versions put the SAME sandbox operations on the governed
execute_tool path, so an agent's LLM discovers them via list_runtime_tools and
calls them under the radar write-action gate — exactly the dual-registration
pattern search_arxiv already uses (a static @mcp.tool plus a runtime @tool).

All three delegate to the sync wrappers in awcp.runtime.sandbox, which own the
path guard, the UI timeline events, and the bridge onto the dedicated sandbox
loop. Risk tiers are env-overridable; writes are gated, reads run ungated.

Default risks are 'medium' so the platform's default (permissive) gate admits
them like save_artifact — set AWCP_SANDBOX_RUN_RISK=high for stricter setups
where executing arbitrary shell should require operator approval.
"""
import os

from awcp.runtime.tool_runtime import tool
from awcp.runtime.sandbox import read_file_sync, run_command_sync, write_file_sync


@tool("read_file", risk=os.getenv("AWCP_SANDBOX_READ_RISK", "low"))
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the isolated workspace sandbox.

    Args:
        path: Path relative to the workspace root. '..' and absolute paths are
            rejected.

    Returns:
        The file contents, or an error string.
    """
    return read_file_sync(path)


@tool("write_file", risk=os.getenv("AWCP_SANDBOX_WRITE_RISK", "medium"))
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file in the isolated workspace sandbox.

    GOVERNED write (gated by the radar before execution).

    Args:
        path: Path relative to the workspace root. '..' and absolute paths are
            rejected.
        content: The file body to write.

    Returns:
        A confirmation string, or an error string.
    """
    return write_file_sync(path, content)


@tool("run_command", risk=os.getenv("AWCP_SANDBOX_RUN_RISK", "medium"))
def run_command(command: str) -> str:
    """Execute a shell command inside the isolated workspace sandbox container
    (never the host), with the workspace as the working directory.

    GOVERNED write (gated by the radar before execution).

    Args:
        command: The shell command to run.

    Returns:
        The combined STDOUT/STDERR, or an error string.
    """
    return run_command_sync(command)
