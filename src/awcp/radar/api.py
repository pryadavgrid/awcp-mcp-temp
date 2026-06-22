"""Agent Radar — REST API + minimal web view.

A background scanner auto-detects running agentic environments (agent frameworks,
MCP servers, LLM runtimes, orchestrators); agents can also self-register. Each
new entry is onboarded via a per-agent Temporal workflow (map -> quarantine-check
-> link-MCP -> admit) when a Temporal server is reachable, else inline. Detected/
uninstrumented agents stay 'quarantined' until they have telemetry + policy hooks.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

import httpx
import psutil

from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from awcp.radar import onboarding, policy, opa, tokens
from awcp.radar import db as _events_db
from awcp.radar.models import AgentEntry, RegisterRequest
from awcp.radar.store import REGISTRY
from awcp.radar.scanner import SCANNER
from awcp.radar.temporal.config import TEMPORAL_SERVER_URL, TASK_QUEUE, TEMPORAL_UI_BASE
from awcp.radar.temporal.workflows.onboarding import AgentOnboardingWorkflow
from awcp.radar.temporal.workflows.execution import AgentExecutionWorkflow
from awcp.radar.temporal.activities.onboarding import (
    map_identity,
    quarantine_check,
    link_mcp,
    admit,
)
from awcp.radar.temporal.activities.execution import (
    execution_setup,
    execution_llm_call,
    execution_web_search,
    execution_tool_call,
    execution_synthesize_answer,
    execution_complete,
)

# --- Telemetry: link the registry into the shared awcp.observability stack ---
# (HTTP-route tracing is applied by the gateway via instrument_fastapi(app); this
# module only exposes an APIRouter, so it does no FastAPI instrumentation itself.
# A standalone `app` is still built at the bottom for radar-only deployments.)
from awcp.observability.setup import setup_otel
from awcp.radar.telemetry import get_radar_metrics, radar_span, log

setup_otel("awcp-radar")
METRICS = get_radar_metrics()
_OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() == "true"

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# runtime state shared with request handlers
STATE: dict = {
    "temporal": False,
    "client": None,
    # task_id → (workflow_id, workflow_handle) for execution workflows
    "exec_workflows": {},
    # task_id → agent_id, so a real-time execution event (which only carries the
    # task_id) can be attributed to an agent for telemetry observation.
    "exec_agents": {},
}

# Env-driven task queue for execution workflows (separate from onboarding)
EXEC_TASK_QUEUE = os.getenv("AGENT_EXEC_TASK_QUEUE", "agent-task-execution")

# ----------------------------------------------------------------------
# Closed-loop telemetry: the quarantine decision reflects telemetry the radar
# has actually OBSERVED in execution, not a flag the agent declared.
#   REQUIRE_OBSERVED_TELEMETRY  true  -> a declared telemetry_enabled is NOT
#                                        trusted; the agent must emit real
#                                        telemetry to earn the hook (fail-closed,
#                                        the magazine's "observed in execution").
#                               false -> legacy trust-on-declare behaviour.
#   TELEMETRY_TTL               secs  -> an agent whose observed telemetry goes
#                                        silent for longer than this is
#                                        re-quarantined. 0 disables demotion.
# ----------------------------------------------------------------------
REQUIRE_OBSERVED_TELEMETRY = os.getenv(
    "AGENT_RADAR_REQUIRE_OBSERVED_TELEMETRY", "true").lower() == "true"
# Same closed-loop rule for the policy-callback hook: a declared callback list is
# not trusted; the agent must actually exercise policy (consult the gate) before
# the hook counts. Off => trust the declared policy_callbacks (legacy).
REQUIRE_OBSERVED_POLICY = os.getenv(
    "AGENT_RADAR_REQUIRE_OBSERVED_POLICY", "true").lower() == "true"
# Same closed-loop rule for the feature-flag (flag-wiring) hook. Unlike telemetry
# and policy — which ride behaviours the agent already exhibits (token events /
# gate calls) — flag observation needs the agent to REPORT flag state in its
# execution events, so this defaults OFF (declared flags are trusted) to avoid
# re-quarantining agents that don't report flags yet. Set true to enforce.
REQUIRE_OBSERVED_FLAGS = os.getenv(
    "AGENT_RADAR_REQUIRE_OBSERVED_FLAGS", "false").lower() == "true"
TELEMETRY_TTL = float(os.getenv("AGENT_RADAR_TELEMETRY_TTL", "300"))
TELEMETRY_RECONCILE_INTERVAL = float(
    os.getenv("AGENT_RADAR_TELEMETRY_RECONCILE_INTERVAL", "15"))
# Coalesce registry writes: refresh last_telemetry_ts at most this often per
# agent while it is already active (avoids a JSON persist on every event).
_TELEMETRY_REFRESH_MIN = 5.0

# Recent-decisions log: a registry-local, in-memory ring buffer of the last N
# governance events (onboarding / gate / degradation / operator actions). This is
# NOT the durable Evidence Ledger (a separate component) — it's a lightweight live
# audit view so operators can see what the registry just did.
_EVENTS: deque = deque(maxlen=int(os.getenv("AGENT_RADAR_EVENTS_MAX", "200")))


def _record_event(kind: str, agent_id: str = "", detail: str = "", **extra) -> None:
    _EVENTS.appendleft(
        {"ts": time.time(), "kind": kind, "agent_id": agent_id,
         "detail": detail, **extra}
    )
    # Durable mirror: the audit-worthy subset is also written to the canonical
    # schema (evidence.ledger / governance.policy_decisions / degradation_events,
    # routed in db.record) so approvals/scope-changes/demotions survive a restart.
    # Only DENY gate decisions are durable (recorded as "gate_denied"); allows stay
    # in the live ring only. No-op when the DB is unavailable.
    durable_kind = kind
    if kind == "gate":
        if not detail.startswith("deny"):
            return
        durable_kind = "gate_denied"
    if durable_kind in _events_db.DURABLE_EVENT_TYPES:
        _events_db.record(durable_kind, agent_id,
                          {"detail": detail, **extra})


# ----------------------------------------------------------------------
# Token control — TWO coexisting layers, both owned by the control plane:
#
#   (1) GRACEFUL DEGRADATION  (durable, recoverable) — a token breach steps the
#       agent ONE rung down its OWN autonomy ladder, exactly like a failure-budget
#       breach. This lives in _on_token_breach (laminar's injected callback) and
#       is INTACT from before.
#
#   (2) HARD STOP  (live, self-healing) — WHILE the agent is over its token budget
#       (laminar's sliding window), the radar refuses to let it execute any
#       further: the cooperative gate denies ALL actions, a new governed execution
#       will not start, and an in-flight one is terminated. This does NOT touch the
#       autonomy ladder (layer 1 owns that); it is a separate overlay that lifts
#       automatically once the window clears or an operator resets it.
#
# Both are authoritative and LIVE, keyed only on agent_id with budgets laminar
# resolves positionally — so they cover every agent, existing or registered at
# runtime, with nothing hardcoded.
# ----------------------------------------------------------------------
def _token_blocked(agent_id: str) -> dict | None:
    """Return the live budget evaluation when the agent is over its limit
    (=> hard stop), else None. No-op (None) when laminar is absent/disabled."""
    if not (_LAMINAR and agent_id):
        return None
    try:
        if _laminar.is_exhausted(agent_id):
            return _laminar.budget_state(agent_id)
    except Exception as exc:  # noqa: BLE001 — token control must never break a route
        log.warning("radar.token.check.error agent_id=%s error=%r", agent_id, exc)
    return None


def _note_token_block(agent_id: str, evaluation: dict | None, where: str) -> None:
    """Audit a LIVE hard-stop block — WITHOUT mutating the autonomy ladder, which
    is owned by graceful degradation (_on_token_breach). Records only that the
    control plane refused an over-budget agent at `where`."""
    used = (evaluation or {}).get("used_tokens")
    budget = (evaluation or {}).get("budget_tokens")
    detail = f"blocked at {where} — token budget exhausted"
    if budget:
        detail = f"blocked at {where} — token budget exhausted ({used}/{budget})"
    _record_event("token_hard_stop", agent_id, detail)
    log.warning("radar.token.hardstop agent_id=%s %s", agent_id, detail)


# ----------------------------------------------------------------------
# Layer (3) — PROCESS HARD STOP for AUTONOMOUS agents.
# The gate / governed-execution stops only bite agents that route through the
# control plane. An autonomous agent (a scanned process running its own loop)
# never asks the gate, so the radar must act on the PROCESS it already detected:
# on token exhaustion it SIGSTOPs the process (freezes execution), and SIGCONTs
# it once the budget recovers. Reversible, identity-checked, opt-out via env.
# Nothing hardcoded — purely keyed on the detected pid + agent_id.
# ----------------------------------------------------------------------
TOKEN_PROCESS_STOP = os.getenv("AGENT_RADAR_TOKEN_PROCESS_STOP", "true").lower() == "true"
TOKEN_PROCESS_INTERVAL = float(os.getenv("AGENT_RADAR_TOKEN_PROCESS_INTERVAL", "10"))
TOKEN_CONTROL_TIMEOUT = float(os.getenv("AGENT_RADAR_TOKEN_CONTROL_TIMEOUT", "2"))
_token_suspended: dict[str, int] = {}        # agent_id -> pid WE froze (local SIGSTOP)
_token_remote_stopped: dict[str, str] = {}    # agent_id -> control_endpoint WE suspended (remote)
_token_uncontrolled: set[str] = set()         # over-budget but no pid / endpoint to act on
_token_proc_lock = threading.Lock()

# ── crash-recovery journal (Postgres-exclusive) ───────────────────────────────
# Every stop WE apply is recorded in registry.freeze_journal (Postgres). A radar
# that is SIGKILLed cannot SIGCONT/resume on the way down, so a frozen process
# would stay frozen forever. On the NEXT startup the radar reads the journal from
# Postgres and releases every orphaned stop (see _recover_orphaned_freezes). There
# is NO on-disk JSON journal — freeze state lives only in Postgres. An in-memory
# mirror (+ its own lock) keeps reads cheap and avoids deadlocking _token_proc_lock.
_freeze_journal: dict[str, dict] = {}        # agent_id -> {"kind": "process"|"remote", ...}
_journal_lock = threading.Lock()


def _journal_set(agent_id: str, entry: dict) -> None:
    with _journal_lock:
        _freeze_journal[agent_id] = entry
    # Persist to the canonical registry.freeze_journal (Postgres only).
    _events_db.record_freeze(agent_id, entry.get("kind", "process"),
                             pid=entry.get("pid"), url=entry.get("url"),
                             reason=entry.get("reason"), payload=entry)


def _journal_clear(agent_id: str) -> None:
    with _journal_lock:
        if _freeze_journal.pop(agent_id, None) is None:
            return
    _events_db.clear_freeze(agent_id)


def _recover_orphaned_freezes() -> None:
    """Startup repair: release every stop journaled by a previous radar instance.

    A SIGKILLed radar leaves freeze rows in Postgres; those stops are orphans (the
    process/remote agent will never be resumed by the dead radar). We resume them
    all and clear the journal. This is also CORRECT, not just safe: the in-memory
    token ledger is lost on restart, so budgets start fresh — a previously
    over-budget agent should run again, and the reconciler will re-freeze it only
    if it crosses the limit anew."""
    journaled = _events_db.load_freezes()
    if not journaled:
        return
    recovered = 0
    for agent_id, ent in (journaled or {}).items():
        try:
            if ent.get("kind") == "process":
                pid, ct = ent.get("pid"), ent.get("create_time")
                if not pid:
                    continue
                p = psutil.Process(pid)
                if ct and abs(p.create_time() - ct) > 2:
                    continue                  # pid reused — not the process we froze
                p.resume()
                recovered += 1
                log.warning("radar.token.recover.resume agent_id=%s pid=%s (orphaned freeze)",
                            agent_id, pid)
            elif ent.get("kind") == "remote" and ent.get("url"):
                _post_control(ent["url"], {"action": "resume", "agent_id": agent_id,
                                           "reason": "control plane restarted"})
                recovered += 1
                log.warning("radar.token.recover.remote agent_id=%s url=%s (orphaned stop)",
                            agent_id, ent["url"])
        except Exception:                     # noqa: BLE001 — best-effort per entry
            pass
    with _journal_lock:
        _freeze_journal.clear()
    _events_db.clear_all_freezes()
    if recovered:
        _record_event("token_recover", "", f"resumed {recovered} orphaned stop(s) after restart")
        log.info("radar.token.recover resumed=%d after restart", recovered)


def _proc_for_entry(e) -> "psutil.Process | None":
    """The live psutil.Process for a detected agent, or None if it is not a
    controllable local process. Verifies start-time to defeat PID reuse, and
    never returns the radar's own process."""
    pid = getattr(e, "pid", None)
    # Identity-guard timestamp: an entry's create_time. For a scanned entry that
    # is `first_seen`.
    ts_hint = getattr(e, "first_seen", None)
    # Recover the pid when the `pid` field is empty (e.g. an agent that
    # self-registered with a scan-style id but no pid). The id format is
    # `proc-<pid>-<int(create_time)>`, so the id ALSO carries the identity guard —
    # this is what lets the control plane stop an autonomous local agent that
    # never told us its pid.
    if not pid:
        m = re.match(r"proc-(\d+)-(\d+)$", str(getattr(e, "id", "")))
        if m:
            pid = int(m.group(1))
            ts_hint = float(m.group(2))     # the id's timestamp IS the create_time
    if not pid or pid == os.getpid():
        return None
    try:
        p = psutil.Process(pid)
        # If a different process now owns this pid, the start-times won't match —
        # refuse to signal it.
        if ts_hint and abs(p.create_time() - ts_hint) > 2:
            return None
        return p
    except Exception:
        return None


