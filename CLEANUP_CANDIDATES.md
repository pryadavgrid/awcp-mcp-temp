# Cleanup Candidates — awcp-mcp-temp2

> Scan date: 2026-06-16  
> Nothing has been deleted. Every file listed below still exists exactly where it was found.  
> Organized by reason for flagging. Severity: **High** (safe to remove with no impact) → **Medium** (likely stale, verify first) → **Low** (personal preference / minor).

---

## 1 — Runtime state committed to the repo

These files are produced at runtime and should never live in version control. They capture the live state of a running instance on one machine and have no meaning anywhere else. Neither is listed in `.gitignore`.

| File | Why it's here | Problem |
|---|---|---|
| `agent_radar_registry.json` | Written by the radar on every heartbeat scan — live registry data | Changes on every run; commits pollute git history with machine state. Currently shows as modified in `git status`. Should be in `.gitignore`. |
| `agent_radar_registry.json.freeze.json` | Written when a hard-stop freeze is applied | Content is `{"frozen": {}}` — essentially empty. No value in tracking this. Also missing from `.gitignore`. |

**Recommended action:** Add both to `.gitignore` and remove from tracking with `git rm --cached`.

---

## 2 — Exact duplicate file

| File | Duplicate of | Notes |
|---|---|---|
| `Agent-Workforce-Control-Plane-Magazine.html` (root) | `docs/Agent-Workforce-Control-Plane-Magazine.html` | Confirmed byte-for-byte identical with `diff`. The `docs/` copy is the canonical one; the root copy has no references pointing to it. |

---

## 3 — macOS metadata

| File | Notes |
|---|---|
| `.DS_Store` (root) | macOS Finder metadata. Already listed in `.gitignore` but still committed. Should be removed from tracking. |

---

## 4 — Session handoff / implementation summary documents

These are write-up files generated during development chat sessions — "here is what I just did" summaries. They describe decisions that are already reflected in the code, and have no value as living documentation.

| File | Contents | Severity |
|---|---|---|
| `AWCP_BUILD_CONTEXT.md` | Chat resume/handoff doc ("pick up from any session"). References files by path and describes in-progress state as of a past session. | High |
| `ORCHESTRATION_REFACTOR_SUMMARY.md` | "Mission accomplished" write-up of the orchestration refactoring. The refactoring is done and in the code. | High |
| `RESEARCH_FORMAT_UPDATE.md` | "Problem solved" write-up describing how research result formatting was fixed. 481 lines of now-historical narrative. | High |
| `docs/DYNAMIC_TOOL_SELECTION.md` | Implementation notes for when dynamic tool selection was added. The feature exists in the code; the doc adds nothing new. | High |
| `docs/REFACTORING_COMPARISON.md` | Before/after comparison from the orchestration refactor. Historical, not reference material. | High |
| `docs/RESEARCH_RESULTS_FORMAT.md` | Describes a formatting fix. Mirrors `RESEARCH_FORMAT_UPDATE.md` in the root — same topic, different write-up. | High |
| `docs/RESPONSE_FORMAT_GUIDE.md` | Documents response formatting decisions made in a past session. | High |
| `docs/Step_By_Step.md` | **Accidental commit.** Content is raw Claude session output: `"Viewed mcp_gateway.py:1-257 / Viewed base_workflow.py:1-64 / Listed directory tools …"` — not a document. | High |

---

## 5 — Scripts consolidated into `run_everything.sh` ✅ done

Resolved. `scripts/run_everything.sh` is now the **single launcher** (whole platform behind the gateway on `:8000`, workers in-process). The previous all-in-one runners (`run_all.sh`, `run_awcp.sh`) and the single-component scripts (`run_radar.sh`, `run_worker.sh`, `run_control.sh`, `start_mcp.sh`, `start_server.sh`) have been **removed**. Docs/UI references were updated to `run_everything.sh`.

`scripts/clean_cache.sh` (finds and removes `__pycache__` dirs) is kept as a small utility.

---

## 6 — Misplaced file

| File | Problem |
|---|---|
| `docs/ollama_service.py` | A Python source file sitting in the `docs/` directory. It is not imported by anything in the project (`grep` found zero references). Not served, not tested, not connected to any module. Wrong location regardless of whether the code itself is useful. |

---

## 7 — Unreferenced or stale HTML artifacts

| File | Status | Notes |
|---|---|---|
| `Agent-Registry-Upgrade.html` (root) | No references | An agent registry upgrade guide HTML. Not linked from README, not linked from any other doc. No code references it. |
| `docs/demo_ui.html` | No references | 729-line HTML prototype UI titled "Agent Ops Control Plane." Not served by any route, not linked anywhere. Appears to be a design mockup from an earlier iteration. |
| `docs/AWCP_Architecture_Flow.html` | Referenced only by `AWCP_BUILD_CONTEXT.md` | `AWCP_BUILD_CONTEXT.md` is itself a stale handoff file (section 4 above). The architecture is now more accurately covered by `docs/AWCP_Magazine_vs_temp2.html`. |

---

## 8 — Docs that may be stale but have some reference

These are lower priority — they have at least one reference or some remaining utility but are worth reviewing.

| File | Reference | Notes |
|---|---|---|
| `docs/AWCP_Implementation_Guide.html` | Linked from `README.md` line 381 | README calls it the implementation guide. The content describes the pre-Temporal, pre-Laminar architecture. May be outdated relative to the current codebase. |
| `docs/EXECUTION_FLOW_DIAGRAM.md` | No external references | ASCII flow diagram of the orchestration. Describes an older execution flow. The current flow is in `docs/AWCP_Magazine_vs_temp2.html`. |
| `docs/agent_registry.md` | No external references | 79-line architectural doc for the registry. Describes the registry model from before the Temporal onboarding pipeline existed. |
| `docs/AGENT_RELOAD_GUIDE.md` | No external references | 177-line guide explaining why `uvicorn --reload` doesn't pick up new agent files and what to do instead. Operationally useful, but could be a README note instead of a full file. |

---

## Summary counts

| Category | Count |
|---|---|
| Runtime state files to gitignore | 2 |
| Exact duplicates | 1 |
| macOS metadata | 1 |
| Session summary / handoff docs | 8 |
| Superseded scripts | 6 (+1 low-priority) |
| Misplaced file | 1 |
| Unreferenced HTML artifacts | 3 |
| Stale but referenced docs | 4 |
| **Total flagged** | **26** |

Active source files in `src/`, `tests/`, `observability/`, `scripts/run_everything.sh`, `requirements.txt`, `README.md`, and all files under `docs/Agent-Workforce-Control-Plane-Magazine.html` + `docs/AWCP_Magazine_vs_temp2.html` are **not flagged** — they are in active use.
