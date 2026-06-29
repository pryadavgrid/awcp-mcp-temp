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
import re
import threading
import time
from collections import deque
from datetime import timedelta

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.sandboxes import Host, Volume

from awcp.runtime import sandbox_db

logger = logging.getLogger(__name__)

# Recent sandbox lifecycle + tool-call events, newest first — the UI's "Sandbox"
# page renders this as a timeline. The in-memory ring keeps the live view fast;
# every event is ALSO written through to Postgres (ops.sandbox_events) so the
# timeline survives an MCP server restart — without the DB the ring alone resets
# when the process exits and the Sandbox page goes blank after Ctrl+C. Fail-open:
# when no DB is configured/reachable this degrades to the old in-memory behaviour.
_EVENTS: deque = deque(maxlen=int(os.getenv("AWCP_SANDBOX_EVENTS_MAX", "200")))

# Lazy one-shot DB init guarded by a cheap flag so the hot record_event path
# doesn't take sandbox_db's lock on every call. sandbox_db.init() is itself
# idempotent; this just avoids re-entering it. The MCP server also calls
# sandbox_db.init() at startup, so this is belt-and-suspenders for other entry
# points (stdio mode, tests).
_db_inited = False


def _ensure_db() -> None:
    global _db_inited
    if not _db_inited:
        _db_inited = True
        try:
            sandbox_db.init()
        except Exception:  # noqa: BLE001 — persistence is best-effort, never fatal
            logger.debug("sandbox_db.init() failed — events stay in-memory only", exc_info=True)


def record_event(kind: str, detail: str = "", **extra) -> None:
    _ensure_db()
    ts = time.time()
    _EVENTS.appendleft({"ts": ts, "kind": kind, "detail": detail, **extra})
    # Write-through to Postgres so the timeline outlives the process. No-op when
    # the DB is unavailable (the in-memory ring above is the fallback).
    sandbox_db.record(kind, detail=detail, event_ts=ts, payload=extra or None)


def sandbox_events(limit: int = 50) -> list[dict]:
    """Newest-first timeline. Durable Postgres history is the source of truth when
    the DB is enabled; the in-memory ring is merged in (and is the sole fallback
    when the DB is unavailable) so an event whose write-through hiccupped is never
    dropped from the live view."""
    _ensure_db()
    limit = max(1, min(limit, _EVENTS.maxlen or 200))
    mem = list(_EVENTS)
    db_rows = sandbox_db.recent(limit) if sandbox_db.enabled() else None
    if not db_rows:
        return mem[:limit]
    # Merge durable history + live ring, dedup on the (near-unique) event ts plus
    # kind/detail, newest first. Both stores share the same event shape.
    seen: set = set()
    merged: list[dict] = []
    for e in sorted([*db_rows, *mem], key=lambda x: x.get("ts") or 0.0, reverse=True):
        key = (e.get("ts"), e.get("kind"), e.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(e)
    return merged[:limit]

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

# A dedicated event loop in a daemon thread that owns ALL sandbox I/O. The
# OpenSandbox SDK is async and its objects (the asyncio.Lock above, the Sandbox's
# httpx clients) bind to whichever loop first drives them. The MCP governance
# path (execute_tool -> run_tool -> handler) is SYNC and runs *on* the server's
# main event loop, so a handler can neither `await` the sandbox nor
# run_coroutine_threadsafe against its own loop without deadlocking. Pinning every
# sandbox coroutine to this one separate loop keeps that state on a single loop
# and lets sync callers bridge in via run_sandbox_sync().
_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_lock = threading.Lock()


def _sandbox_loop() -> asyncio.AbstractEventLoop:
    global _bg_loop
    with _bg_lock:
        if _bg_loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="awcp-sandbox-loop", daemon=True
            ).start()
            _bg_loop = loop
        return _bg_loop


def run_sandbox_sync(coro):
    """Run a sandbox coroutine on the dedicated sandbox loop and block for its
    result. Safe from the main event-loop thread (the work runs on a different
    loop/thread, so there's no self-deadlock) or from any worker thread."""
    return asyncio.run_coroutine_threadsafe(coro, _sandbox_loop()).result()


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


async def _close_async() -> None:
    global _sandbox
    if _sandbox is not None:
        logger.info("sandbox.close")
        record_event("sandbox_close", detail=f"id={_sandbox.id}")
        await _sandbox.kill()
        await _sandbox.close()
        _sandbox = None


