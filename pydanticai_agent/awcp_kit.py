"""AWCP runtime kit — shared helpers that turn a framework agent into a fully
INDEPENDENT, self-contained task-worker runtime.

Design: the agent is DECOUPLED from the AWCP control plane. It sends nothing to
AWCP — no self-registration, no gate calls, no signals. The AWCP radar detects
the agent AUTONOMOUSLY by scanning the running process (it reads this agent's
framework import). Governance shown to AWCP is therefore observe-only.

What the agent governs ITSELF (local policy, no control plane needed):
  * risk tiers on its write actions (medium = local, high = external);
  * an operator APPROVAL step for high-risk external writes;
  * a task queue + background worker that executes goals in multiple steps.

Only Python stdlib is used, so each agent stays independent of any control-plane
code. The kit also provides web_search, the task-console UI, and the worker.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

# The agent sends NOTHING to AWCP. It runs standalone; the radar detects it by
# scanning the process. AGENT_NAME is set by mount() and only labels external
# writes (so a receiver can tell which agent posted) — it is not sent to AWCP.
AGENT_NAME = "agent"


def sse(event: dict) -> str:
    """Format one Server-Sent-Events frame."""
    return "data: " + json.dumps(event) + "\n\n"


def web_search(query: str, max_results: int = 5) -> str:
    """Free web search (DuckDuckGo, no API key). Returns the top results' title,
    link and snippet — the raw material the model reads to answer current-info
    questions. Shared so every agent searches the web the same way."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:  # older package name
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max(1, min(max_results, 8))))
        if not results:
            return "No web results found."
        return "\n\n".join(
            f"Title: {r.get('title', '')}\nLink: {r.get('href', '')}\n{r.get('body', '')}"
            for r in results
        )
    except Exception as e:  # noqa: BLE001
        return f"web_search unavailable: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Browser chat UI (served by the agent at GET /). Framework-agnostic: it reads