def _token_suspend_process(agent_id: str, evaluation: dict | None = None) -> None:
    """Freeze (SIGSTOP) an over-budget agent's process — the control plane's hard
    stop for an agent that will not stop itself. No-op if disabled, already
    frozen, or not a controllable local process."""
    if not TOKEN_PROCESS_STOP:
        return
    e = REGISTRY.get(agent_id)
    if not e:
        return
    p = _proc_for_entry(e)
    if p is None:
        return
    pid = p.pid                              # the RESOLVED pid (may come from the id)
    with _token_proc_lock:
        if agent_id in _token_suspended:
            return
        try:
            p.suspend()
            _token_suspended[agent_id] = pid
        except Exception as exc:  # noqa: BLE001
            log.warning("radar.token.suspend.failed agent_id=%s pid=%s error=%r",
                        agent_id, pid, exc)
            return
    # Journal the freeze so a crash can be repaired on the next startup.
    try:
        ct = p.create_time()
    except Exception:                           # noqa: BLE001
        ct = None
    _journal_set(agent_id, {"kind": "process", "pid": pid, "create_time": ct})
    used = (evaluation or {}).get("used_tokens")
    budget = (evaluation or {}).get("budget_tokens")
    detail = "process frozen (SIGSTOP) — token budget exhausted"
    if budget:
        detail = f"process frozen (SIGSTOP) — token budget exhausted ({used}/{budget})"
    _record_event("token_process_stop", agent_id, detail)
    log.warning("radar.token.process_stop agent_id=%s pid=%s %s", agent_id, pid, detail)


def _token_resume_process(agent_id: str) -> None:
    """SIGCONT a process the radar previously froze (budget recovered / reset)."""
    with _token_proc_lock:
        pid = _token_suspended.get(agent_id)
    if pid is None:
        return
    try:
        psutil.Process(pid).resume()
        log.info("radar.token.process_resume agent_id=%s pid=%s", agent_id, pid)
        _record_event("token_process_resume", agent_id, "process resumed (SIGCONT) — budget recovered")
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.token.resume.failed agent_id=%s pid=%s error=%r", agent_id, pid, exc)
    with _token_proc_lock:
        _token_suspended.pop(agent_id, None)
    _journal_clear(agent_id)


# ---- remote agents (no local pid): push a stop directive over a webhook -------

def _post_control(url: str, payload: dict) -> bool:
    """POST a control directive to an agent's control_endpoint. True on 2xx."""
    # SSRF guard: control_endpoint is agent-supplied — refuse private/link-local
    # targets before POSTing to it (same metadata-service risk as link_mcp).
    from awcp.radar.netguard import assert_safe_url, UnsafeURLError
    try:
        assert_safe_url(url)
    except UnsafeURLError as exc:
        log.warning("radar.token.control.refused url=%s reason=%r", url, exc)
        return False
    try:
        r = httpx.post(url, json=payload, timeout=TOKEN_CONTROL_TIMEOUT,
                       headers={"ngrok-skip-browser-warning": "true"})
        return 200 <= r.status_code < 300
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.token.control.post_failed url=%s error=%r", url, exc)
        return False


def _token_stop_remote(agent_id: str, evaluation: dict | None) -> bool:
    """Hard stop for a REMOTE agent: push {"action":"suspend"} to its registered
    control_endpoint (the network analog of SIGSTOP). True if delivered."""
    e = REGISTRY.get(agent_id)
    url = getattr(e, "control_endpoint", None) if e else None
    if not url:
        return False
    with _token_proc_lock:
        if agent_id in _token_remote_stopped:
            return True
    ok = _post_control(url, {"action": "suspend", "agent_id": agent_id,
                             "reason": "token budget exhausted",
                             "budget": evaluation or {}})
    if ok:
        with _token_proc_lock:
            _token_remote_stopped[agent_id] = url
        _journal_set(agent_id, {"kind": "remote", "url": url})
        _record_event("token_remote_stop", agent_id, f"suspend pushed to {url}")
        log.warning("radar.token.remote_stop agent_id=%s url=%s", agent_id, url)
    else:
        _record_event("token_remote_stop_failed", agent_id, f"could not reach {url}")
    return ok


def _token_resume_remote(agent_id: str) -> None:
    """Release a remote agent the control plane suspended (budget recovered)."""
    with _token_proc_lock:
        url = _token_remote_stopped.get(agent_id)
    if not url:
        return
    _post_control(url, {"action": "resume", "agent_id": agent_id,
                        "reason": "token budget recovered"})
    with _token_proc_lock:
        _token_remote_stopped.pop(agent_id, None)
    _journal_clear(agent_id)
    _record_event("token_remote_resume", agent_id, "resume pushed")
    log.info("radar.token.remote_resume agent_id=%s url=%s", agent_id, url)


# ---- dispatcher: pick the right hard-stop mechanism for THIS agent ------------

def _pid_of_entry(e) -> int | None:
    """The OS pid an entry maps to — its `pid` field, or recovered from a
    `proc-<pid>-<ts>` id. Lightweight (no psutil); used to detect SHARED processes."""
    pid = getattr(e, "pid", None)
    if pid:
        return pid
    m = re.match(r"proc-(\d+)-(\d+)$", str(getattr(e, "id", "")))
    return int(m.group(1)) if m else None


def _pid_shared(pid: int | None, agent_id: str) -> bool:
    """True if any OTHER registered agent maps to the same OS process — i.e. a
    SIGSTOP would freeze (and silence the monitoring of) more than just this agent."""
    if not pid:
        return False
    return any(e.id != agent_id and _pid_of_entry(e) == pid for e in REGISTRY.all())


def _token_enforce_stop(agent_id: str, evaluation: dict | None = None) -> None:
    """Hard-stop an over-budget agent with whatever the control plane can reach,
    no agent cooperation in the decision and nothing hardcoded:
      1. a DEDICATED local process -> SIGSTOP (freeze);
      2. else a remote control_endpoint -> push a suspend directive;
      3. else -> flag uncontrollable (operator-visible; gate/deny still applies).

    A process SHARED by several registered agents is never frozen — SIGSTOP is
    process-granular, so freezing it would stop and silence the OTHER agents in
    that process (e.g. a multi-agent runtime). Such an agent falls through to the
    gate/deny path, leaving its co-tenants running and monitored."""
    if not TOKEN_PROCESS_STOP:
        return
    e = REGISTRY.get(agent_id)
    if not e:
        return
    p = _proc_for_entry(e)
    if p is not None and not _pid_shared(p.pid, agent_id):
        _token_uncontrolled.discard(agent_id)   # became controllable (e.g. pid recovered)
        _token_suspend_process(agent_id, evaluation)
        return
    if getattr(e, "control_endpoint", None):
        _token_uncontrolled.discard(agent_id)
        _token_stop_remote(agent_id, evaluation)
        return
    if agent_id not in _token_uncontrolled:
        _token_uncontrolled.add(agent_id)
        why = ("shares its OS process with other agents — not frozen (that would "
               "stop them too)" if p is not None else
               "no local pid or control endpoint")
        _record_event("token_uncontrolled", agent_id,
                      f"over budget but {why} — enforcement limited to the gate/deny path")
        log.warning("radar.token.uncontrolled agent_id=%s shared_process=%s",
                    agent_id, p is not None)


