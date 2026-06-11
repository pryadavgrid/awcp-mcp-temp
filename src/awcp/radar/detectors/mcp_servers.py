"""Detect running MCP servers (FastMCP/low-level), stdio or SSE."""

from __future__ import annotations

import psutil

from awcp.radar.models import AgentEntry, KIND_MCP_SERVER
from awcp.radar.detectors.base import make_entry
from awcp.radar.detectors.ports import port_from_cmdline, proc_listen_ports

# strong, low-false-positive tokens for "this is an MCP server"
_MCP_TOKENS = ("mcp.server", "mcp_server", "fastmcp", "modelcontextprotocol")


def _is_mcp_server(cmdline: str) -> bool:
    low = cmdline.lower()
    return any(tok in low for tok in _MCP_TOKENS)


def classify(proc: psutil.Process, info: dict) -> AgentEntry | None:
    cmdline_list = info.get("cmdline") or []
    cmdline = " ".join(cmdline_list)
    if not _is_mcp_server(cmdline):
        return None

    toks = [t.lower() for t in cmdline_list]
    transport: str | None = None
    endpoint: str | None = None

    if "stdio" in toks:
        transport = "stdio"
    else:
        port = port_from_cmdline(cmdline_list)
        if port is None:
            listen = proc_listen_ports(proc)
            port = min(listen) if listen else None
        if port:
            transport = "sse"
            endpoint = f"http://127.0.0.1:{port}/sse"

    return make_entry(
        info, proc, kind=KIND_MCP_SERVER,
        framework="mcp", detected_via="cmdline",
        endpoint=endpoint, transport=transport, runtime="python",
    )
