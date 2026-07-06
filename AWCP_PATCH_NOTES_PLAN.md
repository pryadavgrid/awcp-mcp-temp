# Patch‑Notes Feature — Plan

> **In one line.** This feature writes **patch notes** for the project the agents are working on
> — the code that sits in the sandbox **`workspace/`** folder (the only place AWCP agents can
> read, write, or run files). A patch note is a short, reviewable document: what's wrong, why,
> the suggested change, the risk, and which files it touches. AWCP's job is to **run and record
> the patch process**. The evidence for the bug comes from **the target project itself**, not
> from AWCP's own internal records.

---

## 1. The one rule: keep two things separate

The design keeps two different questions apart.

| | Bug evidence | Process record |
|---|---|---|
| **Question** | Why does the target code break? | Who proposed, approved, and applied the patch — and was it governed? |
| **Where it comes from** | The target project in `workspace/`: its failing tests, errors, and run output. These show up in **`ops.sandbox_events`** when an agent runs or tests the code with the `run_command` tool. Plus the issue the operator reports. | AWCP's own records: the **`evidence.ledger`**, the **approvals** table, and tool risk tiers. |
| **Belongs to** | The target project. | AWCP. |

AWCP's `evidence.ledger` is a record of **what the agent did** — it is not a debugger for some
other project. So it's used only for the right‑hand column (running the patch process). The bug
evidence is read from the target project's own runs in the sandbox. The two stay separate.

---

## 2. Where the target project lives

AWCP agents can only touch files **inside the sandbox container**. That container mounts one
host folder — `AWCP_WORKSPACE_DIR` (default `<repo>/workspace`) — at `/workspace`, and it can
see nothing else. So:

* **The target project is whatever the operator puts into `workspace/`.** There's no separate
  "project registry"; the workspace folder is the agreement.
* All reading, testing, patch‑writing, and validation happen **inside that folder**, through the
  normal governed tools — never against AWCP's own code in `src/awcp/`.

---

## 3. Principles

1. **Govern everything.** Nothing outside `workspace/` is ever written. AWCP never patches itself.
2. **Bug evidence from the target; process record from AWCP.** (See §1.)
3. **Understand the repo dynamically.** Build an index of the workspace project by reading its
   code (AST). No hardcoded "this issue means that file" mappings.
4. **Reuse the existing tool path.** One place runs every tool:
   the MCP server's `execute_tool` (`mcp/server.py`). For each call it works out the tool's risk
   (`get_tool_risk`), treats `medium`/`high`/`critical` as a write that must be gated
   (`is_write_risk`), asks the gate (`_radar_gate` → `opa.evaluate_action` + operator policy),
   then either blocks or runs it and records a checkpoint. So if the patch feature sends its
   `write_file` / `run_command` calls **through the MCP server**, it gets the risk tiers, the
   gate, and the audit trail for free. (Calling the plain in‑process runtime instead skips the
   gate, so the workflow must use the MCP‑server path.)
5. **Keep a human in the loop** using the existing approvals (`governance.write_approvals` + the
   Approvals page).
6. **Postgres first, fail open** — like the rest of the system.

---

## 4. Where the bug evidence comes from

* **`ops.sandbox_events`** — a log of every sandbox `run_command` / `read_file` / `write_file`.
  It's written to Postgres by `runtime/sandbox_db.py`, merged with an in‑memory copy, and served
  by the `/sandbox/events` endpoint on the MCP server. Each entry holds the command plus a short
  outcome/error summary — enough to tell you that a `pytest` or `py_compile` run on the target
  failed. The **full** error text isn't stored in the event (a tool returns its full output to
  the caller), so the workflow gets that by running the tests itself (next point).
* **A governed test run.** If there's no failure on record yet, the workflow runs the target's
  tests or build with a `run_command`. That produces a real failure and logs it to
  `ops.sandbox_events` — fresh evidence instead of stale logs.
* **The operator's input** — the reported issue and the desired outcome.
* **Token info (context only, not bug evidence).** Laminar's token usage and `trace_url` give
  the reviewer a sense of LLM cost during the run. They don't explain the target bug.

`evidence.py` gathers all of this into one `PatchEvidence` object per run.

---

## 5. Where the code goes (follows current conventions)