def _token_enforce_resume(agent_id: str) -> None:
    """Release whatever stop the control plane applied (local and/or remote)."""
    _token_resume_process(agent_id)   # no-op unless WE froze it locally
    _token_resume_remote(agent_id)    # no-op unless WE suspended it remotely
    _token_uncontrolled.discard(agent_id)


def _token_reconcile_once() -> None:
    """One sync reconcile pass (run off the event loop): stop the newly
    over-budget, release whatever recovered or vanished."""
    for e in REGISTRY.all():
        if _laminar.is_exhausted(e.id):
            _token_enforce_stop(e.id, _laminar.budget_state(e.id))
    tracked = set(_token_suspended) | set(_token_remote_stopped) | set(_token_uncontrolled)
    for aid in tracked:
        ent = REGISTRY.get(aid)
        if ent is None or not _laminar.is_exhausted(aid):
            _token_enforce_resume(aid)


async def _token_process_reconciler() -> None:
    """Keep hard-stop state in sync with live budgets — freeze/suspend the newly
    over-budget and release the recovered — for BOTH local processes and remote
    (webhook) agents. Disabled via env or when laminar is absent."""
    if not (TOKEN_PROCESS_STOP and _LAMINAR):
        log.info("radar.token.hardstop.reconciler disabled (env or laminar absent)")
        return
    log.info("radar.token.hardstop.reconciler enabled interval=%.0fs", TOKEN_PROCESS_INTERVAL)
    while True:
        try:
            await asyncio.to_thread(_token_reconcile_once)   # HTTP/psutil off the loop
        except Exception as exc:  # noqa: BLE001 — reconciler must never die
            log.warning("radar.token.hardstop.reconciler.error error=%r", exc, exc_info=True)
        await asyncio.sleep(TOKEN_PROCESS_INTERVAL)


def _resume_all_frozen() -> None:
    """Shutdown safety: never leave any agent stopped because the radar stopped."""
    for aid in set(_token_suspended) | set(_token_remote_stopped):
        _token_enforce_resume(aid)


def _observe_telemetry(agent_id: str, detail: str = "telemetry observed in execution") -> None:
    """Close the telemetry loop: real telemetry was OBSERVED for this agent
    (an execution event / signal reached the control plane). Mark the telemetry
    hook present — proven, not declared — and, if the agent was quarantined only
    for missing telemetry, re-run the SAME onboarding gate so it leaves
    quarantine automatically. The magazine's "telemetry ... observed in
    execution", now actually observed.
    """
    if not agent_id:
        return
    e = REGISTRY.get(agent_id)
    if not e:
        return
    now = time.time()

    # Coalesce: if the hook is already proven and the agent is active, only
    # refresh the timestamp occasionally so a busy task doesn't persist on every
    # event. The reconciler's TTL is far larger than this refresh window.
    if (e.telemetry_enabled and e.status != "quarantined"
            and e.last_telemetry_ts is not None
            and now - e.last_telemetry_ts < _TELEMETRY_REFRESH_MIN):
        return

    # An observed telemetry event PROVES the agent is alive right now, so refresh
    # its liveness too — otherwise a self-registered agent (whose process the
    # scanner only sees under a different proc-<pid> id, and which doesn't appear
    # in seen_ids) is pruned by store.reconcile_scan after SELF_PRUNE_AFTER_SEC
    # even while it is actively working, which would 404 its write-action gate.
    fields: dict = {"telemetry_enabled": True, "last_telemetry_ts": now,
                    "last_seen": now, "alive": True}
    if e.status == "quarantined":
        # Re-evaluate admission with the telemetry hook now proven present.
        probe = e.model_copy(update={"telemetry_enabled": True, "last_telemetry_ts": now})
        status, reason = onboarding.decide_status(probe)
        fields["status"] = status
        fields["quarantine_reason"] = reason

    updated = REGISTRY.patch(agent_id, **fields)
    if updated and e.status == "quarantined" and updated.status == "active":
        _record_event("telemetry_observed", agent_id, detail + " -> active")
        log.info("radar.telemetry.observed agent_id=%s -> active", agent_id)


def _observe_policy(agent_id: str, detail: str = "policy hook exercised in execution") -> None:
    """Close the POLICY-CALLBACK loop, mirroring _observe_telemetry: the agent
    actually CONSULTED the control plane's policy — it called the gate — which is
    its policy hook 'observed in execution' (the magazine's onboarding
    requirement). Mark it proven and, if that was the last hook missing, re-run
    the SAME onboarding gate so the agent leaves quarantine. Nothing is keyed on a
    specific agent or callback URL."""
    if not agent_id:
        return
    e = REGISTRY.get(agent_id)
    if not e:
        return
    now = time.time()
    if (e.policy_observed and e.status != "quarantined"
            and e.last_policy_ts is not None
            and now - e.last_policy_ts < _TELEMETRY_REFRESH_MIN):
        return
    fields: dict = {"policy_observed": True, "last_policy_ts": now,
                    "last_seen": now, "alive": True}
    if e.status == "quarantined":
        probe = e.model_copy(update={"policy_observed": True, "last_policy_ts": now})
        status, reason = onboarding.decide_status(probe)
        fields["status"] = status
        fields["quarantine_reason"] = reason
    updated = REGISTRY.patch(agent_id, **fields)
    if updated and e.status == "quarantined" and updated.status == "active":
        _record_event("policy_observed", agent_id, detail + " -> active")
        log.info("radar.policy.observed agent_id=%s -> active", agent_id)


def _event_has_flags(event: dict) -> bool:
    """Taxonomy-free: an execution event proves flag wiring when it carries
    feature-flag state (a `feature_flags`/`flags` key, in `extra` or top level)."""
    extra = event.get("extra") or {}
    return bool(extra.get("feature_flags") or extra.get("flags")
                or event.get("feature_flags") or event.get("flags"))


def _observe_flags(agent_id: str, detail: str = "flag wiring observed in execution") -> None:
    """Close the FEATURE-FLAG loop, mirroring _observe_telemetry/_observe_policy:
    the agent reported feature-flag state during execution, proving its flag hook
    is wired and "observed in execution". Mark it proven and, if that was the last
    hook missing, re-run the SAME onboarding gate so the agent leaves quarantine.
    Nothing is keyed on a specific agent or flag name."""
    if not agent_id:
        return
    e = REGISTRY.get(agent_id)
    if not e:
        return
    now = time.time()
    if (e.flags_observed and e.status != "quarantined"
            and e.last_flags_ts is not None
            and now - e.last_flags_ts < _TELEMETRY_REFRESH_MIN):
        return
    fields: dict = {"flags_observed": True, "last_flags_ts": now}
    if e.status == "quarantined":
        probe = e.model_copy(update={"flags_observed": True, "last_flags_ts": now})
        status, reason = onboarding.decide_status(probe)
        fields["status"] = status
        fields["quarantine_reason"] = reason
    updated = REGISTRY.patch(agent_id, **fields)
    if updated and e.status == "quarantined" and updated.status == "active":
        _record_event("flags_observed", agent_id, detail + " -> active")
        log.info("radar.flags.observed agent_id=%s -> active", agent_id)


async def _telemetry_reconciler() -> None:
    """The other half of the closed loop: an agent that STOPS exercising a proven
    control hook — telemetry OR its policy callback — loses that hook and is
    re-quarantined. Disabled when AGENT_RADAR_TELEMETRY_TTL=0. Quarantine only
    blocks governed writes, so an idle agent simply cannot write until it proves
    the hook again — it re-promotes automatically on its next observed event/gate.
    """
    if TELEMETRY_TTL <= 0:
        log.info("radar.hook.reconciler disabled (AGENT_RADAR_TELEMETRY_TTL=0)")
        return
    log.info("radar.hook.reconciler enabled ttl=%.0fs interval=%.0fs",
             TELEMETRY_TTL, TELEMETRY_RECONCILE_INTERVAL)
    while True:
        try:
            now = time.time()
            for e in REGISTRY.all():
                # Drop any REQUIRED, proven hook that has gone silent past the TTL.
                # Each is gated on its require-observed flag so a declared-trusted
                # hook (which carries no observation timestamp anyway) is never
                # reconciled out.
                stale: dict = {}
                if (REQUIRE_OBSERVED_TELEMETRY and e.telemetry_enabled
                        and e.last_telemetry_ts is not None
                        and now - e.last_telemetry_ts > TELEMETRY_TTL):
                    stale["telemetry_enabled"] = False
                if (REQUIRE_OBSERVED_POLICY and e.policy_observed
                        and e.last_policy_ts is not None
                        and now - e.last_policy_ts > TELEMETRY_TTL):
                    stale["policy_observed"] = False
                if (REQUIRE_OBSERVED_FLAGS and e.flags_observed
                        and e.last_flags_ts is not None
                        and now - e.last_flags_ts > TELEMETRY_TTL):
                    stale["flags_observed"] = False
                if not stale:
                    continue
                probe = e.model_copy(update=stale)
                status, reason = onboarding.decide_status(probe)
                REGISTRY.patch(e.id, status=status, quarantine_reason=reason, **stale)
                if status == "quarantined" and e.status != "quarantined":
                    dropped = ", ".join(k.replace("_enabled", "").replace("_observed", "")
                                        for k in stale)
                    _record_event("hook_stale", e.id,
                                  f"{dropped} hook(s) went silent -> quarantined")
                    log.warning("radar.hook.stale agent_id=%s dropped=%s -> quarantined",
                                e.id, list(stale))
        except Exception as exc:  # noqa: BLE001 — reconciler must never die
            log.warning("radar.hook.reconciler.error error=%r", exc, exc_info=True)
        await asyncio.sleep(TELEMETRY_RECONCILE_INTERVAL)


