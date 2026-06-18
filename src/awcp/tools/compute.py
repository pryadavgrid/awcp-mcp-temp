"""Generic compute / utility tools, combined into the MCP server so agents call
them over MCP instead of implementing them locally. All are low-risk reads
(no state change), so the write-action gate lets them through and simply traces
them. Registered dynamically via @tool — nothing here is agent-specific.
"""

import datetime

from awcp.runtime.tool_runtime import tool


@tool("multiply")
def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return float(a) * float(b)


@tool("add")
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return float(a) + float(b)


@tool("power")
def power(base: float, exponent: float) -> float:
    """Raise base to the power of exponent."""
    return float(base) ** float(exponent)


@tool("word_count")
def word_count(text: str) -> int:
    """Count the words in a piece of text."""
    return len(str(text).split())


@tool("current_time")
def current_time() -> str:
    """Return the current local date/time (ISO-8601, seconds precision)."""
    return datetime.datetime.now().isoformat(timespec="seconds")
