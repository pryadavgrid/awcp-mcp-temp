Viewed mcp_gateway.py:1-257
Viewed base_workflow.py:1-64
Viewed server.py:1-651
Listed directory tools
Listed directory runtime
Viewed web_search.py:1-83
Viewed tool_runtime.py:1-95
Viewed run_worker.py:1-53

Now I have a complete picture of the entire system. Here is the **full step-by-step breakdown**:

---

# 🔍 What Happens When You `POST /ask` with Query: `"What Is The Price Of Silver Today"`

---

## 🏗️ Architecture Overview (Before the Request)

Three processes must be running:

| Process | File | Port |
|---|---|---|
| **FastAPI Control Surface** | `control/api.py` | `8001` |
| **Temporal Worker** | `temporal/worker/run_worker.py` | connects to `localhost:7233` |
| **Temporal Server** | (external) | `7233` |

The **MCP Server** (`mcp/server.py`) is **NOT a long-running process** — it is **spawned on demand as a subprocess** by each Temporal activity via `stdio`.

---

## 📨 STEP 0 — HTTP Request Arrives at FastAPI

```http
POST /ask
Content-Type: application/json

{"query": "What Is The Price Of Silver Today"}
```

**In [`control/api.py` → `ask()` function (line 98–140)](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/control/api.py#L98-L140):**

1. `req.query.strip()` → `"What Is The Price Of Silver Today"` ✅ not empty
2. `workflow_id` is generated → e.g. `awcp-ask-a3f9c12b`
3. Logger logs: `Starting /ask workflow_id=awcp-ask-a3f9c12b query='What Is The Price Of Silver Today'`
4. `Client.connect("localhost:7233")` → connects to Temporal server
5. `client.start_workflow(DynamicAskWorkflow.run, {"query": "What Is The Price Of Silver Today"}, id="awcp-ask-a3f9c12b", task_queue="awcp-governance-queue")`
6. `handle.result()` → **FastAPI BLOCKS HERE** waiting for the workflow to complete

---

## ⚙️ STEP 1 — Temporal Dispatches to the Worker

**In [`temporal/worker/run_worker.py`](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/temporal/worker/run_worker.py):**

- The worker is listening on task queue: `"awcp-governance-queue"`
- Temporal picks up the `DynamicAskWorkflow` task and routes it to the worker
- The worker runs `DynamicAskWorkflow.run({"query": "What Is The Price Of Silver Today"})`

---

## 🧠 STEP 2 — `DynamicAskWorkflow` Starts (5 Internal Steps)

**In [`temporal/workflows/dynamic_ask.py`](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/temporal/workflows/dynamic_ask.py)**

---

### 🔵 WORKFLOW STEP 1: LLM Initial Reasoning (`mcp_call_llm`)

**Activity executed:** `mcp_call_llm` → calls MCP tool `call_llm`

The worker **spawns the MCP Server as a subprocess** via `stdio`:
```
python -m awcp.mcp.server stdio
```

The MCP tool [`call_llm` in `mcp/server.py` (line 307)](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/mcp/server.py#L307-L369) sends this prompt to **Ollama (local LLM)**:

> *"What Is The Price Of Silver Today — should I use tools or answer directly?"*

**Decision Logic:**  
Since the query contains **"price"** and **"today"** → this is **live market data** (date-sensitive) → the LLM sets:
```json
{"final": false, "answer": "", "reason": "Requires live/current pricing data"}
```

➡️ `final = false` → **Workflow does NOT stop here.** Moves to tool path.

```python
response["execution_path"] = "tools"
```

---

### 🔵 WORKFLOW STEP 2: Tool Discovery (`mcp_discover_tools`)

**Activity executed:** `mcp_discover_tools` → calls MCP tool `list_runtime_tools`

The MCP server introspects `TOOL_REGISTRY` (populated by `discover_tools()` which auto-imports all modules in `awcp/tools/`):

Discovered tools from [`tools/` directory](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/tools):
| Tool Name | File |
|---|---|
| `web_search` | `tools/web_search.py` |
| `advanced_web_search` | `tools/advanced_web_search.py` |
| `arxiv_search` | `tools/arxiv_search.py` |

Returns a JSON list of all 3 tools with their parameter schemas.

```python
response["tool_execution"]["tools_discovered"] = ["web_search", "advanced_web_search", "arxiv_search"]
```

---

### 🔵 WORKFLOW STEP 3: Tool Selection (`mcp_select_tools`)

**Activity executed:** `mcp_select_tools` → calls MCP tool `select_runtime_tools`

The MCP server sends a prompt to Ollama with the query + discovered tools list.

**The LLM's selection logic from [`mcp/server.py` line 398-410](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/mcp/server.py#L398-L410):**

> - "current events, news, general facts, recent information" → `web_search` or `advanced_web_search`
> - "research papers, academic content" → `search_arxiv`

Since "Price Of Silver Today" = **live market/financial data** → LLM selects:
```json
{
  "tool_calls": [
    {
      "tool_name": "web_search",
      "tool_input": {"query": "What Is The Price Of Silver Today"},
      "reason": "Live price data requires a web search"
    }
  ]
}
```

> ⚠️ `arxiv_search` would NOT be selected — this is not a research/academic query.

---

### 🔵 WORKFLOW STEP 4: Tool Execution (`mcp_run_tool`)

**Activity executed:** `mcp_run_tool` with `tool_name="web_search"` → calls MCP tool `execute_tool`

The MCP `execute_tool` calls `run_tool("web_search", {"query": "What Is The Price Of Silver Today"})` which invokes [`tools/web_search.py`](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/tools/web_search.py):

```python
# web_search.py runs TWO DuckDuckGo searches:
# 1. "What Is The Price Of Silver Today latest recent" (timelimit="y")
# 2. "What Is The Price Of Silver Today" (timelimit=None)
```

Uses **DuckDuckGo Search (`DDGS`)** — fetches up to 10 results, deduplicates by URL.

Returns a formatted string like:
```
Result 1
Title: Silver Price Today | Silver Spot Price Chart | Live Price of Silver
Snippet: Silver spot price is $31.25 per troy ounce. Silver price per gram: $1.01...
URL: https://www.kitco.com/charts/livesilver.html

Result 2
Title: Silver Prices Today - Live Silver Price Chart - BullionVault
Snippet: Today silver price is $31.20 oz. Buy and sell silver at live silver prices...
URL: https://www.bullionvault.com/silver-price-chart.do
...
```

The raw output is passed through `_extract_important_output()` which:
- Limits to first 2000 chars
- Splits into lines, keeps lines between 20–500 chars
- Returns top 10 important lines

```python
response["tool_execution"]["tools_called"] = [{
    "tool": "web_search",
    "status": "success",
    "input": {"query": "What Is The Price Of Silver Today"},
    "important_output": ["Silver spot price is $31.25 per troy ounce...", ...],
    "reason": "Live price data requires a web search"
}]
```

> ⚡ **Retry Policy:** `TOOL_EXECUTION_RETRY` — up to **3 attempts**, 2s→30s backoff

---

### 🔵 WORKFLOW STEP 5: Final Synthesis (`mcp_synthesize_answer`)

**Activity executed:** `mcp_synthesize_answer` → calls MCP tool `synthesize_tool_results`

The MCP [`synthesize_tool_results` function (line 467)](file:///Users/pryadav/Desktop/AWCP-Capstone/awcp-mcp-temporal/src/awcp/mcp/server.py#L467-L549) builds a prompt:

```
You are a factual QA assistant.
User query: What Is The Price Of Silver Today
Tool outputs:
Tool: web_search
Output:
Silver spot price is $31.25 per troy ounce...
[...more results...]

Your brief answer:
```

Ollama LLM synthesizes and returns something like:
> *"As of today, the spot price of silver is approximately $31.25 per troy ounce. Prices fluctuate throughout the trading day, so check a live source like Kitco or BullionVault for the most current figure."*

```python
response["final_synthesis"] = {
    "status": "success",
    "used_tool_outputs": ["web_search"],
    "answer": "As of today, the spot price of silver is approximately $31.25 per troy ounce..."
}
```

> ⚡ **Retry Policy:** `SYNTHESIS_RETRY` — up to **2 attempts**, non-retryable on `ValueError/TypeError/KeyError`

---

## 📤 STEP 3 — Workflow Returns to FastAPI

The workflow returns the full `response` dict to `handle.result()`. FastAPI's `ask()` function logs:

```
Completed /ask workflow_id=awcp-ask-a3f9c12b synthesis_status=success
```

---

## ✅ FINAL HTTP RESPONSE

```json
{
  "workflow_id": "awcp-ask-a3f9c12b",
  "temporal_url": "http://localhost:8233/namespaces/default/workflows/awcp-ask-a3f9c12b",
  "result": {
    "query": "What Is The Price Of Silver Today",
    "execution_path": "tools",
    "llm_decision": {
      "used": true,
      "final_direct_answer": false,
      "reason": "Requires live/current pricing data"
    },
    "tool_execution": {
      "tools_discovered": ["web_search", "advanced_web_search", "arxiv_search"],
      "tools_called": [
        {
          "tool": "web_search",
          "status": "success",
          "input": {"query": "What Is The Price Of Silver Today"},
          "important_output": [
            "Silver spot price is $31.25 per troy ounce...",
            "..."
          ],
          "reason": "Live price data requires a web search"
        }
      ]
    },
    "research_results": null,
    "final_synthesis": {
      "status": "success",
      "used_tool_outputs": ["web_search"],
      "answer": "As of today, the spot price of silver is approximately $31.25 per troy ounce..."
    }
  }
}
```

---

## 🗺️ Complete Flow Diagram

```
POST /ask {"query": "What Is The Price Of Silver Today"}
    │
    ▼
FastAPI (control/api.py)
    │  generates workflow_id = "awcp-ask-a3f9c12b"
    │  starts DynamicAskWorkflow on Temporal
    │  BLOCKS waiting for result
    │
    ▼
Temporal Server (localhost:7233)
    │  queues task on "awcp-governance-queue"
    │
    ▼
Temporal Worker (run_worker.py)
    │
    ├─► STEP 1: Activity mcp_call_llm
    │       └─► spawns MCP Server subprocess (stdio)
    │           └─► MCP tool: call_llm → Ollama LLM
    │               └─► Decision: final=false (needs live data)
    │
    ├─► STEP 2: Activity mcp_discover_tools
    │       └─► spawns MCP Server subprocess (stdio)
    │           └─► MCP tool: list_runtime_tools
    │               └─► Returns: [web_search, advanced_web_search, arxiv_search]
    │
    ├─► STEP 3: Activity mcp_select_tools
    │       └─► spawns MCP Server subprocess (stdio)
    │           └─► MCP tool: select_runtime_tools → Ollama LLM
    │               └─► Selects: web_search (live price = current event)
    │
    ├─► STEP 4: Activity mcp_run_tool (web_search)
    │       └─► spawns MCP Server subprocess (stdio)
    │           └─► MCP tool: execute_tool → web_search
    │               └─► DuckDuckGo search → returns silver price results
    │
    └─► STEP 5: Activity mcp_synthesize_answer
            └─► spawns MCP Server subprocess (stdio)
                └─► MCP tool: synthesize_tool_results → Ollama LLM
                    └─► Synthesizes final answer from web results
    │
    ▼
Workflow returns result dict
    │
    ▼
FastAPI returns HTTP 200 JSON response
```

---

## ⚠️ Edge Cases / Failure Paths

| Scenario | What Happens |
|---|---|
| **Ollama is down** | `call_llm` returns `final=false` (safe fallback), continues to tools anyway |
| **web_search returns empty** | Tool marked `status: failed`, synthesis skipped, returns `"no_tools_succeeded"` message |
| **Synthesis LLM fails** | `_generate_fallback_answer()` builds a deterministic text answer from raw tool outputs |
| **Temporal not running** | `Client.connect()` fails → FastAPI returns `HTTP 502` with `workflow_id` and error detail |
| **Query is empty** | FastAPI immediately returns `HTTP 400` before touching Temporal at all |