# Dynamic Tool Selection - Implementation Guide

## Overview

The system now implements **intelligent, dynamic tool selection** where the LLM automatically determines when external tools (like Arxiv search) are needed and triggers them without hardcoding.

## Core Behavior Flow

```
User Query
    ↓
Step 1: Internal LLM Reasoning (call_llm)
    ↓
Step 2: Evaluate Need for External Tools
    ↓
    ├─→ If final=true → Return direct answer
    │
    └─→ If final=false → Continue to tools
            ↓
        Step 3: Discover Available Tools (discover_tools)
            ↓
        Step 4: LLM Selects Relevant Tools (select_tools)
            ↓
        Step 5: Execute Selected Tools (run_tool for each)
            ↓
        Step 6: Synthesize Final Answer (synthesize_answer)
```

## Decision Logic

### When `final=true` (Answer Directly)
- Pure reasoning, logic, or math
- Code writing/explanation
- Creative writing
- Stable definitional knowledge
- User explicitly says "from your knowledge"

### When `final=false` (Use Tools)
- **Research papers** or academic publications
- **Latest/recent/current** information
- Scientific references or technical literature
- **Arxiv papers** or scholarly articles
- Live data (news, weather, prices)
- Date-sensitive facts or statistics
- External verification needed

## Example Queries

### Triggers Arxiv Tool ✅
```
"latest research papers on multi-agent systems"
"find arxiv papers about transformers"
"recent studies on quantum computing"
"research on Temporal.io and AI agents"
"papers about retrieval-augmented generation"
```

### Triggers Web Search Tool ✅
```
"current CEO of OpenAI"
"latest news about AI"
"weather in London today"
"GPU prices in 2024"
```

### Uses Internal Knowledge Only ✅
```
"explain recursion"
"write a python function to sort"
"what is object-oriented programming"
"how does binary search work"
```

## Tool Selection Priority

The `select_runtime_tools` function uses this logic:

1. **Research/Academic queries** → `search_arxiv`
   - Papers, studies, scientific publications
   - Academic content, scholarly articles

2. **Current/Live/News queries** → `web_search` or `advanced_web_search`
   - Recent events, news, documentation
   - Live data, prices, current facts

3. **Fallback** → Query-compatible tools with "query" parameter

## Files Modified

### 1. `dynamic_ask.py` (Workflow)
- Removed hardcoded arxiv call
- Implements conditional tool execution based on `final` flag
- Tool discovery happens only when needed

### 2. `server.py` (MCP Server)
- Enhanced `call_llm` prompt with explicit research detection
- Improved `select_runtime_tools` with clear priority rules
- Added examples for better LLM decision-making

### 3. `arxiv_search.py` (Tool)
- Added comprehensive docstring
- Describes use cases and return format

### 4. `mcp_gateway.py` (Activities)
- Removed redundant `mcp_search_arxiv` activity
- All tools now go through generic `mcp_run_tool`

## Testing

### Test Research Query
```bash
# Should trigger arxiv tool automatically
curl -X POST http://localhost:8001/api/workflow/execute \
  -H "Content-Type: application/json" \
  -d '{"query": "latest research on large language models"}'
```

### Test General Knowledge Query
```bash
# Should answer directly without tools
curl -X POST http://localhost:8001/api/workflow/execute \
  -H "Content-Type: application/json" \
  -d '{"query": "explain what is a linked list"}'
```

### Expected Response Structure
```json
{
  "system_action": "SUCCESS",
  "path": "tools",  // or "llm" if no tools used
  "query": "...",
  "answer": "...",
  "llm": {
    "final": false,  // true if no tools needed
    "reason": "Query requires research papers..."
  },
  "discovered_tools": ["search_arxiv", "web_search", ...],
  "selection": {
    "tool_calls": [
      {
        "tool_name": "search_arxiv",
        "tool_input": {"query": "...", "max_results": 5},
        "reason": "User requested research papers"
      }
    ]
  },
  "tool_results": [...]
}
```

## Key Principles

1. **Always try internal knowledge first** - Don't waste resources if LLM can answer
2. **Automatically detect need for tools** - Never skip discovery when external info is needed
3. **Dynamic tool discovery** - No hardcoded tool calls
4. **LLM-driven selection** - Intelligence decides which tools to use
5. **Graceful fallback** - If tools fail, provide partial answer

## Benefits

- ✅ No hardcoded tool calls
- ✅ Intelligent automatic tool selection
- ✅ Works for any new tools added to `awcp/tools/`
- ✅ Efficient: only calls tools when needed
- ✅ Extensible: add new tools without changing workflow code
- ✅ Research queries automatically use Arxiv
- ✅ Respects user intent from query analysis