# ----------------------------------------------------------------------
# Onboarding (Temporal when available, inline fallback otherwise)
# ----------------------------------------------------------------------
async def _onboard_inline(agent_id: str) -> None:
    e = REGISTRY.get(agent_id)
    if not e:
        log.warning("radar.onboard.inline.skipped agent_id=%s reason=not_found", agent_id)
        return

    path = "inline"
    status = "quarantined"
    reason: str | None = None

    with radar_span("radar.onboard.inline", {"agent_id": agent_id, "path": path}):
        # Step 1: map identity (normalize owner/runtime/version)
        t0 = time.monotonic()
        with radar_span("radar.onboard.step.map_identity", {"agent_id": agent_id}):
            try:
                patch = onboarding.map_identity_patch(e)
                REGISTRY.patch(agent_id, **patch)
                log.info(
                    "radar.onboard.map_identity agent_id=%s owner=%s runtime=%s dur_ms=%.1f",
                    agent_id, patch.get("owner"), patch.get("runtime"),
                    (time.monotonic() - t0) * 1000,
                )
                METRICS.record_onboarding_step("map_identity", time.monotonic() - t0, "ok", path)
            except Exception as exc:
                log.error(
                    "radar.onboard.step.error step=map_identity agent_id=%s error=%r",
                    agent_id, exc, exc_info=True,
                )
                METRICS.record_onboarding_step("map_identity", time.monotonic() - t0, "error", path)
                raise

        e = REGISTRY.get(agent_id)

        # Step 2: quarantine check (verify telemetry + policy hooks)
        t0 = time.monotonic()
        with radar_span("radar.onboard.step.quarantine_check", {"agent_id": agent_id}):
            try:
                status, reason = onboarding.decide_status(e)
                REGISTRY.patch(agent_id, status=status, quarantine_reason=reason)
                log.info(
                    "radar.onboard.quarantine_check agent_id=%s status=%s reason=%r dur_ms=%.1f",
                    agent_id, status, reason, (time.monotonic() - t0) * 1000,
                )
                METRICS.record_onboarding_step("quarantine_check", time.monotonic() - t0, "ok", path)
            except Exception as exc:
                log.error(
                    "radar.onboard.step.error step=quarantine_check agent_id=%s error=%r",
                    agent_id, exc, exc_info=True,
                )
                METRICS.record_onboarding_step("quarantine_check", time.monotonic() - t0, "error", path)
                raise

        e = REGISTRY.get(agent_id)

        # Step 3: link MCP (enumerate tools if entry exposes an SSE endpoint)
        t0 = time.monotonic()
        with radar_span("radar.onboard.step.link_mcp", {"agent_id": agent_id, "kind": e.kind}):
            try:
                caps, note = await onboarding.link_mcp(e)
                REGISTRY.patch(agent_id, capabilities=caps, onboarding_state="done")
                log.info(
                    "radar.onboard.link_mcp agent_id=%s caps=%d note=%r dur_ms=%.1f",
                    agent_id, len(caps), note, (time.monotonic() - t0) * 1000,
                )
                METRICS.record_onboarding_step("link_mcp", time.monotonic() - t0, "ok", path)
            except Exception as exc:
                log.error(
                    "radar.onboard.step.error step=link_mcp agent_id=%s error=%r",
                    agent_id, exc, exc_info=True,
                )
                METRICS.record_onboarding_step("link_mcp", time.monotonic() - t0, "error", path)
                raise

        METRICS.onboarding_completed.add(1, {"status": status, "path": path})
        _record_event("onboarded", agent_id, status, reason=reason or "", path=path)
        # Onboarding run completed (inline path) -> ops.onboarding_runs. Keyed by
        # the real workflow id when present, else a stable per-agent inline key.
        _events_db.record_onboarding_run(
            getattr(e, "onboarding_workflow_id", None) or f"inline-{agent_id}",
            agent_id, "done", payload={"status": status, "path": path})
        log.info(
            "radar.onboard.completed agent_id=%s status=%s path=%s",
            agent_id, status, path,
        )


async def _onboarding_manager() -> None:
    """Trigger onboarding for any entry that hasn't been onboarded yet."""
    while True:
        try:
            for e in REGISTRY.all():
                if e.onboarding_state is not None:
                    continue
                if not (e.alive or e.source == "self"):
                    continue
                REGISTRY.patch(e.id, onboarding_state="pending")
                if STATE["temporal"] and STATE["client"] is not None:
                    # Unique workflow ID per registration run so every restart
                    # creates a new visible workflow in the Temporal UI.
                    wf_id = f"onboard-{e.id}-{int(time.time())}"
                    try:
                        await STATE["client"].start_workflow(
                            AgentOnboardingWorkflow.run,
                            e.id,
                            id=wf_id,
                            task_queue=TASK_QUEUE,
                        )
                        REGISTRY.patch(
                            e.id, onboarding_state="running", onboarding_workflow_id=wf_id
                        )
                        _events_db.record_onboarding_run(wf_id, e.id, "running")
                        log.info(
                            "radar.onboarding.temporal.started agent_id=%s workflow_id=%s",
                            e.id, wf_id,
                        )
                    except Exception as exc:
                        log.warning(
                            "radar.onboarding.temporal.fallback agent_id=%s error=%r",
                            e.id, exc,
                        )
                        await _onboard_inline(e.id)
                else:
                    log.debug("radar.onboarding.inline agent_id=%s", e.id)
                    await _onboard_inline(e.id)
        except Exception as exc:
            log.warning("radar.onboarding_manager.error error=%r", exc, exc_info=True)
        await asyncio.sleep(3)


async def _connect_temporal() -> None:
    """Best-effort: connect to Temporal and start an in-process worker."""
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker

        client = await asyncio.wait_for(Client.connect(TEMPORAL_SERVER_URL), timeout=5)

        # One worker handles both task queues — onboarding + task execution
        onboarding_worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[AgentOnboardingWorkflow],
            activities=[map_identity, quarantine_check, link_mcp, admit],
        )
        execution_worker = Worker(
            client,
            task_queue=EXEC_TASK_QUEUE,
            workflows=[AgentExecutionWorkflow],
            activities=[
                execution_setup,
                execution_llm_call,
                execution_web_search,
                execution_tool_call,
                execution_synthesize_answer,
                execution_complete,
            ],
        )
        STATE["client"] = client
        STATE["temporal"] = True
        # Two separate tasks — asyncio.gather() returns a Future in Python 3.12+
        # which create_task() rejects; explicit tasks are cleaner and cancellable.
        STATE["worker_tasks"] = [
            asyncio.create_task(onboarding_worker.run(), name="onboarding-worker"),
            asyncio.create_task(execution_worker.run(), name="execution-worker"),
        ]
        log.info(
            "radar.temporal.connected url=%s onboarding_queue=%s exec_queue=%s",
            TEMPORAL_SERVER_URL, TASK_QUEUE, EXEC_TASK_QUEUE,
        )
    except Exception as exc:
        STATE["temporal"] = False
        STATE["client"] = None
        log.info(
            "radar.temporal.unavailable url=%s reason=%r — falling back to inline onboarding",
            TEMPORAL_SERVER_URL, exc,
        )


def _gateway_upstream_port() -> int:
    """Parse the upstream model port from AWCP_GATEWAY_UPSTREAM / OLLAMA_BASE env."""
    try:
        from urllib.parse import urlparse
        upstream = os.getenv("AWCP_GATEWAY_UPSTREAM", os.getenv("OLLAMA_BASE", "http://localhost:11434"))
        parsed = urlparse(upstream)
        return parsed.port or (443 if parsed.scheme == "https" else 80)
    except Exception:
        return 11434


# Bypass detector: tracks last-reported time per agent to suppress duplicate events.
_bypass_seen: dict[str, float] = {}


async def _bypass_detector() -> None:
    """Detect registered agents making direct connections to the upstream model port
    (bypassing the LLM gateway).  Fires a radar event and logs a warning once per
    agent per 5-minute window — repeated connections produce one event, not many."""
    port = _gateway_upstream_port()
    radar_pid = os.getpid()
    log.info("radar.bypass_detector.started upstream_port=%s", port)
    while True:
        try:
            conns = await asyncio.to_thread(psutil.net_connections, "inet")
            now = time.time()
            for conn in conns:
                if not (conn.raddr and conn.raddr.port == port and conn.pid):
                    continue
                if conn.pid == radar_pid:
                    continue               # the gateway itself
                for e in REGISTRY.all():
                    if getattr(e, "pid", None) == conn.pid:
                        if now - _bypass_seen.get(e.id, 0) < 300:
                            continue       # already reported within 5 min
                        _bypass_seen[e.id] = now
                        _record_event(
                            "gateway_bypass", e.id,
                            f"direct connection to upstream port {port} detected (bypassing /llm gateway)",
                        )
                        log.warning(
                            "radar.bypass_detector agent_id=%s pid=%s upstream_port=%s",
                            e.id, conn.pid, port,
                        )
        except Exception as exc:           # noqa: BLE001
            log.debug("radar.bypass_detector.error error=%r", exc)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("radar.startup starting scanner and connecting to Temporal...")
    # Durable governance-event log (fail-open: a no-op if no DB is configured).
    _events_db.init()
    # Re-arm the approval gate (Step 2): registry.agents carries no approval state,
    # so rehydrate it from any still-open governance.approval_tokens. A no-op when
    # no DB / no open tokens.
    for _aid in _events_db.open_approval_agent_ids():
        if REGISTRY.get(_aid):
            REGISTRY.patch(_aid, approval_state="pending", status="quarantined",
                           approval_reason="awaiting operator approval (scope change)",
                           quarantine_reason="awaiting operator approval (scope change)")
            log.info("radar.startup approval gate re-armed agent_id=%s", _aid)
    SCANNER.start()
    # Crash recovery FIRST: release anything a previous (possibly SIGKILLed) radar
    # left frozen/suspended, before the reconciler re-establishes live state.
    _recover_orphaned_freezes()
    await _connect_temporal()
    mgr = asyncio.create_task(_onboarding_manager())
    tel = asyncio.create_task(_telemetry_reconciler())
    tps = asyncio.create_task(_token_process_reconciler())
    byp = asyncio.create_task(_bypass_detector())
    log.info("radar.startup complete temporal=%s", STATE["temporal"])
    try:
        yield
    finally:
        log.info("radar.shutdown stopping scanner and workers...")
        mgr.cancel()
        tel.cancel()
        tps.cancel()
        byp.cancel()
        _resume_all_frozen()   # never leave a process frozen on shutdown
        for wt in STATE.get("worker_tasks") or []:
            wt.cancel()
        SCANNER.stop()
        log.info("radar.shutdown complete")


