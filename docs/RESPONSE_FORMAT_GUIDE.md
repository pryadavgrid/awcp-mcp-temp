# Response Format Guide - Clean, Debuggable Output

## Overview

The AWCP orchestration now returns **clean, structured responses** optimized for debugging in Temporal UI, logs, dashboards, and OpenTelemetry traces.

## Core Design Principles

1. ✅ **Never dump raw tool output** - Only extract key facts
2. ✅ **Clearly show which tool was used** - Track discovered, called, successful, failed
3. ✅ **Final synthesis always runs** - With automatic fallback on failure
4. ✅ **Separate execution stages** - Each stage visible in Temporal UI
5. ✅ **No empty answers** - Always provide meaningful output
6. ✅ **Debug visibility** - Developer can understand flow without reading logs

---

## Response Structure

### Complete Response Schema

```json
{
  "query": "user's original query",
  "execution_path": "llm | tools",
  
  "llm_decision": {
    "used": true,
    "final_direct_answer": false,
    "reason": "Requires latest external information"
  },
  
  "tool_execution": {
    "tools_discovered": [
      "web_search",
      "search_arxiv",
      "advanced_web_search"
    ],
    "tools_called": [
      {
        "tool": "web_search",
        "status": "success",
        "input": {
          "query": "who is the CEO of OpenAI"
        },
        "important_output": [
          "Sam Altman is CEO of OpenAI",
          "He returned to the role in November 2023"
        ],
        "reason": "User query requires current leadership information"
      },
      {
        "tool": "advanced_web_search",
        "status": "failed",
        "input": {
          "query": "who is the CEO of OpenAI"
        },
        "error": "Activity timeout after 5 minutes",
        "reason": "Backup search tool"
      }
    ]
  },
  
  "final_synthesis": {
    "status": "success",
    "used_tool_outputs": [
      "web_search"
    ],
    "answer": "Sam Altman is the CEO of OpenAI. He returned to this role in November 2023."
  }
}
```

---

## Execution Paths

### Path 1: Direct LLM Answer (Stable Knowledge)

**Trigger:** Query can be answered from internal LLM knowledge

**Example Queries:**
- "What is recursion?"
- "Explain object-oriented programming"
- "Write a Python function to sort a list"

**Response:**
```json
{
  "query": "What is recursion?",
  "execution_path": "llm",
  
  "llm_decision": {
    "used": true,
    "final_direct_answer": true,
    "reason": "Query asks for stable programming concept definition"
  },
  
  "tool_execution": {
    "tools_discovered": [],
    "tools_called": []
  },
  
  "final_synthesis": {
    "status": "direct",
    "used_tool_outputs": [],
    "answer": "Recursion is a programming technique where a function calls itself..."
  }
}
```

---

### Path 2: External Tools Required

**Trigger:** Query requires current, external, or research-based information

**Example Queries:**
- "Latest AI research papers"
- "Who is the CEO of OpenAI?"
- "Recent updates to Temporal.io"
- "Find arxiv papers about transformers"

**Response:**
```json
{
  "query": "latest research on quantum computing",
  "execution_path": "tools",
  
  "llm_decision": {
    "used": true,
    "final_direct_answer": false,
    "reason": "Query requires latest research papers from external sources"
  },
  
  "tool_execution": {
    "tools_discovered": [
      "search_arxiv",
      "web_search",
      "advanced_web_search"
    ],
    "tools_called": [
      {
        "tool": "search_arxiv",
        "status": "success",
        "input": {
          "query": "quantum computing",
          "max_results": 5
        },
        "important_output": [
          "Title: Quantum Error Correction Advances (2024)",
          "Authors: Smith et al.",
          "Summary: Recent developments in topological quantum codes...",
          "Published: 2024-01-15",
          "PDF: https://arxiv.org/pdf/2401.xxxxx"
        ],
        "reason": "User explicitly asked for research papers"
      }
    ]
  },
  
  "final_synthesis": {
    "status": "success",
    "used_tool_outputs": [
      "search_arxiv"
    ],
    "answer": "Recent quantum computing research includes significant advances in quantum error correction. A notable 2024 paper by Smith et al. discusses topological quantum codes..."
  }
}
```

---

## Status Field Values

### `execution_path`
- `"llm"` - Answered directly from LLM knowledge
- `"tools"` - Required external tool execution

### `tool_execution.tools_called[].status`
- `"success"` - Tool executed successfully
- `"failed"` - Tool execution failed (includes error message)

### `final_synthesis.status`
- `"direct"` - Direct LLM answer (no tools used)
- `"success"` - Successful synthesis from tool outputs
- `"fallback"` - Synthesis failed, using deterministic fallback
- `"no_tools_succeeded"` - All tools failed

---

## Important Output Extraction

### ❌ Bad (Raw Dump)
```json
{
  "tool": "web_search",
  "output": "<!DOCTYPE html><html><body>...10000 characters of HTML..."
}
```

### ✅ Good (Extracted Facts)
```json
{
  "tool": "web_search",
  "important_output": [
    "Sam Altman is CEO of OpenAI",
    "He returned to the role in November 2023",
    "OpenAI is an AI research company based in San Francisco"
  ]
}
```

### Extraction Rules
1. Max 10 important lines per tool
2. Each line: 20-500 characters
3. Take first 20 meaningful lines
4. Truncate raw output at 2000 characters before processing
5. Fallback: Show first 500 chars as preview

---

## Fallback Answer Generation

When LLM synthesis fails, a **deterministic fallback** is generated:

