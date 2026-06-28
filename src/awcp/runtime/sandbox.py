"""Isolation boundary for the workspace tools (read_file/write_file/run_command).

These tools used to touch the host filesystem/shell directly. They now run
inside a single long-lived OpenSandbox container with only a dedicated host
directory (`workspace/`) bind-mounted in — agents can read/write/execute
freely inside that mount without any access to the rest of the host.

Requires a local OpenSandbox runtime (the control-plane the SDK talks to,
default localhost:8080) to already be running, e.g.:
    uvx opensandbox-server init-config ~/.sandbox.toml --example docker
    uvx opensandbox-server
"""
import asyncio
import logging
import os
import time
from collections import deque

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.models.sandboxes import Host, Volume

logger = logging.getLogger(__name__)

# Recent sandbox lifecycle + tool-call events, newest first — the UI's "Sandbox"
# page renders this as a timeline. In-memory only (mirrors the radar's _EVENTS
# ring buffer pattern); resets when the MCP server process restarts.
_EVENTS: deque = deque(maxlen=int(os.getenv("AWCP_SANDBOX_EVENTS_MAX", "200")))


def record_event(kind: str, detail: str = "", **extra) -> None:
    _EVENTS.appendleft({"ts": time.time(), "kind": kind, "detail": detail, **extra})


def sandbox_events(limit: int = 50) -> list[dict]:
    return list(_EVENTS)[: max(1, min(limit, _EVENTS.maxlen or 200))]

WORKSPACE_HOST_DIR = os.getenv(
    "AWCP_WORKSPACE_DIR",
    os.path.join(os.getcwd(), "workspace"),
)
SANDBOX_IMAGE = os.getenv("AWCP_SANDBOX_IMAGE", "python:3.11")
SANDBOX_MOUNT_PATH = "/workspace"
# Pin to 127.0.0.1 explicitly rather than "localhost": on hosts where something
# else (e.g. Docker Desktop's own dashboard) is also bound to the same port,
# "localhost" can resolve to ::1 and silently hit that other service instead
# of the local OpenSandbox runtime.
SANDBOX_DOMAIN = os.getenv("OPEN_SANDBOX_DOMAIN", "127.0.0.1:8080")

_sandbox: Sandbox | None = None
_lock = asyncio.Lock()


async def get_sandbox() -> Sandbox:
    """Return the shared sandbox, creating it on first use."""
    global _sandbox
    async with _lock:
        if _sandbox is None:
            os.makedirs(WORKSPACE_HOST_DIR, exist_ok=True)
            logger.info("sandbox.create image=%s host_dir=%s", SANDBOX_IMAGE, WORKSPACE_HOST_DIR)
            record_event("sandbox_create", detail=f"image={SANDBOX_IMAGE}")
            try:
                _sandbox = await Sandbox.create(
                    SANDBOX_IMAGE,
                    timeout=None,
                    volumes=[
                        Volume(
                            name="workspace",
                            host=Host(path=WORKSPACE_HOST_DIR),
                            mount_path=SANDBOX_MOUNT_PATH,
                        )
                    ],
                    connection_config=ConnectionConfig(domain=SANDBOX_DOMAIN),
                )
            except Exception as exc:  # noqa: BLE001
                record_event("sandbox_create_failed", detail=f"{type(exc).__name__}: {exc}"[:200])
                raise
            record_event("sandbox_ready", detail=f"id={_sandbox.id}")
        return _sandbox


async def close_sandbox() -> None:
    """Tear down the shared sandbox. Call this on server shutdown."""
    global _sandbox
    if _sandbox is not None:
        logger.info("sandbox.close")
        record_event("sandbox_close", detail=f"id={_sandbox.id}")
        await _sandbox.kill()
        await _sandbox.close()
        _sandbox = None


def sandbox_status() -> dict:
    """Report the sandbox's current state without creating one. Lazy init
    means "not_started" is a normal, healthy state — not an error."""
    return {
        "status": "running" if _sandbox is not None else "not_started",
        "sandbox_id": _sandbox.id if _sandbox is not None else None,
        "image": SANDBOX_IMAGE,
        "workspace_dir": WORKSPACE_HOST_DIR,
        "mount_path": SANDBOX_MOUNT_PATH,
    }