# The radar is no longer just a standalone FastAPI app — it is an APIRouter that
# the gateway app (awcp.gateway.app) includes. HTTP routes are auto-traced by the
# gateway's instrument_fastapi(app); an APIRouter has no middleware stack of its
# own and cannot be instrumented directly. A standalone `app` is still built at
# the bottom of this file for radar-only deployments (uvicorn awcp.radar.api:app).
router = APIRouter()

# Token-aware LLM gateway (enforcement way #5): a model-call proxy under /llm
# that refuses an over-budget agent's calls at the source. Additive — mounting it
# changes no existing route; agents opt in by pointing their model base URL at it.
from awcp.radar.llm_gateway import gateway_router  # noqa: E402
router.include_router(gateway_router)

# ----------------------------------------------------------------------
# Token monitoring & control (awcp.laminar — OPTIONAL, self-contained).
# The laminar package never imports radar internals; the radar injects the
# three hooks it needs right here. A token-budget breach is mapped onto the
# EXISTING degradation ladder (policy.next_profile), so the EXISTING
# write-action gate enforces it — no second enforcement mechanism. Removing
# the src/awcp/laminar folder simply turns all of this off.
# ----------------------------------------------------------------------
try:
    from awcp import laminar as _laminar

    def _on_token_breach(agent_id: str, evaluation: dict) -> None:
        """Token budget exhausted -> step the agent one rung down its OWN ladder
        (GRACEFUL DEGRADATION — the same durable, recoverable path a failure-budget
        breach takes in /signal). INTACT from before. The IMMEDIATE hard stop that
        denies all execution while the agent is over budget is layer (2), enforced
        live by _token_blocked() at the gate / execution entrypoints — so the two
        coexist without one overriding the other."""
        e = REGISTRY.get(agent_id)
        if not e:
            return
        ladder = policy.ladder_for(e)
        if e.autonomy_profile == ladder[-1]:
            return                              # already at the hard stop
        new_profile = policy.next_profile(e.autonomy_profile, ladder)
        why = (f"token budget exhausted "
               f"({evaluation['used_tokens']}/{evaluation['budget_tokens']} tokens/window)")
        REGISTRY.patch(agent_id, autonomy_profile=new_profile,
                       autonomy_reason=why, failure_count=0)
        METRICS.record_signal(ok=False, degraded=True)
        _record_event("degraded", agent_id, f"-> {new_profile}", reason=why,
                      from_profile=e.autonomy_profile, to_profile=new_profile,
                      trigger="token_budget")
        log.warning("radar.token.degraded agent_id=%s -> %s (%s)",
                    agent_id, new_profile, why)
        # NB: the forceful hard stop (SIGSTOP / remote suspend) is deliberately
        # NOT done here. This callback runs synchronously inside an async request
        # handler, so blocking psutil/HTTP work would stall the single event loop
        # and make ONE over-budget agent freeze the radar for EVERY other agent.
        # The off-loop reconciler (_token_process_reconciler, via asyncio.to_thread)
        # applies the forceful stop for every over-budget agent within its interval,
        # and the gate / gateway already deny this agent immediately and live.

    _laminar.init_laminar(get_agent=REGISTRY.get,
                          on_breach=_on_token_breach,
                          record_event=_record_event)
    router.include_router(_laminar.router)
    _LAMINAR = True
    log.info("radar.laminar.mounted ui=/laminar/ui")
except Exception as _exc:                       # radar runs fine without the package
    _LAMINAR = False
    log.warning("radar.laminar.unavailable error=%r", _exc)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "agent"


def _to_dict(e: AgentEntry) -> dict:
    d = e.model_dump()
    if e.onboarding_workflow_id:
        d["temporal_url"] = (
            f"{TEMPORAL_UI_BASE}/namespaces/default/workflows/{e.onboarding_workflow_id}"
        )
    # surface the AUTHORITATIVE risk (max of declared + magazine-assigned) so the
    # operator sees the tier actually enforced, not just what the agent declared.
    d["authoritative_risk"] = policy.authoritative_risk(e)
    # surface the EFFECTIVE degradation policy (after risk/override resolution)
    d["effective_budget"] = policy.budget_for(e)
    d["effective_ladder"] = policy.ladder_for(e)
    # the agent's CURRENT degradation-stage directives (sampling / retry /
    # concurrency / profile / writes) so the runtime + operators can honour them
    d["effective_stage"] = policy.effective_stage(e)
    return d


@router.get("/agents")
def list_agents() -> list[dict]:
    return [_to_dict(e) for e in REGISTRY.all()]


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict:
    e = REGISTRY.get(agent_id)
    if not e:
        raise HTTPException(status_code=404, detail="agent not found")
    return _to_dict(e)


def _assert_safe_agent_urls(req: RegisterRequest) -> None:
    """SSRF guard at the registration boundary (hardening gap #2). An agent's
    declared endpoint (SSE link target) and control_endpoint (remote hard-stop
    webhook) are URLs the radar will later fetch, so reject private/link-local
    targets here with HTTP 400 instead of only refusing silently at fetch time.
    Only http(s) URLs are checked — a stdio endpoint or other non-URL transport
    is left alone. The fetch-time guards in link_mcp/_post_control stay as
    defense-in-depth (DNS can re-point between register and fetch)."""
    from awcp.radar.netguard import assert_safe_url, UnsafeURLError
    for field, url in (("endpoint", req.endpoint),
                       ("control_endpoint", req.control_endpoint)):
        if not url or not url.startswith(("http://", "https://")):
            continue
        try:
            assert_safe_url(url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=f"unsafe {field}: {exc}")


@router.post("/agents/register")
def register(req: RegisterRequest) -> dict:
    _assert_safe_agent_urls(req)
    agent_id = req.id or f"reg-{_slug(req.name)}"
    existing = REGISTRY.get(agent_id)
    entry = AgentEntry(
        id=agent_id,
        name=req.name,
        kind=req.kind,
        framework=req.framework,
        source="self",
        runtime=req.runtime,
        version=req.version,
        owner=req.owner,
        endpoint=req.endpoint,
        transport=req.transport,
        control_endpoint=req.control_endpoint,
        write_scopes=req.write_scopes,
        feature_flags=req.feature_flags,
        # Declared flags are trusted unless observed-flags is required (default
        # trusts them); the _observe_flags path flips this on when the agent
        # reports flag state in execution.
        flags_observed=(bool(req.feature_flags) and not REQUIRE_OBSERVED_FLAGS),
        policy_callbacks=req.policy_callbacks,
        # A DECLARED telemetry hook is not trusted when observed-telemetry is
        # required — the agent must actually emit telemetry to earn it (the
        # _observe_telemetry path flips it on). Legacy trust-on-declare returns
        # by setting AGENT_RADAR_REQUIRE_OBSERVED_TELEMETRY=false.
        telemetry_enabled=(req.telemetry_enabled and not REQUIRE_OBSERVED_TELEMETRY),
        # Same rule for the policy-callback hook: a DECLARED callback list is not
        # trusted when observed-policy is required — the agent must actually
        # consult policy (call the gate) to earn it (the _observe_policy path
        # flips it on). Off => trust the declared list.
        policy_observed=(bool(req.policy_callbacks) and not REQUIRE_OBSERVED_POLICY),
        risk=req.risk,
        autonomy_ladder=req.autonomy_ladder,
        failure_budget=req.failure_budget,
    )
    # Authoritative risk (hardening gap #1): a self-declared tier may only make
    # the agent MORE restrictive, never less — store the max of declared and the
    # magazine-assigned tier so the gate/budget can't be relaxed by declaring
    # "low". Onboarding's map_identity reaffirms this.
    entry.risk = policy.authoritative_risk(entry)
    # let the onboarding pipeline decide status/capabilities (re-onboard on update)
    entry.onboarding_state = None

    # Scope-drift guard (hardening gap #5): on RE-registration, compare incoming
    # write_scopes against what we already had. ADDED scopes are permission creep
    # — hold the agent for operator re-approval (sticky via approval_state) rather
    # than silently widening grants on a restart. Dropped scopes are a safe
    # reduction and applied directly.
    if existing:
        entry.first_seen = existing.first_seen
        # Carry forward the approval gate: register() is a full overwrite, so a
        # fresh payload would otherwise reset approval_state to None and let an
        # agent escape a pending re-approval just by re-registering unchanged.
        entry.approval_state = existing.approval_state
        entry.approval_reason = existing.approval_reason
        added = sorted(set(req.write_scopes) - set(existing.write_scopes or []))
        removed = sorted(set(existing.write_scopes or []) - set(req.write_scopes))
        if added:
            entry.approval_state = "pending"
            entry.approval_reason = f"scope_added: {added} — operator re-approval required"
            entry.status = "quarantined"
            entry.quarantine_reason = entry.approval_reason
            saved = REGISTRY.register(entry)
            # Durable operator gate (Step 2): the in-memory approval_state above is
            # the live gate; the canonical governance.approval_tokens row is what
            # makes it survive a restart (registry.agents has no approval column).
            # Created AFTER register() so the agent row exists for the FK. Fail-open.
            _events_db.record_approval_request(
                agent_id, req.write_scopes,
                context_diff={"added": added,
                              "previous": sorted(existing.write_scopes or []),
                              "new": sorted(req.write_scopes)},
                requested_by=req.owner,
            )
            _record_event("scope_added", agent_id,
                          f"new write_scopes {added} -> quarantined (re-approval required)",
                          added=added)
            log.warning("radar.register.scope_added agent_id=%s added=%s -> quarantined",
                        agent_id, added)
            return _to_dict(saved)
        if removed:
            _record_event("scope_removed", agent_id, f"dropped write_scopes {removed}",
                          removed=removed)
            log.info("radar.register.scope_removed agent_id=%s removed=%s", agent_id, removed)

    saved = REGISTRY.register(entry)
    _record_event("registered", saved.id, saved.name, risk=saved.risk)
    log.info(
        "radar.register agent_id=%s name=%r kind=%s framework=%s risk=%s telemetry=%s",
        saved.id, saved.name, saved.kind, saved.framework, saved.risk, saved.telemetry_enabled,
    )
    return _to_dict(saved)


