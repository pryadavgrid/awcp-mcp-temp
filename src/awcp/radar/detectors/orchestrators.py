"""Detect agent orchestrators: Temporal servers and Temporal workers."""

from __future__ import annotations

import os

import psutil

from awcp.radar.models import AgentEntry, KIND_ORCHESTRATOR
from awcp.radar.detectors.base import make_entry


def _imports_temporalio(proc: psutil.Process) -> bool:
    try:
        for of in proc.open_files():
            if "site-packages/temporalio" in of.path.lower():
                return True
    except (psutil.AccessDenied, psutil.NoSuchProcess, NotImplementedError, Exception):
        pass
    return False


def classify(proc: psutil.Process, info: dict) -> AgentEntry | None:
    cmdline_list = info.get("cmdline") or []
    cmdline = " ".join(cmdline_list).lower()
    name = (info.get("name") or "").lower()
    base = os.path.basename((cmdline_list[0] if cmdline_list else "")).lower()

    # Temporal dev server / CLI binary
    if name == "temporal" or base == "temporal":
        return make_entry(
            info, proc, kind=KIND_ORCHESTRATOR,
            framework="temporal-server", detected_via="cmdline",
            endpoint="localhost:7233", transport="grpc", runtime="temporal",
        )

    # Temporal worker (python process running a worker / temporalio loaded)
    if "run_worker" in cmdline or "temporalio" in cmdline or _imports_temporalio(proc):
        return make_entry(
            info, proc, kind=KIND_ORCHESTRATOR,
            framework="temporal-worker", detected_via="cmdline",
            transport=None, runtime="python",
        )
    return None