```text
src/awcp/patching/                 # NEW
├── __init__.py
├── models.py            # PatchRequest, RepoIndex, PatchEvidence, PatchPlan, PatchNotes, PatchRun
├── repo_index.py        # read the WORKSPACE project's code -> a map of files/functions
├── evidence.py          # gather bug evidence from ops.sandbox_events + the operator report (§4)
├── analyze.py           # turn issue + evidence into a list of likely files (query the index)
├── notes.py             # write the patch-notes markdown
├── db.py                # ops.patch_runs / ops.repo_index (fail-open), like radar/db.py
├── api.py               # APIRouter: POST /patch, GET /patch[/id], /decide, /notes
└── (optional) gen.py, validate.py, apply.py   # Phases G/H/I — thin wrappers over sandbox tools

src/awcp/radar/temporal/           # EXISTING folders, NEW files
├── workflows/patching.py     # @workflow.defn PatchNotesWorkflow
└── activities/patching.py    # @activity.defn collect_evidence / analyze_repo / plan / render_notes

ui/src/pages/Patches.jsx           # NEW — list runs, view notes + diff, approve/deny
observability/init-db/02-schema.sql   # + ops.patch_runs, ops.repo_index
```

The gateway already mounts radar's `APIRouter` and starts the Temporal workers in‑process. The
new patching router and workflow plug in the same way.

---

## 6. The steps

### A — Index the project
Read the workspace project's code (AST) into a map of files, classes, functions, imports, and
docstrings. Save it to `ops.repo_index` (fail open), refresh on demand. This is how files get
found — no hardcoded routing.

### B — Collect evidence
Build `PatchEvidence` from `ops.sandbox_events` and the operator's report. If needed, run the
target's tests with a `run_command` to get a real failure (§4).

### C — Find the likely files
Search the index using clues from the evidence (function names, error references, imports) to
get `{ affected_files, confidence }`. Confidence is shown to the human — it never decides on its
own.

### D — Draft the plan (LLM, no code)
A local Ollama model (the default elsewhere in AWCP, metered through the token gateway) writes
the **root cause, impact, suggested changes, and a first risk guess** — not code.

### E — Write the patch notes  ⟵ **this is the MVP**
Produce a readable markdown note: the issue, root cause, affected files, suggested changes,
evidence from `ops.sandbox_events`, and the risk level. Save the markdown on the `ops.patch_runs`
row and show it on the Patches page. *(Note: `ops.artifacts` only stores a pointer to a blob
— `id / kind / storage_ref / bytes` — not the blob itself, so use it only if you store the file
somewhere else and want to track it.)* **The MVP stops here.**

### F — Approval
For anything beyond notes (and for `medium`/`high` risk when the operator wants it), add a
`governance.write_approvals` row (`create_write_approval`) so it shows up on the **Approvals**
page; `decide_write_approval` releases it. You can also attach an expiring `approval_token`.

### G *(optional)* — Generate the patch
Make a diff and the patched files, and write them with `write_file` **through the MCP server's
`execute_tool`** into `workspace/` only. Because that runs through the gate, the write is
risk‑checked and recorded like any other governed write (`write_file` is `medium`, a gated tier).

### H *(optional)* — Validate (in place)
The target project is already in the folder, so checks run directly with `run_command`:
`python -m py_compile` (always), `pytest` (when there are tests), and `ruff` / `mypy` **only if
the target sets them up**. Report each check as `pass` / `fail` / `skipped (not configured)`.

### I *(optional)* — Second approval, then apply to a branch
Show the reviewer the notes, diff, validation, and risk together. On approval, apply to a **new
branch in the target project** (`patch/<id>`) using git through `run_command` — never the
target's `main`, never automatically, and never AWCP's own code.

---

## 7. Risk (uses the existing tool gate)

A patch is just governed tool calls run through the MCP server's `execute_tool`, which already
tiers and gates them:

* `write_file` and `run_command` are set to risk **`medium`** (changeable with
  `AWCP_SANDBOX_WRITE_RISK` / `AWCP_SANDBOX_RUN_RISK`). `medium` is in the gated write set
  (`AWCP_WRITE_RISK_TIERS`, default `medium,high,critical`), so both go through the gate.
  `read_file` is `low` and never gated. Risk is resolved as: env override → the tool's declared
  value → `low`, so operators can retune it without touching code.
* The gate's answer comes from `opa.evaluate_action` plus the operator‑policy gate. It has four
  outcomes: `auto_authorized`, `awaiting_token`, `awaiting_operator`, `denied`. The MCP path runs
  the tool when it's `auto_authorized` and blocks it when it's `denied`. An `awaiting_*` answer
  means "needs approval" — that goes through the normal approval flow (§F), and the Temporal
  workflow waits for it. It is never auto‑granted.
