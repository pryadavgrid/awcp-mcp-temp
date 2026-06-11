# Execution Flow Diagram

## Complete Workflow Visualization

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                    USER QUERY                             ┃
┃              "latest AI research papers"                   ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                             ▼
         ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
         ┃   STEP 1: LLM INITIAL REASONING        ┃
         ┃   Activity: mcp_call_llm               ┃
         ┃                                         ┃
         ┃   Question: Can we answer directly?    ┃
         ┗━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┛
                          ▼
         ┌───────────────────────────────────────┐
         │   LLM Decision Analysis               │
         │                                       │
         │   final = false                       │
         │   reason = "Requires research papers" │
         └──────────────┬────────────────────────┘
                        ▼
              ┌─────────────────┐
              │  DECISION POINT │
              └────┬────────┬───┘
                   │        │
         final=true│        │final=false
                   │        │
                   ▼        ▼
         ┌─────────────┐  ┌──────────────────┐
         │  PATH: llm  │  │  PATH: tools     │
         └─────────────┘  └──────────────────┘
                │                  │
                ▼                  ▼
     ╔═══════════════════╗  ╔═══════════════════════════════╗
     ║ DIRECT LLM ANSWER ║  ║  EXTERNAL TOOLS REQUIRED      ║
     ╚═══════════════════╝  ╚═══════════════════════════════╝
                │                  │
                │                  ▼
                │         ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
                │         ┃  STEP 2: TOOL DISCOVERY      ┃
                │         ┃  Activity: mcp_discover_tools┃
                │         ┃                               ┃
                │         ┃  Returns: List of available  ┃
                │         ┃  tools with parameters       ┃
                │         ┗━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┛
                │                      ▼
                │         ┌────────────────────────────────┐
                │         │  Discovered Tools:             │
                │         │  • search_arxiv                │
                │         │  • web_search                  │
                │         │  • advanced_web_search         │
                │         └──────────────┬─────────────────┘
                │                        ▼
                │         ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
                │         ┃  STEP 3: TOOL SELECTION      ┃
                │         ┃  Activity: mcp_select_tools  ┃
                │         ┃                               ┃
                │         ┃  LLM decides which tools     ┃
                │         ┃  to use for this query       ┃
                │         ┗━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┛
                │                      ▼
                │         ┌────────────────────────────────┐
                │         │  Selected Tools:               │
                │         │  ✓ search_arxiv (research)     │
                │         │  ✓ web_search (backup)         │
                │         └──────────────┬─────────────────┘
                │                        ▼
                │         ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
                │         ┃  STEP 4: TOOL EXECUTION      ┃
                │         ┃  Activities: mcp_run_tool ×N ┃
                │         ┗━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┛
                │                      ▼
                │         ┌────────────────────────────────┐
                │         │  Run: search_arxiv             │
                │         │  Status: ✓ success             │
                │         │  Extract important_output:     │
                │         │    • "Paper: Transformer..."   │
                │         │    • "Authors: Vaswani et al." │
                │         │    • "Published: 2024-01-15"   │
                │         └──────────────┬─────────────────┘
                │                        ▼
                │         ┌────────────────────────────────┐
                │         │  Run: web_search               │
                │         │  Status: ✓ success             │
                │         │  Extract important_output:     │
                │         │    • "Recent AI advances..."   │
                │         └──────────────┬─────────────────┘
                │                        ▼
                │         ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
                │         ┃  STEP 5: FINAL SYNTHESIS     ┃
                │         ┃  Activity: mcp_synthesize    ┃
                │         ┃                               ┃
                │         ┃  Combine tool outputs into   ┃
                │         ┃  coherent answer             ┃
                │         ┗━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┛
                │                      ▼
                │         ┌────────────────────────────────┐
                │         │  Synthesis Status?             │
                │         └──────┬──────────────┬──────────┘
                │                │              │
                │        Success │              │ Failed
                │                ▼              ▼
                │    ┌─────────────────┐  ┌──────────────┐
                │    │ status: success │  │status:       │
                │    │ LLM-generated   │  │  fallback    │
                │    │ answer          │  │Deterministic │
                │    └────────┬────────┘  │answer from   │
                │             │           │tool outputs  │
                │             │           └──────┬───────┘
                │             │                  │
                ▼             ▼                  ▼
     ╔═════════════════════════════════════════════╗
     ║           FINAL RESPONSE                    ║
     ╚═════════════════════════════════════════════╝
                          ▼
     ┌──────────────────────────────────────────────┐
     │  {                                           │
     │    "query": "latest AI research papers",     │
     │    "execution_path": "llm" | "tools",        │
     │    "llm_decision": { ... },                  │
     │    "tool_execution": {                       │
     │      "tools_discovered": [...],              │
     │      "tools_called": [...]                   │
     │    },                                        │
     │    "final_synthesis": {                      │
     │      "status": "success|fallback|...",       │
     │      "used_tool_outputs": [...],             │
     │      "answer": "..."                         │
     │    }                                         │
     │  }                                           │
     └──────────────────────────────────────────────┘
