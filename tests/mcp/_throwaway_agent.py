"""Throwaway agent-side MCP SSE server used by test_wire_path (NOT a test module).

Stands in for the SDK's embedded FastMCP server: one well-behaved tool and one
that always raises, so the governed executor's remote dispatch can be exercised
over a real SSE connection. Launched as a subprocess by the module fixture.
"""
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("throwaway-agent",
              port=int(os.getenv("AWCP_TEST_REMOTE_PORT", "9411")))


@mcp.tool(description="Uppercase the given text (remote tool wire-path test).")
def echo_upper(text: str) -> str:
    return f"REMOTE[{text.upper()}]"


@mcp.tool(description="Always fails, to exercise the isError path.")
def always_fails(text: str = "") -> str:
    raise RuntimeError("intentional failure from remote agent")


if __name__ == "__main__":
    mcp.run(transport="sse")