* **tiktoken** (`laminar/estimator.py`) measures the context/patch size for the risk field.

So notes (read‑only) need no write gate. The moment a patch wants to write or run, it's gated,
and any approval goes through the existing `write_approvals` flow. The thresholds live in
operator policy / env, not hardcoded in the code.

---

## 8. Audit (recording the process)

Every step of the patch workflow writes a checkpoint to the hash‑chained `evidence.ledger`
(checkable with `verify_chain`): who started the run, what was proposed, who approved, and what
was applied. The MCP server **already** writes a checkpoint for every governed tool call
(including blocked ones), so the patch's `write_file` / `run_command` steps are recorded for
free — the workflow only needs to add checkpoints for the non‑tool steps (plan, notes, approval).
This records the **process**, which is separate from the target's bug evidence (§1).

---

## 9. Storage (Postgres first, fail open)

Add to `observability/init-db/02-schema.sql`:
* `ops.repo_index` — the latest project map (jsonb) + when it was generated.
* `ops.patch_runs` — run id, status, issue, evidence reference, plan, notes, risk level, approval
  id, validation report, who/when (append‑only).

Keep the notes markdown and the diff text **on the `ops.patch_runs` row**. Read evidence from
`ops.sandbox_events`. `ops.artifacts` only stores a pointer (`storage_ref` + `bytes`), so use it
only if you keep the file elsewhere. `patching/db.py` follows `radar/db.py` (self‑migrating with
`ADD COLUMN IF NOT EXISTS`, falls back to memory when there's no DB).

---

## 10. How it's exposed

* **API** (`patching/api.py`, mounted by the gateway): `POST /patch` to start a run on the
  workspace project, `GET /patch`, `GET /patch/{id}`, `POST /patch/{id}/decide`,
  `GET /patch/{id}/notes`.
* **UI** (`ui/src/pages/Patches.jsx`): list runs, show the notes markdown and the diff, show the
  `ops.sandbox_events` evidence, and approve/deny with the same controls as the Approvals page.

---

## 11. What "done" looks like

* AWCP never edits its own `src/awcp/`; the MVP only writes notes for the workspace project.
* Bug evidence comes from the **target** (`ops.sandbox_events` + operator report) — never from
  AWCP's own ledger.
* `evidence.ledger` and approvals are used **only** to run and record the patch process.
* Files are found dynamically from the project index — no hardcoded routing.
* Generate / validate / apply are just the **existing governed tools** (already tiered and gated).
* If apply is built: a **new branch in the target project only**, never `main`, never automatic.

---

## 12. What already exists vs what's new

**Already in the repo (verified):** the OpenSandbox + `workspace/` mount (`runtime/sandbox.py`);
the `read_file` / `write_file` / `run_command` tools, with risk set on the `@tool` wrappers in
`tools/sandbox_tools.py` (the actual work is in `runtime/sandbox.py`); the **gate that runs on
every tool call** in the MCP server's `execute_tool` (`mcp/server.py`: `get_tool_risk` →
`is_write_risk` → `_radar_gate` → `opa.evaluate_action` + operator policy); `ops.sandbox_events`
(`runtime/sandbox_db.py` + the `/sandbox/events` endpoint); `evidence.ledger` with
`record_checkpoint` / `verify_chain` (`context_graph`); `governance.write_approvals` with
`create_write_approval` / `decide_write_approval` and the Approvals page;
`governance.approval_tokens`; `operator_policy` and OPA tool tiers; tiktoken
(`laminar/estimator.py`); the Temporal workers the gateway starts over
`radar/temporal/{workflows,activities}`; `ops.artifacts` (a pointer table); the gateway's
`APIRouter` mounting (`gateway/app.py`).

**New (to build):** the `src/awcp/patching/` package; the project index over the **workspace**
project; the evidence gatherer (`ops.sandbox_events` + operator report); the notes writer; the
`ops.patch_runs` / `ops.repo_index` tables; `temporal/{workflows,activities}/patching.py`; the
`Patches.jsx` page.

---

## 13. Scope: start small

The **MVP is just the notes** (Steps A–F): governed, evidence‑backed patch notes for the
workspace project. Steps G–I (generate → validate → apply to a branch) are optional and add no
new governance — they're thin wrappers over the already‑governed `write_file` / `run_command`
tools, and they only ever touch a new branch in the target project.