```

---

## Path 1: Direct LLM Answer

```
User Query: "What is recursion?"
     │
     ▼
┌──────────────────┐
│ STEP 1: call_llm │
└────────┬─────────┘
         │
         ▼
    final = true
    (stable knowledge)
         │
         ▼
┌─────────────────────┐
│ execution_path: llm │
│ status: direct      │
│ answer: "..."       │
└─────────────────────┘
         │
         ▼
    RETURN RESPONSE
```

---

## Path 2: External Tools Required

```
User Query: "Latest AI research papers"
     │
     ▼
┌──────────────────┐
│ STEP 1: call_llm │
└────────┬─────────┘
         │
         ▼
    final = false
    (needs research)
         │
         ▼
┌─────────────────────┐
│ STEP 2: discover    │
│ tools_discovered: [ │
│   "search_arxiv",   │
│   "web_search"      │
│ ]                   │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ STEP 3: select      │
│ Selected:           │
│ • search_arxiv      │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ STEP 4: execute     │
│ Run search_arxiv    │
│   ├─ Success ✓      │
│   └─ Extract facts  │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ STEP 5: synthesize  │
│ Combine outputs     │
│   ├─ Try LLM        │
│   └─ Fallback if    │
│       needed        │
└────────┬────────────┘
         │
         ▼
┌──────────────────────┐
│ execution_path: tools│
│ status: success      │
│ used_tools:          │
│   ["search_arxiv"]   │
│ answer: "..."        │
└──────────────────────┘
         │
         ▼
    RETURN RESPONSE
```

---

## Temporal UI Trace View

```
┌─────────────────────────────────────────────────────────┐
│ Workflow: DynamicAskWorkflow                            │
│ ID: awcp-ask-2024-06-08-abc123                          │
│ Status: ✓ Completed                                     │
│ Duration: 18.7s                                         │
├─────────────────────────────────────────────────────────┤
│ Timeline:                                               │
│                                                         │
│ 0s    ├─[STEP 1] call_llm                  ✓ 2.3s     │
│ 2.3s  ├─[STEP 2] discover_tools            ✓ 0.5s     │
│ 2.8s  ├─[STEP 3] select_tools              ✓ 3.1s     │
│ 5.9s  ├─[STEP 4] run_tool (search_arxiv)   ✓ 4.2s     │
│ 5.9s  ├─[STEP 4] run_tool (web_search)     ✓ 3.8s     │
│ 10.1s └─[STEP 5] synthesize_answer         ✓ 5.1s     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Activity Details Example

### Activity: run_tool (search_arxiv)

