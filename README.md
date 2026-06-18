# AWCP Agent Runtimes

A collection of **self-contained, self-governed AI agent runtimes** built on the
AWCP *"agent-on-an-existing-runtime"* model. Each agent is a long-lived **FastAPI
HTTP service** with its own task queue, worker, governance gate, and drop-in web
console — designed so a process-scanning registry (`agent_radar`) can
**auto-detect** it by reading the framework import in its `agent_runtime.py`.

Every agent runs **free and fully local** (local [Ollama](https://ollama.com)
models, no API keys) and is governed the same way: read tools run freely, while
**writes are gated** — local writes are medium-risk, external writes are
high-risk and pause for operator approval.

> The headline agent on this branch (`DS_Prateek`) is the **File Inspector** —
> documented in detail below. The other agents are summarised briefly.

---

## Repository at a glance

| Agent | Framework | Port | What it does |
|---|---|---|---|
| **⭐ [File Inspector](fileinspector_agent/)** | LangGraph | `8104` | **Identify any file (text, image, PDF, JSON, .docx, code, binary) and explain what's inside it.** |
| [LangGraph Orchestrator](langgraph_agent/) | LangGraph | `8100` | General research & compute orchestrator — multi-step web + math, then a written answer. |
| [CrewAI Writer](crewai_agent/) | CrewAI | `8101` | Content & report writer — researches a topic, then drafts a structured write-up. |
| [PydanticAI Extractor](pydanticai_agent/) | PydanticAI | `8102` | Structured-data extractor — returns clean, validated JSON for any query. |
| [arXiv Research Worker](arxiv_agent/) | LangGraph | `8103` | Academic research — finds arXiv papers and reports findings with citations. |

Plus shared infrastructure: the [`control_panel.py`](control_panel.py)
(start/stop every agent from one UI, port `8099`), the per-agent
[`awcp_kit.py`](fileinspector_agent/awcp_kit.py) (queue + governance + console +
radar streaming), and the [`A2A/`](A2A/) onboarding prototype.

---

## ⭐ File Inspector Agent

A **free, fully-working file-inspection agent**. Give it **any file** — text,
image, PDF, JSON, `.docx`, CSV, source code, or an unknown binary — and it
returns a **brief, plain-language summary of what's inside** (no technical
metadata — just the contents).

- **LangGraph** runtime over **local Ollama** models — a deterministic
  read/extract step plus a short, grounded summary. No API keys.
- **Multimodal:** images are described by a local Ollama **vision** model.
- Strong free defaults: **`qwen2.5:7b`** for reading/summarising documents and
  text, **`qwen2.5vl:7b`** (Qwen2.5-VL) for image captioning. Both
  env-overridable.
- Real, deterministic file tools: `classify_file`, `read_document`,
  `describe_image` — plus governed `save_artifact` and `external_post`.
- A persistent service with a **drag-and-drop upload box**.

### Quick start

```bash
cd fileinspector_agent
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# needs Ollama with these models (free, local):
ollama pull qwen2.5:7b      # reading / summarising documents & text
ollama pull qwen2.5vl:7b    # image captioning

./run.sh                    # → http://localhost:8104
```

Open <http://localhost:8104>, **drop a file** (or click *Attach file*),
optionally type what you want to know, and it replies with a short summary of
what the file contains.

From the shell:

```bash
# upload a file, then inspect it via a goal that carries its local path
P=$(curl -s -F "file=@/etc/hosts" localhost:8104/upload \
     | python3 -c 'import sys,json;print(json.load(sys.stdin)["path"])')
curl -s -XPOST localhost:8104/tasks -H 'Content-Type: application/json' \
  -d "{\"goal\":\"Identify this file and summarise it.\n\nFILE_PATH: $P\"}"
```

### How it works

1. The upload box (or a hand-typed path) puts a `FILE_PATH:` into the **goal**.
2. The background worker classifies the file deterministically (`classify_file`),
   then either **reads** it (`read_document` for PDF/DOCX/text/code) or
   **describes** it (`describe_image` for images via the vision model).
3. The extracted content is summarised by the text model into a **brief
   plain-language description** — no metadata, just the contents.
4. The result is persisted through the agent's **self-governed write path**
   (`save_artifact` = medium-risk local write; `external_post` = high-risk
   external write that pauses for approval). Files land in `artifacts/`.
5. If the summary model is unavailable, a **fallback** shows the file's
   extracted content directly so you still see what's inside.