@router.post("/agents/announce")
async def announce(req: RegisterRequest) -> dict:
    """Agent-initiated onboarding (Option B announce path).

    The agent calls this at startup instead of /agents/register.  Two
    differences from /agents/register:
      1. The REGISTRY entry is created with onboarding_state='pending' before
         the Temporal workflow is queued, so the agent is immediately visible
         in GET /agents and gate calls work from the first millisecond.
      2. The Temporal workflow is started synchronously inside this request —
         no wait for _onboarding_manager's 3-second poll cycle.
    The response carries onboarding_workflow_id and temporal_url so the
    caller can observe its own onboarding progress.
    Falls back to inline onboarding when Temporal is unavailable.
    """
    _assert_safe_agent_urls(req)
    agent_id = req.id or f"reg-{_slug(req.name)}"

    # Check for a restart: same slug ID, new PID.  Patch liveness + reset
    # onboarding state so the pipeline re-runs for the new process instance.
    existing = REGISTRY.get(agent_id)
    if existing:
        REGISTRY.patch(agent_id, pid=req.pid, alive=True,
                       last_seen=time.time(), onboarding_state=None,
                       telemetry_enabled=False, policy_observed=False,
                       flags_observed=False)
        _record_event("announced", agent_id, f"{req.name} re-announced (restart)", risk=existing.risk)
        log.info("radar.announce.restart agent_id=%s pid=%s", agent_id, req.pid)
    else:
        entry = AgentEntry(
            id=agent_id,
            name=req.name,
            kind=req.kind,
            framework=req.framework,
            source="self",
            pid=req.pid,
            runtime=req.runtime,
            version=req.version,
            owner=req.owner,
            endpoint=req.endpoint,
            transport=req.transport,
            control_endpoint=req.control_endpoint,
            write_scopes=req.write_scopes,
            feature_flags=req.feature_flags,
            flags_observed=(bool(req.feature_flags) and not REQUIRE_OBSERVED_FLAGS),
            policy_callbacks=req.policy_callbacks,
            telemetry_enabled=(req.telemetry_enabled and not REQUIRE_OBSERVED_TELEMETRY),
            policy_observed=(bool(req.policy_callbacks) and not REQUIRE_OBSERVED_POLICY),
            risk=req.risk,
            autonomy_ladder=req.autonomy_ladder,
            failure_budget=req.failure_budget,
            # Set pending immediately — _onboarding_manager skips entries that
            # already have onboarding_state set, so there is no double-onboard.
            onboarding_state="pending",
        )
        # Authoritative risk (hardening gap #1) — same rule as /agents/register:
        # declared tier may only tighten, never relax.
        entry.risk = policy.authoritative_risk(entry)
        REGISTRY.register(entry)
        _record_event("announced", agent_id, req.name, risk=entry.risk)
        log.info(
            "radar.announce agent_id=%s name=%r framework=%s pid=%s",
            agent_id, req.name, req.framework, req.pid,
        )

    # Start the Temporal onboarding workflow immediately (no poll cycle wait).
    if STATE["temporal"] and STATE["client"] is not None:
        pid_tag = f"-{req.pid}" if req.pid else ""
        wf_id = f"onboard-{agent_id}{pid_tag}-{int(time.time())}"
        try:
            await STATE["client"].start_workflow(
                AgentOnboardingWorkflow.run,
                agent_id,
                id=wf_id,
                task_queue=TASK_QUEUE,
            )
            REGISTRY.patch(agent_id, onboarding_state="running",
                           onboarding_workflow_id=wf_id)
            _events_db.record_onboarding_run(wf_id, agent_id, "running")
            log.info(
                "radar.announce.temporal.started agent_id=%s workflow_id=%s",
                agent_id, wf_id,
            )
        except Exception as exc:
            log.warning(
                "radar.announce.temporal.fallback agent_id=%s error=%r",
                agent_id, exc,
            )
            await _onboard_inline(agent_id)
    else:
        log.debug("radar.announce.inline agent_id=%s (temporal unavailable)", agent_id)
        await _onboard_inline(agent_id)

    return _to_dict(REGISTRY.get(agent_id))


# ----------------------------------------------------------------------
# Write-action gate + degradation ladder (governance, ported from awcp_agents)
# ----------------------------------------------------------------------
class GateRequest(BaseModel):
    action: str = ""
    write: bool = True            # the magazine gates WRITE-capable actions
    scope: str = ""               # the action's write scope (checked vs declared write_scopes)
    action_class: str = ""        # optional class label (e.g. "cross_system" → operator approval)
    token_id: str = ""            # an approval token the caller presents for a gated write
    workflow_id: str = ""         # branch context recorded on an issued approval token
    branch_id: str = ""


class SignalRequest(BaseModel):
    ok: bool                      # did the agent's last action succeed?
    reason: str = ""


class AutonomyRequest(BaseModel):
    profile: str                  # operator override: active|recommendation_only|suspended


def _require(agent_id: str) -> AgentEntry:
    e = REGISTRY.get(agent_id)
    if not e:
        raise HTTPException(status_code=404, detail="agent not found")
    return e


@router.post("/agents/{agent_id}/gate")
def gate(agent_id: str, req: GateRequest) -> dict:
    """Evaluate whether an agent may perform an action (the write-action gate).
    An external agent/interceptor calls this before a state-changing action."""
    e = _require(agent_id)

    # The agent consulting the gate IS its policy callback being exercised in
    # execution — observe it so the hook is proven (magazine onboarding).
    _observe_policy(agent_id)

    # Token HARD STOP (control plane): an over-budget agent is denied EVERY
    # action — including reads — so it cannot execute any further. Checked before
    # the normal write-gate, which would otherwise let reads through.
    blocked = _token_blocked(agent_id)
    if blocked is not None:
        METRICS.record_gate(decision="deny", mode="token_hard_stop", duration=0.0, risk=e.risk)
        _record_event("gate", agent_id, "deny (token_hard_stop)", action=req.action)
        return {"agent_id": agent_id, "action": req.action, "mode": "token_hard_stop",
                "decision": "deny",
                "reason": "token budget exhausted — hard stop by control plane",
                "budget": blocked, "status": e.status,
                "autonomy_profile": e.autonomy_profile}

    t0 = time.monotonic()
    # Policy Decision Point: OPA when AWCP_OPA_URL is set, else policy.evaluate_action
    # (opa.evaluate_action falls back to it on any OPA error — fail-secure). The
    # decision carries the magazine's 4-value gate kind (auto_authorized /
    # awaiting_token / awaiting_operator / denied) alongside the allow|deny verdict.
    decision = opa.evaluate_action(e, action=req.action, is_write=req.write,
                                   scope=req.scope, action_class=req.action_class)
    # Resolve any approval-token requirement (issue/verify) — magazine Scenario B.
    decision = _resolve_approval(e, req, decision)
    elapsed = time.monotonic() - t0
    METRICS.record_gate(
        decision=decision["decision"],
        mode=decision["mode"],
        duration=elapsed,
        risk=e.risk,
    )
    log.info(
        "radar.gate agent_id=%s action=%r decision=%s gate=%s mode=%s engine=%s risk=%s dur_ms=%.2f",
        agent_id, (req.action or "")[:64], decision["decision"], decision.get("gate"),
        decision["mode"], decision.get("engine"), e.risk, elapsed * 1000,
    )
    # Durable mirror: the gate kind drives governance.policy_decisions.decision
    # (auto_authorized | awaiting_token | awaiting_operator | denied). Allows stay
    # in the live ring only; denies/holds are persisted (see _record_event).
    _record_event("gate", agent_id, f"{decision['decision']} ({decision['mode']})",
                  action=req.action, decision=decision.get("gate"),
                  scope=req.scope or None, reason=decision.get("reason"),
                  token_id=decision.get("token_id"), workflow_id=req.workflow_id or None)
    return {"agent_id": agent_id, **decision,
            "status": e.status, "autonomy_profile": e.autonomy_profile}


def _resolve_approval(e: AgentEntry, req: GateRequest, decision: dict) -> dict:
    """Turn an `awaiting_token` / `awaiting_operator` PDP decision into an
    enforceable one by issuing or verifying an expiring approval token.

      * awaiting_token + a presented token  -> verify + single-use consume -> allow
      * awaiting_token + no token           -> issue a pending token, hold (deny)
      * awaiting_operator                   -> issue a pending operator token, hold

    Fail-secure: if the governance DB is unavailable no token can be issued or
    verified, so the action stays denied rather than being granted un-auditably."""
    gate_kind = decision.get("gate")
    if gate_kind not in ("awaiting_token", "awaiting_operator"):
        return decision

    risk_tier = policy.authoritative_risk(e)

    # A caller presenting a token for a token-gated write: verify and consume it.
    if gate_kind == "awaiting_token" and req.token_id:
        ok, why = tokens.verify_and_consume(req.token_id, agent_id=e.id, scope=req.scope)
        if ok:
            _record_event("token_consumed", e.id,
                          f"approval token consumed for {req.action!r} ({why})",
                          action=req.action, token_id=req.token_id, decision="auto_authorized")
            return {**decision, "decision": "allow", "gate": "auto_authorized",
                    "mode": "token_consumed",
                    "reason": f"approved — single-use approval token consumed ({why})",
                    "token_id": req.token_id}
        return {**decision, "reason": f"approval token rejected: {why}", "token_id": req.token_id}

    # No (valid) token: issue a pending, branch-scoped, expiring token and hold.
    action_class = req.action_class or ("operator_review" if gate_kind == "awaiting_operator"
                                        else "gated_write")
    tid = tokens.issue(e, action=req.action, action_class=action_class, scope=req.scope,
                       risk_tier=risk_tier, workflow_id=req.workflow_id, branch_id=req.branch_id)
    if not tid:
        return {**decision,
                "reason": (decision.get("reason", "") +
                           " — approval tokens unavailable (no governance DB); action denied")}
    _record_event("token_requested", e.id,
                  f"approval token issued for {req.action!r} ({gate_kind})",
                  action=req.action, token_id=tid, decision=gate_kind, scope=req.scope or None)
    return {**decision, "token_id": tid,
            "reason": decision.get("reason", "") + " — pending approval token issued"}