```
┌─────────────────────────────────────────────────────────┐
│ Activity: run_tool                                      │
│ Status: ✓ Completed                                     │
│ Duration: 4.2s                                          │
│ Attempt: 1                                              │
├─────────────────────────────────────────────────────────┤
│ Input:                                                  │
│ {                                                       │
│   "tool_name": "search_arxiv",                          │
│   "tool_input": {                                       │
│     "query": "latest AI research papers",               │
│     "max_results": 5                                    │
│   }                                                     │
│ }                                                       │
├─────────────────────────────────────────────────────────┤
│ Output:                                                 │
│ {                                                       │
│   "tool_name": "search_arxiv",                          │
│   "tool_input": {...},                                  │
│   "output": "[Paper results...]",                       │
│   "status": "succeeded"                                 │
│ }                                                       │
├─────────────────────────────────────────────────────────┤
│ Logs:                                                   │
│ [INFO] Starting run_tool activity                      │
│ [INFO] Tool: search_arxiv                              │
│ [INFO] Completed run_tool successfully                 │
└─────────────────────────────────────────────────────────┘
```

---

## Error Handling Flow

### Scenario: Tool Fails

```
User Query
     │
     ▼
STEP 1-3: Normal flow
     │
     ▼
STEP 4: Execute Tools
     │
     ├─ search_arxiv ✓ Success
     │
     └─ web_search ✗ Failed
         │
         ▼
    Continue (don't abort)
     │
     ▼
STEP 5: Synthesize
     │
     └─ Use only successful tools
         │
         ▼
    ┌────────────────────┐
    │ Response shows:    │
    │ • Both tools tried │
    │ • One succeeded    │
    │ • One failed       │
    │ • Answer from      │
    │   successful tool  │
    └────────────────────┘
```

### Scenario: Synthesis Fails

```
STEP 5: Synthesize
     │
     ├─ Try LLM synthesis
     │      │
     │      ✗ Failed (timeout)
     │
     └─ Generate fallback
         │
         ▼
    Deterministic answer from
    successful tool outputs
         │
         ▼
    ┌────────────────────┐
    │ Response shows:    │
    │ status: "fallback" │
    │ fallback_reason:   │
    │   "LLM timeout"    │
    │ answer: Based on   │
    │   tool results...  │
    └────────────────────┘
```

---

## Response Transformation

### Stage: Tool Execution

**Raw Tool Output (5000 chars):**
```
<!DOCTYPE html><html><head><title>OpenAI Leadership</title></head>
<body><div class="content"><h1>OpenAI</h1><p>Sam Altman is the 
CEO of OpenAI. He returned to this role in November 2023...</p>
... [4950 more characters] ...
```

**↓ Transform ↓**

**Extracted Important Output:**
```json
"important_output": [
  "Sam Altman is the CEO of OpenAI",
  "He returned to this role in November 2023",
  "OpenAI is an AI research company based in San Francisco"
]
```

---

## Decision Tree

```
                    ┌─────────────┐
                    │ User Query  │
                    └──────┬──────┘
                           │
                           ▼
                  ┌────────────────┐
                  │ LLM evaluates  │
                  └────┬───────────┘
                       │
           ┌───────────┴───────────┐
           │                       │
           ▼                       ▼
    ┌────────────┐        ┌──────────────┐
    │ Stable     │        │ Requires     │
    │ knowledge? │        │ external     │
    │            │        │ data?        │
    └─────┬──────┘        └──────┬───────┘
          │                      │
    YES   │                 YES  │
          │                      │
          ▼                      ▼
    ┌──────────┐        ┌───────────────┐
    │ Direct   │        │ What type?    │
    │ Answer   │        └───┬───────────┘
    └──────────┘            │
                ┌───────────┴────────────┐
                │                        │
                ▼                        ▼
        ┌──────────────┐       ┌───────────────┐
        │ Research     │       │ Current/Live  │
        │ Papers?      │       │ Info?         │
        └──────┬───────┘       └───────┬───────┘
               │                       │
               ▼                       ▼
        ┌──────────────┐       ┌──────────────┐
        │ search_arxiv │       │ web_search   │
        └──────────────┘       └──────────────┘
```

---

## Summary

The execution flow is now:
- ✅ **Linear & Clear** - 5 distinct stages
- ✅ **Well-Logged** - Each stage logs progress
- ✅ **Fault-Tolerant** - Continues on tool failures
- ✅ **Observable** - Beautiful Temporal traces
- ✅ **Debuggable** - Instant visibility without log diving
