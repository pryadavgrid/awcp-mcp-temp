"""Small helpers for resolving a process's network endpoint."""

from __future__ import annotations

import re
import socket

import psutil


def proc_listen_ports(proc: psutil.Process) -> set[int]:
    ports: set[int] = set()
    try:
        for c in proc.connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                ports.add(c.laddr.port)
    except (psutil.AccessDenied, psutil.NoSuchProcess, NotImplementedError, Exception):
        pass
    return ports


def port_from_cmdline(cmdline_list: list[str]) -> int | None:
    toks = cmdline_list or []
    for i, t in enumerate(toks):
        if t in ("--port", "-p") and i + 1 < len(toks) and toks[i + 1].isdigit():
            return int(toks[i + 1])
        m = re.match(r"--port=(\d+)$", t)
        if m:
            return int(m.group(1))
    for t in toks:
        m = re.search(r":(\d{2,5})(?:\b|/)", t)
        if m:
            return int(m.group(1))
    return None


def tcp_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False