@router.post("/agents/{agent_id}/signal")
def signal(agent_id: str, req: SignalRequest) -> dict:
    """Report an execution outcome. Failures step autonomy down the ladder once
    the failure budget is exhausted (graceful degradation)."""
    e = _require(agent_id)
    # An execution-outcome report is observed telemetry for this agent.
    _observe_telemetry(agent_id, "execution signal")
    result = policy.apply_signal(e, ok=req.ok, reason=req.reason)
    updated = REGISTRY.patch(agent_id, **result["patch"])
    budget = policy.budget_for(updated)
    METRICS.record_signal(
        ok=req.ok,
        degraded=result["degraded"],
        count=updated.failure_count,
        budget=budget,
    )
    if result["degraded"]:
        log.warning(
            "radar.signal.degraded agent_id=%s from=%s to=%s count=%d budget=%d reason=%r",
            agent_id, e.autonomy_profile, updated.autonomy_profile,
            updated.failure_count, budget, req.reason,
        )
        _record_event("degraded", agent_id,
                      f"-> {updated.autonomy_profile}", reason=updated.autonomy_reason or "",
                      from_profile=e.autonomy_profile, to_profile=updated.autonomy_profile,
                      trigger="failure_budget")
    elif not req.ok:
        log.info(
            "radar.signal.failure agent_id=%s count=%d budget=%d reason=%r",
            agent_id, updated.failure_count, budget, req.reason,
        )
        _record_event("signal", agent_id, f"failure ({updated.failure_count})",
                      reason=req.reason)
    else:
        log.debug("radar.signal.ok agent_id=%s", agent_id)
    return {
        "agent_id": agent_id,
        "degraded": result["degraded"],
        "autonomy_profile": updated.autonomy_profile,
        "autonomy_reason": updated.autonomy_reason,
        "failure_count": updated.failure_count,
        # Hand the runtime its CURRENT degradation directives at the exact moment
        # autonomy changed, so it applies the magazine's tighten-retry / lower-
        # concurrency / safer-profile / raise-sampling for the next step.
        "effective_stage": policy.effective_stage(updated),
    }


@router.post("/agents/{agent_id}/autonomy")
def set_autonomy(agent_id: str, req: AutonomyRequest) -> dict:
    """Operator override — set the autonomy profile directly (e.g. restore to active)."""
    e = _require(agent_id)
    ladder = policy.ladder_for(e)
    if req.profile not in ladder:
        raise HTTPException(status_code=400, detail=f"profile must be one of {ladder}")
    updated = REGISTRY.patch(
        agent_id, autonomy_profile=req.profile, failure_count=0,
        autonomy_reason=f"operator set to {req.profile}",
    )
    _record_event("autonomy", agent_id, f"operator set to {req.profile}",
                  from_profile=e.autonomy_profile, to_profile=req.profile, trigger="operator")
    return {"agent_id": agent_id, "autonomy_profile": updated.autonomy_profile}


@router.post("/agents/{agent_id}/approve")
def approve(agent_id: str) -> dict:
    """Operator action — clear a pending re-approval gate (hardening gap #5).
    After an agent was held for adding write_scopes, the operator approves the
    new grants here; the approval gate lifts and the SAME onboarding hook check
    decides whether it returns to active."""
    e = _require(agent_id)
    if e.approval_state != "pending":
        return {"agent_id": agent_id, "approval_state": e.approval_state,
                "status": e.status, "note": "no pending approval"}
    probe = e.model_copy(update={"approval_state": "approved", "approval_reason": None})
    status, reason = onboarding.decide_status(probe)
    updated = REGISTRY.patch(agent_id, approval_state="approved", approval_reason=None,
                             status=status, quarantine_reason=reason)
    # Settle the durable token(s) so the gate does NOT re-arm on the next restart.
    _events_db.decide_approval(agent_id, "approved")
    _record_event("approved", agent_id,
                  f"operator approved scope change -> {status}",
                  scopes=list(updated.write_scopes or []))
    log.info("radar.approve agent_id=%s -> status=%s scopes=%s",
             agent_id, status, list(updated.write_scopes or []))
    return {"agent_id": agent_id, "approval_state": "approved",
            "status": updated.status, "write_scopes": list(updated.write_scopes or [])}


# ── Per-action approval tokens (magazine Scenario B) ──────────────────────────
# These govern a single high-risk WRITE (distinct from /approve above, which gates
# an AGENT after a scope expansion). The gate issues a pending token when a
# token-gated write arrives without one; the operator approves/denies it here; the
# agent presents it back at the gate where it is verified + single-use consumed.
class TokenDecisionRequest(BaseModel):
    decided_by: str = ""          # operator identity recorded on the token


@router.get("/agents/{agent_id}/tokens")
def list_tokens(agent_id: str, limit: int = 50) -> list[dict]:
    """All approval tokens for an agent, newest first (durable governance store)."""
    _require(agent_id)
    return tokens.list_for_agent(agent_id, limit=limit)


@router.post("/agents/{agent_id}/tokens/{token_id}/approve")
def approve_token(agent_id: str, token_id: str, req: TokenDecisionRequest) -> dict:
    """Operator approves one pending approval token; the agent may then present it
    at the gate to perform the single gated write within its expiry window."""
    _require(agent_id)
    tok = tokens.get(token_id)
    if not tok or tok.get("agent_id") != agent_id:
        raise HTTPException(status_code=404, detail="token not found for this agent")
    if not tokens.decide(token_id, "approved", req.decided_by or None):
        raise HTTPException(status_code=409,
                            detail=f"token not pending (status={tok.get('status')})")
    _record_event("token_approved", agent_id, f"operator approved token {token_id}",
                  token_id=token_id, decided_by=req.decided_by or None)
    log.info("radar.token.approved agent_id=%s token_id=%s", agent_id, token_id)
    return {"agent_id": agent_id, "token_id": token_id, "status": "approved"}


@router.post("/agents/{agent_id}/tokens/{token_id}/deny")
def deny_token(agent_id: str, token_id: str, req: TokenDecisionRequest) -> dict:
    """Operator denies one pending approval token; the gated write stays blocked."""
    _require(agent_id)
    tok = tokens.get(token_id)
    if not tok or tok.get("agent_id") != agent_id:
        raise HTTPException(status_code=404, detail="token not found for this agent")
    if not tokens.decide(token_id, "denied", req.decided_by or None):
        raise HTTPException(status_code=409,
                            detail=f"token not pending (status={tok.get('status')})")
    _record_event("token_denied", agent_id, f"operator denied token {token_id}",
                  token_id=token_id, decided_by=req.decided_by or None)
    log.info("radar.token.denied agent_id=%s token_id=%s", agent_id, token_id)
    return {"agent_id": agent_id, "token_id": token_id, "status": "denied"}


class RiskRequest(BaseModel):
    # both optional → the operator can set just the tier, just the budget, or both
    risk: str | None = None           # the risk tier (e.g. low | medium | high)
    token_budget: int | None = None   # explicit per-agent budget; 0 clears it (→ use tier)


@router.post("/agents/{agent_id}/risk")
def set_risk(agent_id: str, req: RiskRequest) -> dict:
    """Operator override — set an agent's RISK tier and/or its explicit per-agent
    token budget (the magazine's declared budget). Risk drives the budget tier;
    an explicit token_budget outranks the tier; sending token_budget=0 clears it
    so the agent falls back to its tier. Free-form tier string (no hardcoded set)."""
    _require(agent_id)
    patch: dict = {}
    if req.risk is not None and req.risk.strip():
        patch["risk"] = req.risk.strip().lower()
    if req.token_budget is not None:
        patch["token_budget"] = req.token_budget if req.token_budget > 0 else None
    if not patch:
        raise HTTPException(status_code=400, detail="provide risk and/or token_budget")
    updated = REGISTRY.patch(agent_id, **patch)
    detail = " ".join(f"{k}={patch[k]}" for k in patch)
    _record_event("risk", agent_id, f"operator set {detail}")
    return {"agent_id": agent_id, "risk": updated.risk,
            "token_budget": getattr(updated, "token_budget", None)}


@router.delete("/agents/{agent_id}")
def deregister(agent_id: str) -> dict:
    """Operator action — remove an entry from the inventory (registry hygiene).
    A still-running scanned process will be re-detected on the next scan."""
    # Release ANY hard stop the control plane applied BEFORE removing the entry,
    # so the operator regains full control of the process. A SIGSTOP'd process
    # ignores SIGTERM until it is continued, so without this SIGCONT the operator
    # cannot turn the agent off; once resumed (and untracked) the radar will not
    # touch it again.
    _token_enforce_resume(agent_id)
    if not REGISTRY.remove(agent_id):
        raise HTTPException(status_code=404, detail="agent not found")
    _record_event("removed", agent_id, "operator removed entry (hard stop released)")
    return {"ok": True, "removed": agent_id}


# ----------------------------------------------------------------------
# Agent task execution — start workflow, receive events, complete
# ----------------------------------------------------------------------
class TaskExecStartRequest(BaseModel):
    agent_id: str
    task_id: str
    goal: str
    framework: str = ""


class TaskExecEventRequest(BaseModel):
    type: str
    tool_name: str = ""
    model: str = ""
    query: str = ""
    risk: str = ""
    gate: str = "allowed"
    http_status: int = 200
    call_n: int = 1
    result_len: int = 0
    tools_used: list[str] = []
    extra: dict = {}


class TaskExecCompleteRequest(BaseModel):
    status: str = "done"
    result: str = ""
    tools_used: list[str] = []
    error: str = ""