# the agent's identity/tools from GET /info and streams answers from POST /stream.
# --------------------------------------------------------------------------
UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AWCP Agent</title>
<style>
:root{--bg:#0b0f17;--panel:#121826;--line:#1f2937;--fg:#e5e7eb;--mut:#9ca3af;--acc:#6366f1;--ok:#22c55e;--warn:#f59e0b}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg);height:100vh;display:flex;flex-direction:column}
header{padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel);display:flex;align-items:center;gap:10px;flex-wrap:wrap}
header h1{font-size:16px;margin:0 8px 0 0}
.badge{font-size:12px;color:var(--mut);background:#0b1220;border:1px solid var(--line);padding:3px 9px;border-radius:999px}
.badge.ok{color:var(--ok);border-color:#14532d}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.chip{font-size:11px;background:#0b1220;border:1px solid var(--line);color:#a5b4fc;padding:2px 8px;border-radius:6px}
#log{flex:1;overflow:auto;padding:20px;display:flex;flex-direction:column;gap:14px}
.msg{max-width:820px;padding:10px 14px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.me{align-self:flex-end;background:var(--acc);color:#fff;border-bottom-right-radius:3px}
.bot{align-self:flex-start;background:var(--panel);border:1px solid var(--line);border-bottom-left-radius:3px}
.tools{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.tcall{font-size:11px;color:var(--warn);border:1px solid #78350f;background:#1c1408;padding:1px 7px;border-radius:6px}
footer{border-top:1px solid var(--line);background:var(--panel);padding:12px 16px;display:flex;gap:10px}
textarea{flex:1;resize:none;height:48px;background:#0b1220;color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:13px}
button{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:0 18px;font-weight:600;cursor:pointer}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--mut)}
button:disabled{opacity:.5;cursor:default}
.empty{color:var(--mut);text-align:center;margin:auto;max-width:420px}
</style>
</head>
<body>
<header>
  <h1 id="name">Agent</h1>
  <span class="badge" id="fw">framework</span>
  <span class="badge" id="model">model</span>
  <span class="badge" id="reg">registry</span>
  <div class="chips" id="tools"></div>
</header>
<div id="log"><div class="empty" id="empty">Send a task below. The agent answers here, streaming, with the tools it calls shown as chips.</div></div>
<footer>
  <textarea id="in" placeholder="Type a task and press Enter (Shift+Enter for newline)…"></textarea>
  <button id="send">Send</button>
  <button id="reset" class="ghost">Reset</button>
</footer>
<script>
const session = (crypto.randomUUID && crypto.randomUUID()) || String(Math.random());
const log = document.getElementById('log');
const inp = document.getElementById('in');
const sendBtn = document.getElementById('send');
function add(cls, text){
  const e = document.getElementById('empty'); if(e) e.remove();
  const d = document.createElement('div'); d.className = 'msg ' + cls; d.textContent = text || '';
  log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
}
function chip(wrap, name){
  if([...wrap.children].some(c=>c.textContent.includes(name))) return;
  const c = document.createElement('span'); c.className='tcall'; c.textContent='⚙ '+name; wrap.appendChild(c);
}
async function loadInfo(){
  try{
    const j = await (await fetch('/info')).json();
    document.getElementById('name').textContent = j.agent || 'Agent';
    document.getElementById('fw').textContent = j.framework || '';
    document.getElementById('model').textContent = j.model || '';
    document.title = j.agent || 'Agent';
    const t = document.getElementById('tools');
    (j.tools||[]).forEach(n=>{const c=document.createElement('span');c.className='chip';c.textContent=n;t.appendChild(c);});
    const reg = document.getElementById('reg');
    reg.textContent = j.registered ? 'registry: active' : 'registry: standalone';
    if(j.registered) reg.classList.add('ok');
  }catch(e){}
}
async function send(){
  const text = inp.value.trim(); if(!text) return;
  inp.value=''; add('me', text); sendBtn.disabled = true;
  const bot = add('bot', ''); const body = document.createElement('span'); bot.appendChild(body);
  const toolWrap = document.createElement('div'); toolWrap.className='tools'; bot.appendChild(toolWrap);
  try{
    const resp = await fetch('/stream', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({input:text, session})});
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf='';
    while(true){
      const {done, value} = await reader.read(); if(done) break;
      buf += dec.decode(value, {stream:true}); let i;
      while((i = buf.indexOf('\n\n')) >= 0){
        const raw = buf.slice(0, i); buf = buf.slice(i+2);
        const dl = raw.split('\n').find(l=>l.startsWith('data:')); if(!dl) continue;
        let ev; try{ ev = JSON.parse(dl.slice(5).trim()); }catch(_){ continue; }
        if(ev.type==='token'){ body.textContent += ev.text; }
        else if(ev.type==='tool'){ chip(toolWrap, ev.name); }
        else if(ev.type==='done'){ (ev.tools_used||[]).forEach(n=>chip(toolWrap, n)); }
        else if(ev.type==='error'){ body.textContent += (body.textContent?'\n':'') + '[error] ' + ev.message; }
        log.scrollTop = log.scrollHeight;
      }
    }
    if(!body.textContent) body.textContent = '(no output)';
  }catch(e){ body.textContent += '\n[network error] ' + e; }
  sendBtn.disabled = false; inp.focus();
}
sendBtn.onclick = send;
inp.addEventListener('keydown', e=>{ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send(); }});
document.getElementById('reset').onclick = async ()=>{
  try{ await fetch('/reset',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({session})}); }catch(e){}
  log.innerHTML=''; add('bot','(conversation reset)');
};
loadInfo();
</script>
</body>
</html>
"""


# ==========================================================================
# AUTONOMOUS TASK-WORKER RUNTIME  (worker + queue + self-governed actions)
# Pulls goals off a queue, executes them in multiple steps, performs real local +
# external WRITES, and pauses high-risk external writes for operator approval.
# Governance here is the agent's OWN local policy (risk tiers + approval) — there
# is no call to the AWCP control plane; AWCP only observes the agent via its scan.
# ==========================================================================
import uuid as _uuid
from collections import deque as _deque

# --- external write target: env-driven (free public sandbox by default) ---
EXTERNAL_WRITE_URL = os.getenv("AGENT_EXTERNAL_WRITE_URL", "https://httpbin.org/post")
EXTERNAL_WRITE_TOKEN = os.getenv("AGENT_EXTERNAL_WRITE_TOKEN", "")   # optional bearer
APPROVAL_REQUIRED = os.getenv("AGENT_APPROVAL_REQUIRED", "true").lower() == "true"
APPROVAL_TIMEOUT = float(os.getenv("AGENT_APPROVAL_TIMEOUT", "180"))
# Deterministic "finalize" so the runtime ALWAYS routes its output through the
# control plane, even if a weak local model fails to call the write tools itself.
# Both env-tunable (nothing hardcoded): persist the result locally (on by
# default), and optionally also report it externally (off by default, since that
# triggers the high-risk approval flow on every task).
FINALIZE_ARTIFACT = os.getenv("AGENT_FINALIZE_ARTIFACT", "true").lower() == "true"
FINALIZE_EXTERNAL = os.getenv("AGENT_FINALIZE_EXTERNAL", "false").lower() == "true"
ARTIFACT_DIR = ""   # set by mount() to <agent_dir>/artifacts

# --- in-memory task store: queue + index (one worker processes one at a time) ---
TASKS: dict = {}
_QUEUE: _deque = _deque()
_TLOCK = threading.Lock()
_CURRENT: dict = {"task": None}          # the task currently being executed
_APPROVAL_EVENTS: dict = {}              # task_id -> threading.Event
_APPROVAL_DECISION: dict = {}            # task_id -> "approve" | "deny"


def _now() -> float:
    return time.time()


def submit_task(goal: str) -> dict:
    tid = "task-" + _uuid.uuid4().hex[:10]
    task = {"id": tid, "goal": goal, "status": "queued", "steps": [],
            "result": "", "tools_used": [], "awaiting": None,
            "created": _now(), "started": None, "finished": None, "error": ""}
    with _TLOCK:
        TASKS[tid] = task
        _QUEUE.append(tid)
    return _public_task(task)


def _public_task(t: dict) -> dict:
    return {k: t[k] for k in ("id", "goal", "status", "steps", "result",
                              "tools_used", "awaiting", "created", "started",
                              "finished", "error")}


def list_tasks() -> list:
    with _TLOCK:
        return sorted((_public_task(t) for t in TASKS.values()),
                      key=lambda t: t["created"], reverse=True)


def get_task(tid: str):
    t = TASKS.get(tid)
    return _public_task(t) if t else None


def _add_step(task, step) -> None:
    step.setdefault("ts", _now())
    task["steps"].append(step)


def approve_task(tid: str, decision: str) -> bool:
    ev = _APPROVAL_EVENTS.get(tid)
    if not ev:
        return False
    _APPROVAL_DECISION[tid] = "approve" if decision == "approve" else "deny"
    ev.set()
    return True


def governed_action(name: str, risk: str, do_fn, detail: str = ""):
    """Run a state-changing action under the agent's OWN local policy:
      1. if HIGH risk, PARK the current task and wait for operator approval;
      2. execute the write.
    Records a step on the current task (so the UI shows the trace). No AWCP call
    is made — this is self-governance; the radar only observes the process."""
    task = _CURRENT["task"]
    step = {"action": name, "risk": risk, "status": "", "info": ""}

    if risk == "high" and APPROVAL_REQUIRED and task is not None:
        ev = threading.Event()
        _APPROVAL_EVENTS[task["id"]] = ev
        _APPROVAL_DECISION.pop(task["id"], None)
        task["awaiting"] = {"action": name, "detail": detail}
        task["status"] = "awaiting_approval"
        _add_step(task, {**step, "status": "awaiting_approval", "info": detail})
        got = ev.wait(timeout=APPROVAL_TIMEOUT)
        _APPROVAL_EVENTS.pop(task["id"], None)
        task["awaiting"] = None
        task["status"] = "running"
        if not got or _APPROVAL_DECISION.get(task["id"]) != "approve":
            _add_step(task, {**step, "status": "denied",
                             "info": "operator denied" if got else "approval timed out"})
            return f"DENIED: external write '{name}' was not approved."

    try:
        out = do_fn()
        if task:
            _add_step(task, {**step, "status": "done", "info": str(out)[:300]})
        return out
    except Exception as e:  # noqa: BLE001
        if task:
            _add_step(task, {**step, "status": "failed", "info": str(e)})
        return f"ERROR: {e}"


def save_artifact(name: str, content: str) -> str:
    """Governed LOCAL write (medium risk): persist a result artifact to disk."""
    def _do():
        d = ARTIFACT_DIR or os.path.join(os.getcwd(), "artifacts")
        os.makedirs(d, exist_ok=True)
        safe = "".join(c for c in name if c.isalnum() or c in "-_.") or "artifact"
        path = os.path.join(d, f"{int(_now())}-{safe}")
        with open(path, "w") as f:
            f.write(content)
        return f"saved artifact: {path}"
    return governed_action("save_artifact", "medium", _do, detail=name)


def external_post(summary: str) -> str:
    """Self-governed EXTERNAL write (HIGH risk): POST a result to an external
    system. The endpoint is env-driven (AGENT_EXTERNAL_WRITE_URL); the outbound
    call only happens after an operator approves (agent-local approval)."""
    def _do():
        body = json.dumps({"agent": AGENT_NAME, "summary": summary}).encode()
        headers = {"content-type": "application/json"}
        if EXTERNAL_WRITE_TOKEN:
            headers["authorization"] = f"Bearer {EXTERNAL_WRITE_TOKEN}"
        req = urllib.request.Request(EXTERNAL_WRITE_URL, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            return f"external POST {EXTERNAL_WRITE_URL} -> HTTP {r.status}"
    return governed_action("external_post", "high", _do, detail=f"POST {EXTERNAL_WRITE_URL}")


def _worker_loop(run_goal) -> None:
    while True:
        tid = None
        with _TLOCK:
            if _QUEUE:
                tid = _QUEUE.popleft()
        if not tid:
            time.sleep(0.4)
            continue
        task = TASKS.get(tid)
        if not task:
            continue
        _CURRENT["task"] = task
        task["status"] = "running"
        task["started"] = _now()
        try:
            out = run_goal(task["goal"]) or {}
            result = str(out.get("result", ""))
            task["result"] = result
            task["tools_used"] = out.get("tools_used", [])
            # deterministic finalize — route the runtime's output through the gate
            # even if the model didn't call the write tools on its own.
            if FINALIZE_ARTIFACT and not any(s["action"] == "save_artifact" for s in task["steps"]):
                save_artifact("result", result or task["goal"])
            if FINALIZE_EXTERNAL and not any(s["action"] == "external_post" for s in task["steps"]):
                external_post((result or task["goal"])[:500])
            blocked = any(s.get("status") in ("blocked", "denied") for s in task["steps"])
            task["status"] = "blocked" if blocked else "done"
        except Exception as e:  # noqa: BLE001
            task["status"] = "failed"
            task["error"] = str(e)
        finally:
            task["finished"] = _now()
            _CURRENT["task"] = None


# Request bodies for the worker routes. These MUST be module-level: this file
# uses `from __future__ import annotations`, so FastAPI resolves the body type by
# name against the module globals — a class defined inside mount() would not be
# found and FastAPI would mis-read the body as a query param.
from pydantic import BaseModel as _BaseModel  # noqa: E402


class GoalReq(_BaseModel):
    goal: str


class ApproveReq(_BaseModel):
    decision: str = "approve"          # approve | deny


def mount(app, *, meta: dict, run_goal) -> None:
    """Turn a FastAPI app into a self-contained task-worker runtime: wire the
    routes, serve the task console, and start the background worker. Sends NOTHING
    to AWCP. `run_goal(goal) -> {"result", "tools_used"}` is the framework hook."""
    global ARTIFACT_DIR, AGENT_NAME
    from fastapi import HTTPException
    from fastapi.responses import HTMLResponse

    ARTIFACT_DIR = os.path.join(meta.get("dir", os.getcwd()), "artifacts")
    AGENT_NAME = meta.get("agent", "agent")

    @app.get("/", response_class=HTMLResponse)
    def _home():
        return TASK_UI_HTML

    @app.get("/info")
    def _info():
        return {**{k: meta[k] for k in meta if k != "dir"},
                "external_url": EXTERNAL_WRITE_URL,
                "approval_required": APPROVAL_REQUIRED}

    @app.get("/health")
    def _health():
        return {"status": "ok", "framework": meta.get("framework")}

    @app.post("/tasks")
    def _submit(req: GoalReq):
        return submit_task(req.goal)

    @app.get("/tasks")
    def _list():
        return list_tasks()

    @app.get("/tasks/{tid}")
    def _get(tid: str):
        t = get_task(tid)
        if not t:
            raise HTTPException(404, "task not found")
        return t

    @app.post("/tasks/{tid}/approve")
    def _approve(tid: str, req: ApproveReq):
        return {"ok": approve_task(tid, req.decision), "decision": req.decision}

    # No AWCP registration — the radar detects this process autonomously.
    threading.Thread(target=_worker_loop, args=(run_goal,),
                     name="awcp-worker", daemon=True).start()


# --------------------------------------------------------------------------
# Task-console UI (served at GET /). Submit a goal, watch governed steps run,
# approve high-risk external writes, read the result/artifact.
# --------------------------------------------------------------------------
TASK_UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AWCP Worker</title>
<style>
:root{--acc:#6366f1;--bg:#0a0e16;--panel:#121826;--panel2:#0d1320;--line:#1f2a3a;--fg:#e6edf3;--mut:#8b97a7;
      --ok:#22c55e;--warn:#f59e0b;--red:#ef4444;--blue:#38bdf8}
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--fg);min-height:100vh;
     background:radial-gradient(1100px 520px at 82% -12%, rgba(99,102,241,.13), transparent), var(--bg)}
header{padding:18px 26px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,var(--panel),transparent);
       display:flex;align-items:center;gap:15px;flex-wrap:wrap}
.logo{width:44px;height:44px;border-radius:13px;background:linear-gradient(135deg,var(--acc),#0ea5e9);display:flex;
      align-items:center;justify-content:center;font-size:23px;flex:none;box-shadow:0 5px 18px rgba(99,102,241,.4)}
.htext h1{font-size:18px;margin:0} .htext .purpose{color:var(--mut);font-size:13px}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto;align-items:center}
.badge{font-size:11px;color:var(--mut);background:var(--panel2);border:1px solid var(--line);padding:4px 10px;border-radius:999px}
.badge.acc{color:#fff;background:var(--acc);border-color:transparent}
.wrap{max-width:880px;margin:0 auto;padding:24px 20px 60px}
.composer{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:0 10px 34px rgba(0,0,0,.34)}
.composer textarea{width:100%;resize:vertical;min-height:58px;background:transparent;color:var(--fg);border:0;outline:none;font:15px/1.55 inherit;padding:6px}
.crow{display:flex;align-items:center;gap:10px;margin-top:6px}
.examples{display:flex;gap:6px;flex-wrap:wrap;flex:1}
.ex{font-size:11.5px;color:var(--mut);background:var(--panel2);border:1px solid var(--line);padding:4px 10px;border-radius:8px;cursor:pointer}
.ex:hover{color:var(--fg);border-color:var(--acc)}
button{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:9px 18px;font-weight:600;cursor:pointer;font-size:14px}
button:disabled{opacity:.5;cursor:default}
button.app{background:var(--ok)} button.deny{background:transparent;border:1px solid var(--red);color:var(--red)}
.tasks{margin-top:22px;display:flex;flex-direction:column;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.chead{padding:13px 16px;display:flex;align-items:center;gap:12px}
.goal{font-weight:600;flex:1}
.pill{font-size:11px;padding:3px 11px;border-radius:999px;font-family:ui-monospace,monospace;display:flex;align-items:center;gap:6px;text-transform:capitalize;white-space:nowrap}
.dot{width:6px;height:6px;border-radius:50%}
.p-queued{background:#1a2332;color:var(--mut)} .p-queued .dot{background:var(--mut)}
.p-running{background:rgba(56,189,248,.14);color:var(--blue)} .p-running .dot{background:var(--blue);animation:pulse 1s infinite}
.p-awaiting_approval{background:rgba(245,158,11,.16);color:var(--warn)} .p-awaiting_approval .dot{background:var(--warn);animation:pulse 1s infinite}
.p-done{background:rgba(34,197,94,.14);color:var(--ok)} .p-done .dot{background:var(--ok)}
.p-failed,.p-blocked{background:rgba(239,68,68,.14);color:var(--red)} .p-failed .dot,.p-blocked .dot{background:var(--red)}
@keyframes pulse{50%{opacity:.32}}
.cbody{padding:2px 16px 14px;border-top:1px solid var(--line)}
.section{margin-top:12px}
.lbl{font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:var(--mut);margin-bottom:6px}
.steps{display:flex;flex-direction:column;gap:5px}
.step{display:flex;gap:9px;align-items:center;font-size:12.5px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 11px}
.step .act{font-family:ui-monospace,monospace}
.rk{font-size:10px;padding:1px 7px;border-radius:6px;margin-left:auto}
.rk-medium{background:rgba(245,158,11,.16);color:var(--warn)} .rk-high{background:rgba(239,68,68,.16);color:var(--red)}
.sstat{font-family:ui-monospace,monospace;font-size:11px}
.sstat.done{color:var(--ok)} .sstat.blocked,.sstat.denied,.sstat.failed{color:var(--red)} .sstat.awaiting_approval{color:var(--warn)}
.chips{display:flex;gap:5px;flex-wrap:wrap}
.chip{font-size:10.5px;background:var(--panel2);border:1px solid var(--line);color:#a5b4fc;padding:2px 8px;border-radius:6px}
.result{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:13px 16px;font-size:14px;overflow-x:auto;line-height:1.65}
.result h2{font-size:16px;margin:10px 0 4px} .result h3{font-size:14px;margin:8px 0 4px} .result h4{font-size:13px;margin:6px 0 3px}
.result a{color:#7dd3fc} .result code{background:#0a0e16;padding:1px 5px;border-radius:4px;font-family:ui-monospace,monospace;font-size:12.5px}
.result pre.cb{background:#070b12;border:1px solid var(--line);border-radius:8px;padding:11px 13px;overflow-x:auto;font-family:ui-monospace,monospace;font-size:12.5px;line-height:1.5;color:#cfe3ff}
.approve{margin-top:12px;background:rgba(245,158,11,.07);border:1px solid #5a3d0c;border-radius:10px;padding:12px;display:flex;gap:10px;align-items:center}
.approve .q{flex:1;font-size:13px}
.empty{color:var(--mut);text-align:center;margin-top:48px}
.foot{color:var(--mut);font-size:11.5px;text-align:center;margin-top:28px}
</style>
</head>
<body>
<header>
  <div class="logo" id="logo">robot</div>
  <div class="htext"><h1 id="name">Worker</h1><div class="purpose" id="purpose"></div></div>
  <div class="badges" id="badges"></div>
</header>
<div class="wrap">
  <div class="composer">
    <textarea id="goal" placeholder="Give the worker a goal..."></textarea>
    <div class="crow"><div class="examples" id="examples"></div><button id="run">Run task &#9656;</button></div>
  </div>
  <div class="tasks" id="tasks"><div class="empty">No tasks yet - give the worker a goal above.</div></div>
  <div class="foot" id="foot"></div>
</div>
<script>
const $=id=>document.getElementById(id);
let FORMAT='markdown';
function esc(s){return (s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));}
function md(t){ t=esc(t);
  t=t.replace(/```([\s\S]*?)```/g,(m,c)=>'<pre class="cb">'+c.replace(/^\n/,'')+'</pre>');
  t=t.replace(/`([^`\n]+)`/g,'<code>$1</code>');
  t=t.replace(/^#{1,2}\s?(.*)$/gm,'<h2>$1</h2>').replace(/^#{3}\s?(.*)$/gm,'<h3>$1</h3>').replace(/^#{4}\s?(.*)$/gm,'<h4>$1</h4>');
  t=t.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
  t=t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank">$1</a>');
  t=t.replace(/(^|\s)(https?:\/\/[^\s<]+)/g,'$1<a href="$2" target="_blank">$2</a>');
  t=t.replace(/^\s*[-*]\s+(.*)$/gm,'&bull; $1');
  t=t.replace(/\n{2,}/g,'<br><br>').replace(/\n/g,'<br>');
  return t;
}
function fmt(text){ if(FORMAT==='json'){ const m=text.match(/\{[\s\S]*\}/);
    if(m){ try{ return '<pre class="cb">'+esc(JSON.stringify(JSON.parse(m[0]),null,2))+'</pre>'; }catch(e){} } }
  return md(text); }
const IC={save_artifact:'\u{1F4BE}',external_post:'\u{1F310}'};
function step(s){ return '<div class="step"><span>'+(IC[s.action]||'⚙')+'</span><span class="act">'+esc(s.action)+
  '</span><span class="rk rk-'+s.risk+'">'+s.risk+'</span><span class="sstat '+s.status+'">'+s.status.replace('_',' ')+'</span></div>'; }
function card(t){
  const steps=(t.steps||[]).length?'<div class="section"><div class="lbl">governed steps</div><div class="steps">'+t.steps.map(step).join('')+'</div></div>':'';
  const chips=(t.tools_used||[]).length?'<div class="section"><div class="lbl">tools used</div><div class="chips">'+t.tools_used.map(n=>'<span class="chip">'+esc(n)+'</span>').join('')+'</div></div>':'';
  const appr=(t.status==='awaiting_approval'&&t.awaiting)?'<div class="approve"><span class="q">&#9888; Approval required: high-risk <b>'+esc(t.awaiting.action)+'</b> &mdash; '+esc(t.awaiting.detail||'')+'</span><button class="app" onclick="approve(\''+t.id+'\',\'approve\')">Approve</button><button class="deny" onclick="approve(\''+t.id+'\',\'deny\')">Deny</button></div>':'';
  const res=t.result?'<div class="section"><div class="lbl">result</div><div class="result">'+fmt(t.result)+'</div></div>':'';
  const err=t.error?'<div class="section"><div class="result" style="color:var(--red)">'+esc(t.error)+'</div></div>':'';
  const body=(appr||res||steps||chips||err)?'<div class="cbody">'+appr+res+steps+chips+err+'</div>':'';
  return '<div class="card"><div class="chead"><span class="goal">'+esc(t.goal)+'</span><span class="pill p-'+t.status+'"><span class="dot"></span>'+t.status.replace('_',' ')+'</span></div>'+body+'</div>';
}
async function refresh(){ let ts=[]; try{ ts=await (await fetch('/tasks')).json(); }catch(e){ return; }
  $('tasks').innerHTML=ts.length?ts.map(card).join(''):'<div class="empty">No tasks yet - give the worker a goal above.</div>'; }
async function run(){ const g=$('goal').value.trim(); if(!g)return; $('goal').value=''; $('run').disabled=true;
  try{ await fetch('/tasks',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({goal:g})}); }catch(e){}
  $('run').disabled=false; refresh(); }
async function approve(id,d){ try{ await fetch('/tasks/'+id+'/approve',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({decision:d})});}catch(e){} refresh(); }
async function init(){ try{ const j=await (await fetch('/info')).json();
  FORMAT=j.format||'markdown';
  if(j.accent) document.documentElement.style.setProperty('--acc',j.accent);
  $('logo').textContent=j.logo||'\u{1F916}'; $('name').textContent=j.agent||'Worker'; document.title=j.agent||'Worker';
  $('purpose').textContent=j.purpose||'';
  $('badges').innerHTML='<span class="badge acc">'+esc(j.framework||'')+'</span><span class="badge">'+esc(j.model||'')+
    '</span><span class="badge" title="This runtime makes no calls to AWCP. If an AWCP radar is running, it discovers this process on its own.">&#10752; independent runtime</span>';
  $('examples').innerHTML=(j.examples||[]).map(e=>'<span class="ex" onclick="document.getElementById(\'goal\').value=this.textContent">'+esc(e)+'</span>').join('');
  $('foot').textContent='ext → '+(j.external_url||'')+(j.approval_required?' · high-risk writes need approval':'');
}catch(e){} }
$('run').onclick=run;
$('goal').addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); run(); }});
init(); refresh(); setInterval(refresh,1500);
</script>
</body>
</html>"""