### Supported inputs

- **Images:** `jpg / png / webp / bmp / gif / tiff`
- **Documents:** PDF, Word `.docx`
- **Text / code / data:** `txt / md / json / csv / tsv / py / js / ts / html /
  xml / yaml / toml / ini / sql / sh / ...`
- **Anything else** is sniffed by **magic bytes** and reported as a typed binary.

### Endpoints

`GET /` (console UI) · `POST /upload` (file intake) · `POST /tasks {goal}` ·
`GET /tasks` · `GET /tasks/{id}` · `POST /tasks/{id}/approve` · `GET /info` ·
`GET /health`

### Configuration (env — nothing hardcoded)

| Variable | Default | Purpose |
|---|---|---|
| `FILE_MODEL` | `qwen2.5:7b` | Ollama model for reading/summarising text (bump to `qwen2.5:14b` / `llama3.3:70b` for higher quality). |
| `VISION_MODEL` | `qwen2.5vl:7b` | Ollama model for image captioning (alt: `llama3.2-vision`, `minicpm-v`, `llava`). |
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama endpoint. |
| `FILE_PORT` | `8104` | Service port. |
| `FILE_MAX_CHARS` | `40000` | Extracted-text cap. |
| `FILE_MODEL_CHARS` | `16000` | How much extracted text is fed to the model. |
| `AGENT_EXTERNAL_WRITE_URL` / `AGENT_APPROVAL_REQUIRED` / `AGENT_FINALIZE_EXTERNAL` | — | Shared governance knobs (see `awcp_kit.py`). |

### How `agent_radar` detects it

Launched as `python <absolute>/agent_runtime.py`, which imports `langgraph` at the
top. The radar's scanner reads the referenced script, sees the `from langgraph...`
import, and registers it as `kind=agent_framework, framework=langgraph`
(`detected_via=script_import`) — exactly like the other LangGraph runtimes here.

For full detail, see [fileinspector_agent/README.md](fileinspector_agent/README.md).

---

## The other agents (brief)

Each follows the same pattern as the File Inspector — a governed FastAPI worker
runtime with a web console — differing only in framework and task. See each
folder's own `README.md` for details.

- **[LangGraph Orchestrator](langgraph_agent/)** (`8100`, LangGraph) — a general
  research & compute orchestrator: multi-step web search + math, then a clear
  written answer.
- **[CrewAI Writer](crewai_agent/)** (`8101`, CrewAI) — researches a topic and
  drafts a structured, headed write-up.
- **[PydanticAI Extractor](pydanticai_agent/)** (`8102`, PydanticAI) — returns
  clean, schema-validated JSON for any query.
- **[arXiv Research Worker](arxiv_agent/)** (`8103`, LangGraph) — finds arXiv
  papers and reports findings with citations and links.

---

## Shared infrastructure (brief)

- **`awcp_kit.py`** (one per agent) — the shared runtime kit: task queue +
  background worker, the **governance gate** for writes, the web console UI, and
  **optional live radar streaming** (each task is streamed to the AWCP radar when
  `AGENT_RADAR_URL` is set; fully standalone otherwise).
- **[`control_panel.py`](control_panel.py)** (`8099`) — one stdlib UI to
  start/stop every agent. Discovery is dynamic: any sub-folder containing a
  `run.sh` is treated as an agent — nothing about ports or models is hardcoded.
- **[`A2A/`](A2A/)** — a standalone prototype that borrows the **A2A Agent Card**
  schema as a registration format and layers an **AWCP governance envelope**
  (owner, write scopes, risk) on top, with a registry, an enforcement gate, an
  onboarding server, and a demo. See [A2A/README.md](A2A/README.md).

## Governance model

All agents share one rule: **reads are free, writes are gated.**

- `save_artifact` → **medium-risk** local write (gated, lands in `artifacts/`).
- `external_post` → **high-risk** external write (gated **and** pauses for
  operator approval before anything leaves the machine).

## Recent additions on this branch

- **Live radar streaming** — agents can self-register and stream each task's
  execution (steps, tool calls, token usage) to the AWCP radar.
- **Per-task token usage** — every runtime now reports `{input_tokens,
  output_tokens}`, feeding the radar's token accounting.
- **A2A onboarding prototype** — Agent Card + AWCP envelope schema, registry with
  a quarantine/approve enforcement gate, onboarding server, seed, and demo.