@router.post("/tasks/execution/start")
async def execution_start(req: TaskExecStartRequest) -> dict:
    """Start an AgentExecutionWorkflow for a task prompt."""
    # token monitor learns task->agent BEFORE any Temporal gating, so token
    # accounting keeps working when Temporal is unavailable
    if _LAMINAR:
        _laminar.on_execution_start(req.model_dump())
    # Attribute later events to this agent, and observe telemetry now (a started
    # governed execution reported to the control plane is itself telemetry).
    STATE["exec_agents"][req.task_id] = req.agent_id
    _observe_telemetry(req.agent_id, "execution started")

    # Token HARD STOP: the control plane refuses to launch a governed execution
    # for an agent already over its token budget.
    blocked = _token_blocked(req.agent_id)
    if blocked is not None:
        _note_token_block(req.agent_id, blocked, "execution start")
        STATE["exec_agents"].pop(req.task_id, None)
        log.warning("radar.exec.start.blocked agent_id=%s task_id=%s reason=token_budget",
                    req.agent_id, req.task_id)
        return {"ok": False, "reason": "token_budget_exhausted", "blocked": True,
                "budget": blocked}

    if not (STATE["temporal"] and STATE["client"]):
        log.debug("radar.exec.start.skipped reason=temporal_unavailable task_id=%s", req.task_id)
        return {"ok": False, "reason": "temporal_unavailable"}

    wf_id = f"task-{req.agent_id}-{req.task_id}"
    try:
        handle = await STATE["client"].start_workflow(
            AgentExecutionWorkflow.run,
            {"agent_id": req.agent_id, "task_id": req.task_id,
             "goal": req.goal, "framework": req.framework},
            id=wf_id,
            task_queue=EXEC_TASK_QUEUE,
        )
        STATE["exec_workflows"][req.task_id] = wf_id
        log.info(
            "radar.exec.started agent_id=%s task_id=%s workflow_id=%s",
            req.agent_id, req.task_id, wf_id,
        )
        return {"ok": True, "workflow_id": wf_id}
    except Exception as exc:
        log.warning("radar.exec.start.failed task_id=%s error=%r", req.task_id, exc)
        return {"ok": False, "reason": str(exc)[:200]}


@router.post("/tasks/execution/{task_id}/event")
async def execution_event(task_id: str, req: TaskExecEventRequest) -> dict:
    """Forward a real-time execution event to the running AgentExecutionWorkflow."""
    event = req.model_dump()
    # token monitor taps EVERY event first (taxonomy-free: any event carrying
    # token counts in .extra/top level is recorded; others are ignored). The
    # budget evaluation is returned to the agent so a cooperative runtime can
    # slow down / stop on warn|exhausted — advisory control on top of the
    # authoritative gate, which starts denying once the breach degrades autonomy.
    token_budget = _laminar.on_execution_event(task_id, event) if _LAMINAR else None

    # Observed telemetry: a real-time execution event reached the control plane.
    # Resolve the agent from the task map (fallback: the laminar evaluation).
    agent_id = STATE["exec_agents"].get(task_id)
    if not agent_id and isinstance(token_budget, dict):
        agent_id = token_budget.get("agent_id")
    if agent_id:
        _observe_telemetry(agent_id)
        # If the event reports feature-flag state, that proves flag wiring is
        # observed in execution (the magazine's third onboarding hook).
        if _event_has_flags(event):
            _observe_flags(agent_id)

    # Degradation directives (magazine Step 04, the half the control plane owns):
    #  • hand the runtime its CURRENT stage directives every step so it applies
    #    tighten-retry / lower-concurrency / safer-profile for the next call;
    #  • for a DEGRADED agent, capture the step as evidence — the control-plane
    #    "increase trace sampling" effect (more capture when things go wrong).
    stage_directive = None
    if agent_id:
        _ent = REGISTRY.get(agent_id)
        if _ent is not None:
            stage_directive = policy.effective_stage(_ent)
            if stage_directive.get("stage") not in (None, "active"):
                _record_event("degraded_step", agent_id,
                              f"{req.type or 'step'} captured (stage={stage_directive['stage']})",
                              stage=stage_directive.get("stage"))

    # Token HARD STOP mid-flight: if this event tipped the agent over its budget
    # (or it is already over), the control plane halts the loop — it stops
    # forwarding the event and signals the workflow to finish, so no further
    # step runs. The agent does not get to decide.
    exhausted = isinstance(token_budget, dict) and token_budget.get("state") == "exhausted"
    if not exhausted and agent_id:
        live = _token_blocked(agent_id)
        if live is not None:
            exhausted, token_budget = True, live
    if exhausted and agent_id:
        _note_token_block(agent_id, token_budget if isinstance(token_budget, dict) else None,
                          "execution event")
        wf_id = STATE["exec_workflows"].pop(task_id, None)
        STATE["exec_agents"].pop(task_id, None)
        if wf_id and STATE["temporal"] and STATE["client"]:
            try:
                handle = STATE["client"].get_workflow_handle(wf_id)
                await handle.signal(
                    AgentExecutionWorkflow.finish,
                    {"status": "blocked", "error": "token budget exhausted — hard stop"},
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("radar.token.hardstop.finish_failed task_id=%s error=%r", task_id, exc)
        log.warning("radar.exec.event.blocked agent_id=%s task_id=%s reason=token_budget",
                    agent_id, task_id)
        return {"ok": False, "reason": "token_budget_exhausted_hard_stop",
                "token_budget": token_budget}

    wf_id = STATE["exec_workflows"].get(task_id)
    if not wf_id or not (STATE["temporal"] and STATE["client"]):
        return {"ok": False, "reason": "no_active_workflow",
                "token_budget": token_budget, "effective_stage": stage_directive}

    try:
        handle = STATE["client"].get_workflow_handle(wf_id)
        await handle.signal(AgentExecutionWorkflow.push_event, event)
        log.debug("radar.exec.event task_id=%s type=%s", task_id, req.type)
        return {"ok": True, "token_budget": token_budget, "effective_stage": stage_directive}
    except Exception as exc:
        log.warning("radar.exec.event.failed task_id=%s error=%r", task_id, exc)
        return {"ok": False, "reason": str(exc)[:200], "token_budget": token_budget}


@router.post("/tasks/execution/{task_id}/complete")
async def execution_complete_ep(task_id: str, req: TaskExecCompleteRequest) -> dict:
    """Signal the AgentExecutionWorkflow that the task is done."""
    if _LAMINAR:
        _laminar.on_execution_complete(task_id, req.model_dump())
    STATE["exec_agents"].pop(task_id, None)
    wf_id = STATE["exec_workflows"].pop(task_id, None)
    if not wf_id or not (STATE["temporal"] and STATE["client"]):
        return {"ok": False, "reason": "no_active_workflow"}

    outcome = req.model_dump()
    try:
        handle = STATE["client"].get_workflow_handle(wf_id)
        await handle.signal(AgentExecutionWorkflow.finish, outcome)
        log.info(
            "radar.exec.completed task_id=%s status=%s workflow_id=%s",
            task_id, req.status, wf_id,
        )
        return {"ok": True, "workflow_id": wf_id}
    except Exception as exc:
        log.warning("radar.exec.complete.failed task_id=%s error=%r", task_id, exc)
        return {"ok": False, "reason": str(exc)[:200]}


@router.get("/events")
def events(limit: int = 50) -> list[dict]:
    """The recent-decisions log (newest first). A live registry audit view — not
    the durable Evidence Ledger."""
    return list(_EVENTS)[: max(1, min(limit, _EVENTS.maxlen or 200))]


@router.get("/events/audit")
def events_audit(agent_id: str = "", since: float = 0.0,
                 event_type: str = "", limit: int = 100) -> dict:
    """The DURABLE governance audit trail, unified across the canonical tables
    (evidence.ledger + governance.policy_decisions + governance.degradation_events)
    — survives restarts, unlike GET /events. Filter by agent_id, event_type, and
    `since` (UNIX epoch seconds). Returns {"enabled": <bool>, "events": [...]};
    enabled=false means no DB is configured and only GET /events is available."""
    return {
        "enabled": _events_db.enabled(),
        "events": _events_db.query(
            agent_id=agent_id or None,
            since=since or None,
            event_type=event_type or None,
            limit=limit,
        ),
    }


@router.get("/healthz")
def healthz() -> dict:
    agents = REGISTRY.all()
    by_kind: dict[str, int] = {}
    by_autonomy: dict[str, int] = {}
    for a in agents:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        by_autonomy[a.autonomy_profile] = by_autonomy.get(a.autonomy_profile, 0) + 1
    return {
        "status": "ok",
        "scan_count": REGISTRY.scan_count,
        "agent_count": len(agents),
        "quarantined": sum(1 for a in agents if a.status == "quarantined"),
        "by_kind": by_kind,
        "by_autonomy": by_autonomy,
        "temporal_connected": STATE["temporal"],
        "otel_enabled": _OTEL_ENABLED,
        "laminar": _laminar.status_summary() if _LAMINAR else {"enabled": False},
    }


@router.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


# ----------------------------------------------------------------------
# Standalone ASGI app — radar-only deployments.
#
# The AWCP gateway (awcp.gateway.app) imports `router` above and mounts it, so it
# does NOT use this object. It remains for a radar-only deployment that serves
# `awcp.radar.api:app` directly (e.g. `uvicorn awcp.radar.api:app --port 8090`),
# with every route — including /llm/*, /laminar/* and the web UI — living at the
# ROOT, which is what the bundled UI's absolute links expect.
#
# Defining `app` at import time is required so `uvicorn awcp.radar.api:app` can
# find it. When the gateway imports this module the object is simply created and
# left unused (its lifespan only runs if uvicorn actually serves it).
# ----------------------------------------------------------------------
from awcp.observability.middleware import instrument_fastapi, instrument_requests  # noqa: E402

app = FastAPI(title="Agent Radar", lifespan=lifespan)
instrument_fastapi(app)        # every radar HTTP route is auto-traced
instrument_requests()          # outbound HTTP calls (link_mcp, etc.) are traced
app.include_router(router)     # radar routes + the mounted /laminar/* + /llm/* routes