### Fallback Response
```json
{
  "final_synthesis": {
    "status": "fallback",
    "fallback_reason": "Activity timeout: synthesis LLM call exceeded 5 minutes",
    "used_tool_outputs": [
      "web_search",
      "search_arxiv"
    ],
    "answer": "Based on web_search, search_arxiv results:\n\nweb_search:\n  - Sam Altman is CEO of OpenAI\n  - He returned in November 2023\n\nsearch_arxiv:\n  - Recent paper on transformer architectures\n  - Published: 2024-01-10"
  }
}
```

### Fallback Rules
1. Never return empty answer
2. Show which tools contributed
3. List top 3 outputs per tool
4. Clear indication that synthesis failed
5. Provide reason for fallback

---

## Temporal UI Visibility

### Workflow Execution Trace
```
DynamicAskWorkflow
├── Activity: call_llm (STEP 1)
│   └── Duration: 2.3s, Status: ✓
├── Activity: discover_tools (STEP 2)
│   └── Duration: 0.5s, Status: ✓
├── Activity: select_tools (STEP 3)
│   └── Duration: 3.1s, Status: ✓
├── Activity: run_tool (web_search) (STEP 4)
│   └── Duration: 4.2s, Status: ✓
├── Activity: run_tool (search_arxiv) (STEP 4)
│   └── Duration: 3.8s, Status: ✓
└── Activity: synthesize_answer (STEP 5)
    └── Duration: 5.1s, Status: ✓
```

### Workflow Logs
```
[INFO] STEP 1: LLM Initial Reasoning
[INFO] DECISION: External tools required
[INFO] STEP 2: Tool Discovery
[INFO] Discovered 3 tools: ['search_arxiv', 'web_search', 'advanced_web_search']
[INFO] STEP 3: Tool Selection
[INFO] Selected 2 tools: ['web_search', 'search_arxiv']
[INFO] STEP 4: Tool Execution
[INFO] Executing tool: web_search
[INFO] Tool web_search: SUCCESS
[INFO] Executing tool: search_arxiv
[INFO] Tool search_arxiv: SUCCESS
[INFO] STEP 5: Final Synthesis
[INFO] Synthesis: SUCCESS
```

---

## Tool Classification

### Research Tools
- `search_arxiv`
- `semantic_scholar`
- `research_db`

**Use for:**
- Academic papers
- Research publications
- Scientific references
- Citations

### Web Tools
- `web_search`
- `advanced_web_search`

**Use for:**
- Current information
- News and events
- Documentation
- APIs
- Live data

---

## Error Handling

### All Tools Failed
```json
{
  "execution_path": "tools",
  "tool_execution": {
    "tools_called": [
      {"tool": "web_search", "status": "failed", "error": "Timeout"},
      {"tool": "search_arxiv", "status": "failed", "error": "Connection refused"}
    ]
  },
  "final_synthesis": {
    "status": "no_tools_succeeded",
    "answer": "I could not retrieve external information to answer your query. All tool executions failed. Please check the tool execution details."
  }
}
```

### Synthesis Failed (with Fallback)
```json
{
  "final_synthesis": {
    "status": "fallback",
    "fallback_reason": "RuntimeError: Synthesis LLM call failed",
    "used_tool_outputs": ["web_search"],
    "answer": "Based on web_search results:\n\nweb_search:\n  - Sam Altman is CEO of OpenAI\n  - Returned November 2023"
  }
}
```

---

## Testing Examples

### Test 1: Direct LLM Answer
```bash
curl -X POST http://localhost:8001/api/workflow/execute \
  -H "Content-Type: application/json" \
  -d '{"query": "explain recursion"}'
```

**Expected:**
- `execution_path`: `"llm"`
- `llm_decision.final_direct_answer`: `true`
- `tool_execution.tools_called`: `[]`
- `final_synthesis.status`: `"direct"`

### Test 2: Research Query (Arxiv)
```bash
curl -X POST http://localhost:8001/api/workflow/execute \
  -H "Content-Type: application/json" \
  -d '{"query": "latest machine learning research papers"}'
```

**Expected:**
- `execution_path`: `"tools"`
- `llm_decision.final_direct_answer`: `false`
- `tool_execution.tools_called`: Contains `search_arxiv`
- `final_synthesis.status`: `"success"` or `"fallback"`
- `final_synthesis.used_tool_outputs`: `["search_arxiv"]`

### Test 3: Current Info Query (Web)
```bash
curl -X POST http://localhost:8001/api/workflow/execute \
  -H "Content-Type: application/json" \
  -d '{"query": "who is the current CEO of OpenAI"}'
```

**Expected:**
- `execution_path`: `"tools"`
- `tool_execution.tools_called`: Contains `web_search`
- `final_synthesis.used_tool_outputs`: `["web_search"]`

---

## Debug Checklist

When debugging a workflow execution, check:

1. ✅ **Execution Path** - Was it `llm` or `tools`?
2. ✅ **LLM Decision Reason** - Why was this path chosen?
3. ✅ **Tools Discovered** - Were the right tools available?
4. ✅ **Tools Called** - Which tools were actually invoked?
5. ✅ **Tool Status** - Did any tools fail?
6. ✅ **Important Outputs** - Are the extracted facts meaningful?
7. ✅ **Synthesis Status** - Was it success, fallback, or failure?
8. ✅ **Used Tool Outputs** - Which tools contributed to the final answer?
9. ✅ **Final Answer** - Is it non-empty and useful?

---

## Benefits

### For Developers
- 🔍 Instant understanding of workflow decisions
- 🐛 Easy debugging without reading raw logs
- 📊 Clean metrics and monitoring
- 🎯 Clear tool execution visibility

### For Operations
- 📈 Better observability in Temporal UI
- ⚡ Faster incident response
- 📉 Reduced log noise
- ✅ Clear success/failure indicators

### For Users
- 💬 Cleaner API responses
- 🎨 Better frontend integration
- 🚀 Easier result parsing
- 📝 Structured error messages
