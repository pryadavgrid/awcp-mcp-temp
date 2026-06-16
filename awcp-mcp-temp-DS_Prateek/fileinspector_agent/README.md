# File Inspector Agent (LangGraph runtime)

A **free, fully-working file-inspection agent** that runs as a long-lived HTTP **runtime** —
the AWCP "agent on an existing runtime" model — built so a process-scanning registry like
`agent_radar` **auto-detects it** (as `agent_framework` / LangGraph).

Give it **any file** — text, image, PDF, JSON, `.docx`, CSV, source code, or an unknown
binary — and it gives you a **brief, plain-language summary of what's inside** (no
technical metadata — just the contents).

- **LangGraph** runtime over **local Ollama** (LangChain) models — a deterministic
  read/extract step plus a short grounded summary (no API keys).
- Strong free local models by default: **`qwen2.5:7b`** for reading/summarising text and
  documents, **`qwen2.5vl:7b`** (Qwen2.5-VL) for image captioning. Both env-overridable.
- Real, deterministic file tools: `classify_file`, `read_document`, `describe_image`.
- **Multimodal** — images are described by the local Ollama **vision** model.
- **Runtime** — a persistent FastAPI service with a drag-and-drop upload box.

## Run

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# needs Ollama with these models (free, local):
ollama pull qwen2.5:7b         # reading / summarising documents & text
ollama pull qwen2.5vl:7b       # image captioning
./run.sh                       # http://localhost:8104
```

Open <http://localhost:8104>, **drop a file** (or click *Attach file*), optionally type
what you want to know, and it replies with a short summary of what the file contains.

Try it from the shell:
```bash
# upload a file, then inspect it via a goal that carries its local path
P=$(curl -s -F "file=@/etc/hosts" localhost:8104/upload | python3 -c 'import sys,json;print(json.load(sys.stdin)["path"])')
curl -s -XPOST localhost:8104/tasks -H 'Content-Type: application/json' \
  -d "{\"goal\":\"Identify this file and summarise it.\n\nFILE_PATH: $P\"}"
```

Endpoints: `GET /` (console UI), `POST /upload` (file intake), `POST /tasks {goal}`,
`GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/approve`, `GET /info`, `GET /health`.

## How it works

1. The upload box (or a hand-typed path) puts a `FILE_PATH:` into the **goal**.
2. The background worker reads the file deterministically (`classify_file` →
   `read_document`, or `describe_image` for images), then writes a **brief summary of
   what's inside** — no metadata, just the contents.
3. The result is persisted through the agent's **self-governed write path**
   (`save_artifact` = medium-risk local write; `external_post` = high-risk external
   write that pauses for operator approval). Files land in `artifacts/`.
4. If the summary model is unavailable, a **fallback** shows the file's extracted
   content directly so you still see what's inside.

## How `agent_radar` detects it

Launched as `python <absolute>/agent_runtime.py`, which imports `langgraph` at the top.
The radar's scanner reads the referenced script, sees the `from langgraph...` import, and
registers it as `kind=agent_framework, framework=langgraph` (`detected_via=script_import`),
exactly like the other LangGraph runtimes here. Or manage everything from one place:
`python3 ../control_panel.py` → <http://localhost:8099>.

## Config (env — nothing hardcoded)
- `FILE_MODEL` (default `qwen2.5:7b`) — Ollama model for reading/summarising text.
  Bump to `qwen2.5:14b` or `llama3.3:70b` on a bigger machine for higher quality.
- `VISION_MODEL` (default `qwen2.5vl:7b`) — Ollama model for image captioning
  (alternatives: `llama3.2-vision`, `minicpm-v`, `llava`).
- `OLLAMA_BASE` (default `http://localhost:11434`).
- `FILE_PORT` (default `8104`).
- `FILE_MAX_CHARS` (default `40000`) — extracted-text cap.
- `FILE_MODEL_CHARS` (default `16000`) — how much extracted text is fed to the model.
- `AGENT_EXTERNAL_WRITE_URL` / `AGENT_APPROVAL_REQUIRED` / `AGENT_FINALIZE_EXTERNAL` —
  shared governance knobs (see `awcp_kit.py`).

## Supported inputs
Images (`jpg/png/webp/bmp/gif/tiff`), PDFs, Word `.docx`, and text/code/data
(`txt/md/json/csv/tsv/py/js/ts/html/xml/yaml/toml/ini/sql/sh/...`). Anything else is
sniffed by **magic bytes** and reported as a typed binary.

## Notes
- Standalone — free end-to-end (local model + local libraries).
- `awcp_kit.py` here is the shared kit plus one **additive** feature: a `/upload`
  endpoint and a drag-drop box, shown only because this agent sets
  `accepts_files: true` in its `meta` (other agents are unaffected).
- `run.sh` launches with an **absolute** script path so the radar can read this file
  and detect the framework import.