def close_sandbox() -> None:
    """Tear down the shared sandbox on the sandbox loop. Call this on server
    shutdown (bridge it off the main loop, e.g. via asyncio.to_thread)."""
    if _sandbox is not None:
        run_sandbox_sync(_close_async())


# ── Sync wrappers — the single home for the read/write/run logic ──────────────
# Both the static @mcp.tool functions (for direct MCP clients) and the runtime
# @tool functions in awcp.tools.sandbox_tools (for agents, via execute_tool) call
# these. Each bridges its async sandbox op onto the dedicated loop and records a
# UI timeline event for the outcome — so the Sandbox page lights up no matter
# which path invoked it.

async def _read_file_async(path: str) -> str:
    sb = await get_sandbox()
    return await sb.files.read_file(f"{SANDBOX_MOUNT_PATH}/{path}")


async def _write_file_async(path: str, content: str) -> None:
    sb = await get_sandbox()
    await sb.files.write_file(f"{SANDBOX_MOUNT_PATH}/{path}", content)


async def _run_command_async(command: str) -> str:
    sb = await get_sandbox()
    execution = await sb.commands.run(
        command,
        opts=RunCommandOpts(
            working_directory=SANDBOX_MOUNT_PATH,
            timeout=timedelta(seconds=30),
        ),
    )
    stdout = "".join(m.text or "" for m in execution.logs.stdout)
    stderr = "".join(m.text or "" for m in execution.logs.stderr)
    return f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"


# Shown in place of a raw "file not found" / "no such file or directory" failure.
# Inside the sandbox that error also covers the "path is outside the workspace"
# case (the container can only see /workspace). Phrased in the first person and
# LEADING with the access fact so the agent's LLM relays it in its final reply
# (it was paraphrasing the old wording down to just "the file does not exist").
_NOT_FOUND_MSG = (
    "I do not have access to this file or folder (it is outside the sandbox "
    "workspace, which is the only area I can access), or no such file or "
    "directory exists."
)


def _is_not_found(text: str) -> bool:
    low = text.lower()
    return ("no such file or directory" in low
            or "file_not_found" in low
            or "file not found" in low
            or "permission denied" in low)


def _rewrite_not_found(text: str) -> str:
    """Rewrite a not-found phrase in place, keeping any command/context prefix
    (e.g. 'rm: /path: …'). Case-insensitive, single pass (re.sub does NOT re-scan
    the replacement, so the phrase inside _NOT_FOUND_MSG can't re-trigger it).
    Non-matching output is returned unchanged."""
    return re.sub(
        r"no such file or directory",
        lambda _m: _NOT_FOUND_MSG,
        text,
        flags=re.IGNORECASE,
    )


def read_file_sync(path: str) -> str:
    if ".." in path or path.startswith("/"):
        record_event("read_file_blocked", detail=path)
        return f"Error: {_NOT_FOUND_MSG}"
    try:
        content = run_sandbox_sync(_read_file_async(path))
        record_event("read_file", detail=path)
        return content
    except Exception as e:  # noqa: BLE001
        record_event("read_file_error", detail=f"{path}: {e}"[:200])
        if _is_not_found(str(e)):
            return f"Error reading file: {_NOT_FOUND_MSG}"
        return f"Error reading file: {str(e)}"


def write_file_sync(path: str, content: str) -> str:
    if ".." in path or path.startswith("/"):
        record_event("write_file_blocked", detail=path)
        return f"Error: {_NOT_FOUND_MSG}"
    try:
        run_sandbox_sync(_write_file_async(path, content))
        record_event("write_file", detail=f"{path} ({len(content)} bytes)")
        return f"Successfully wrote to {path}"
    except Exception as e:  # noqa: BLE001
        record_event("write_file_error", detail=f"{path}: {e}"[:200])
        if _is_not_found(str(e)):
            return f"Error writing file: {_NOT_FOUND_MSG}"
        return f"Error writing file: {str(e)}"


def run_command_sync(command: str) -> str:
    try:
        out = run_sandbox_sync(_run_command_async(command))
        record_event("run_command", detail=command[:200])
        return _rewrite_not_found(out)
    except Exception as e:  # noqa: BLE001
        record_event("run_command_error", detail=f"{command}: {e}"[:200])
        return _rewrite_not_found(f"Error executing command: {str(e)}")


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
