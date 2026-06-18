"""AWCP Registry server — the `POST /v1/agents` API + a tiny onboarding panel.

Stdlib only (same spirit as control_panel.py). Run:

    python3 server.py            # → http://localhost:8090
    AWCP_REGISTRY_PORT=9000 python3 server.py

API
    GET  /v1/agents                     list registry entries
    POST /v1/agents                     register  {card, awcp}      (push path)
    POST /v1/agents/fetch               operator  {url}             (operator path)
    GET  /v1/agents/{id}                one entry
    POST /v1/agents/{id}/approve        {granted_scopes?}
    POST /v1/agents/{id}/deny
    POST /v1/agents/{id}/heartbeat
    POST /v1/agents/{id}/execute        {scope}  → enforcement gate result
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from registry import REGISTRY

PORT = int(os.getenv("AWCP_REGISTRY_PORT", "8090"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):       # quiet
        pass

    # ── helpers ──────────────────────────────────────────────────────────
    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    # ── routes ───────────────────────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, UI_HTML.encode(), "text/html; charset=utf-8")
        if path == "/v1/agents":
            return self._json(REGISTRY.list())
        m = re.fullmatch(r"/v1/agents/([^/]+)", path)
        if m:
            e = REGISTRY.get(m.group(1))
            return self._json(e.view() if e else {"error": "not found"}, 200 if e else 404)
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        body = self._body()

        if path == "/v1/agents":
            return self._json(REGISTRY.register(body))
        if path == "/v1/agents/fetch":
            return self._json(REGISTRY.register_from_url(
                body.get("url", ""), owner=body.get("owner", "operator")))

        m = re.fullmatch(r"/v1/agents/([^/]+)/(approve|deny|heartbeat|execute)", path)
        if m:
            eid, action = m.group(1), m.group(2)
            if action == "approve":
                return self._json(REGISTRY.approve(eid, body.get("granted_scopes")))
            if action == "deny":
                return self._json(REGISTRY.deny(eid))
            if action == "heartbeat":
                return self._json(REGISTRY.heartbeat(eid))
            if action == "execute":
                ok, reason = REGISTRY.can_execute(eid, body.get("scope", ""))
                return self._json({"allowed": ok, "reason": reason})
        self._json({"error": "not found"}, 404)


UI_HTML = r"""<!doctype html><html><head><meta charset="utf-8"/>
<title>AWCP Registry</title><style>
:root{--bg:#0b0f17;--panel:#121826;--line:#1f2937;--fg:#e5e7eb;--mut:#9ca3af;--acc:#6366f1;--ok:#22c55e;--warn:#f59e0b;--red:#ef4444}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:16px 22px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-size:16px;margin:0}.sub{color:var(--mut);font-size:12px}
.wrap{max-width:900px;margin:22px auto;padding:0 18px;display:flex;flex-direction:column;gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.row{display:flex;align-items:center;gap:12px}.name{font-weight:600}.spacer{margin-left:auto}
.badge{font-size:11px;padding:3px 9px;border-radius:20px;border:1px solid var(--line)}
.s-quarantined{color:var(--warn);border-color:var(--warn)}.s-approved{color:var(--ok);border-color:var(--ok)}.s-denied{color:var(--red);border-color:var(--red)}
.meta{color:var(--mut);font-size:12px;margin-top:6px}
button{border:0;border-radius:8px;padding:7px 13px;font-weight:600;cursor:pointer;color:#fff;background:var(--acc)}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--mut)}
input{background:#0b0f17;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:7px 10px}
.exec{margin-top:10px;display:flex;gap:8px;align-items:center}.out{font-size:12px;color:var(--mut)}
code{color:#a5b4fc}
</style></head><body>
<header><h1>AWCP Registry · onboarding panel</h1>
<div class="sub">A2A Agent Card (schema) + AWCP governance · quarantine → approve → enforce</div></header>
<div class="wrap">
  <div class="card"><div class="row">
    <input id="url" placeholder="operator: agent base URL (fetches /.well-known/agent-card.json)" style="flex:1"/>
    <button onclick="fetchUrl()">Register by URL</button>
  </div></div>
  <div id="list"></div>
</div>
<script>
async function api(p,m='GET',b){const o={method:m};if(b){o.headers={'content-type':'application/json'};o.body=JSON.stringify(b)}return (await fetch(p,o)).json()}
function entry(a){
  return `<div class="card">
    <div class="row"><span class="name">${a.name}</span>
      <span class="badge s-${a.status}">${a.status}</span>
      <span class="badge">${a.active?'active':'inactive'}</span>
      <span class="badge">${a.verified?'verified':'unverified'}</span>
      <span class="spacer"></span>
      <button onclick="act('${a.id}','approve')">Approve</button>
      <button class="ghost" onclick="act('${a.id}','deny')">Deny</button>
      <button class="ghost" onclick="act('${a.id}','heartbeat')">Heartbeat</button>
    </div>
    <div class="meta">owner: <code>${a.owner||'—'}</code> · risk: <code>${a.risk}</code> · intake: <code>${a.intake}</code>
      · requested: <code>${(a.requested_write_scopes||[]).join(', ')||'—'}</code>
      · granted: <code>${(a.granted_scopes||[]).join(', ')||'—'}</code></div>
    <div class="exec">
      <input id="sc-${a.id}" placeholder="scope e.g. external_post"/>
      <button class="ghost" onclick="exec('${a.id}')">Test execute</button>
      <span class="out" id="out-${a.id}"></span>
    </div>
  </div>`;
}
async function refresh(){document.getElementById('list').innerHTML=(await api('/v1/agents')).map(entry).join('')||'<div class="card">No agents yet. Run demo.py or POST /v1/agents.</div>'}
async function act(id,what){await api(`/v1/agents/${id}/${what}`,'POST',{});refresh()}
async function exec(id){const sc=document.getElementById('sc-'+id).value;const r=await api(`/v1/agents/${id}/execute`,'POST',{scope:sc});document.getElementById('out-'+id).textContent=(r.allowed?'✅ ':'⛔ ')+r.reason}
async function fetchUrl(){await api('/v1/agents/fetch','POST',{url:document.getElementById('url').value});refresh()}
refresh();setInterval(refresh,3000);
</script></body></html>"""


if __name__ == "__main__":
    print(f"🛂  AWCP Registry  →  http://localhost:{PORT}")
    print(f"    token auth: {'ON' if os.getenv('AWCP_REGISTRY_TOKEN') else 'off (agents register as unverified)'}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
