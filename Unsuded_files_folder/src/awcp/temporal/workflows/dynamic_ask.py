from datetime import timedelta
import json

from temporalio import workflow
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from awcp.temporal.activities.mcp_gateway import (
        mcp_call_llm,
        mcp_discover_tools,
        mcp_run_tool,
        mcp_select_tools,
        mcp_synthesize_answer,
    )
    from awcp.temporal.workflows.base_workflow import (
        FAST_INTERNAL_RETRY,
        SYNTHESIS_RETRY,
        TOOL_EXECUTION_RETRY,
    )


@workflow.defn
class DynamicAskWorkflow:
    """Durable natural-language query workflow with clean, structured output.

    Execution Flow:
    1. LLM Initial Reasoning - Can we answer directly?
    2. Tool Discovery - What tools are available?
    3. Tool Selection - Which tools should we use?
    4. Tool Execution - Run each tool independently
    5. Final Synthesis - Generate answer from tool outputs

    The response format is optimized for debugging in Temporal UI, logs, and dashboards.
    """

    @workflow.run
    async def run(self, workflow_input: dict) -> dict:
        query = workflow_input["query"].strip()
        _otel_ctx = workflow_input.get("_otel_ctx", {})
        
        # Initialize clean response structure
        response = {
            "query": query,
            "execution_path": None,
            "llm_decision": {
                "used": False,
                "final_direct_answer": False,
                "reason": ""
            },
            "tool_execution": {
                "tools_discovered": [],
                "tools_called": []
            },
            "research_results": None,  # Will be populated if research tools used
            "final_synthesis": {
                "status": None,
                "used_tool_outputs": [],
                "answer": ""
            }
        }
        
        # Track if research tools were used
        research_papers = []

        # ============================================================
        # STEP 1: LLM INITIAL REASONING
        # ============================================================
        workflow.logger.info("STEP 1: LLM Initial Reasoning")
        
        first_attempt = await workflow.execute_activity(
            mcp_call_llm,
            {"query": query, "_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=FAST_INTERNAL_RETRY,
        )
        
        response["llm_decision"]["used"] = True
        response["llm_decision"]["final_direct_answer"] = first_attempt.get("final", False)
        response["llm_decision"]["reason"] = first_attempt.get("reason", "")

        # ============================================================
        # DECISION: Can LLM answer directly?
        # ============================================================
        if first_attempt.get("final", False):
            # Path: Direct LLM answer (stable knowledge)
            workflow.logger.info("DECISION: Using direct LLM answer (stable knowledge)")
            response["execution_path"] = "llm"
            response["final_synthesis"]["status"] = "direct"
            response["final_synthesis"]["answer"] = first_attempt.get("answer", "")
            return response

        # ============================================================
        # Path: External tools required
        # ============================================================
        workflow.logger.info("DECISION: External tools required")
        response["execution_path"] = "tools"

        # ============================================================
        # STEP 2: TOOL DISCOVERY
        # ============================================================
        workflow.logger.info("STEP 2: Tool Discovery")
        
        tools = await workflow.execute_activity(
            mcp_discover_tools,
            {"_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=FAST_INTERNAL_RETRY,
        )
        
        response["tool_execution"]["tools_discovered"] = [
            tool.get("name") for tool in tools
        ]
        workflow.logger.info(f"Discovered {len(tools)} tools: {response['tool_execution']['tools_discovered']}")

        # ============================================================
        # STEP 3: TOOL SELECTION
        # ============================================================
        workflow.logger.info("STEP 3: Tool Selection")
        
        selection = await workflow.execute_activity(
            mcp_select_tools,
            {"query": query, "tools": tools, "_otel_ctx": _otel_ctx},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=FAST_INTERNAL_RETRY,
        )
        
        selected_tools = [tc.get("tool_name") for tc in selection.get("tool_calls", [])]
        workflow.logger.info(f"Selected {len(selected_tools)} tools: {selected_tools}")

        # ============================================================
        # STEP 4: TOOL EXECUTION (Independent Activities)
        # ============================================================
        workflow.logger.info("STEP 4: Tool Execution")
        
        for tool_call in selection.get("tool_calls", []):
            tool_name = tool_call.get("tool_name")
            if not tool_name:
                continue

            tool_input = tool_call.get("tool_input") or {}
            workflow.logger.info(f"Executing tool: {tool_name}")

            try:
                result = await workflow.execute_activity(
                    mcp_run_tool,
                    {"tool_name": tool_name, "tool_input": tool_input, "_otel_ctx": _otel_ctx},
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=TOOL_EXECUTION_RETRY,
                )
                
                raw_output = result.get("output", "")
                
                # Check if this is a research tool
                is_research_tool = self._is_research_tool(tool_name)
                
                if is_research_tool:
                    # Preserve structured research data
                    workflow.logger.info(f"Tool {tool_name}: Research tool detected, preserving structure")
                    structured_papers = self._parse_research_output(raw_output, tool_name)
                    
                    if structured_papers:
                        research_papers.extend(structured_papers)
                    
                    response["tool_execution"]["tools_called"].append({
                        "tool": tool_name,
                        "status": "success",
                        "input": tool_input,
                        "output_summary": {
                            "papers_found": len(structured_papers)
                        },
                        "reason": tool_call.get("reason", "")
                    })
                else:
                    # Non-research tools: extract important output
                    important_output = self._extract_important_output(raw_output, tool_name)
                    
                    response["tool_execution"]["tools_called"].append({
                        "tool": tool_name,
                        "status": "success",
                        "input": tool_input,
                        "important_output": important_output,
                        "reason": tool_call.get("reason", "")
                    })
                
                workflow.logger.info(f"Tool {tool_name}: SUCCESS")
                
            except ActivityError as e:
                response["tool_execution"]["tools_called"].append({
                    "tool": tool_name,
                    "status": "failed",
                    "input": tool_input,
                    "error": str(e),
                    "reason": tool_call.get("reason", "")
                })
                workflow.logger.warning(f"Tool {tool_name}: FAILED - {str(e)}")

        # ============================================================
        # STEP 5: FINAL SYNTHESIS
        # ============================================================
        workflow.logger.info("STEP 5: Final Synthesis")
        
        successful_tools = [
            tc for tc in response["tool_execution"]["tools_called"]
            if tc.get("status") == "success"
        ]
        
        if not successful_tools:
            workflow.logger.error("No successful tool outputs for synthesis")
            response["final_synthesis"]["status"] = "no_tools_succeeded"
            response["final_synthesis"]["answer"] = (
                "I could not retrieve external information to answer your query. "
                "All tool executions failed. Please check the tool execution details."
            )
            return response

        # Add research results if any research tools were used
        if research_papers:
            workflow.logger.info(f"Research results: {len(research_papers)} papers found")
            response["research_results"] = {
                "papers_found": len(research_papers),
                "papers": research_papers
            }

        # Prepare synthesis input
        # For research tools: just count, don't include full papers
        synthesis_input = {
            "query": query,
            "tool_results": []
        }
        
        for tc in successful_tools:
            tool_name = tc["tool"]
            
            if self._is_research_tool(tool_name):
                # Research tool: summarize count only
                papers_count = tc.get("output_summary", {}).get("papers_found", 0)
                synthesis_input["tool_results"].append({
                    "tool_name": tool_name,
                    "tool_input": tc["input"],
                    "output": f"Found {papers_count} research papers",
                    "status": "succeeded"
                })
            else:
                # Regular tool: use important output
                synthesis_input["tool_results"].append({
                    "tool_name": tool_name,
                    "tool_input": tc["input"],
                    "output": "\n".join(tc.get("important_output", [])),
                    "status": "succeeded"
                })

        # Try synthesis with LLM
        try:
            synthesis_input["_otel_ctx"] = _otel_ctx
            answer = await workflow.execute_activity(
                mcp_synthesize_answer,
                synthesis_input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=SYNTHESIS_RETRY,
            )
            
            response["final_synthesis"]["status"] = "success"
            response["final_synthesis"]["used_tool_outputs"] = [
                tc["tool"] for tc in successful_tools
            ]
            response["final_synthesis"]["answer"] = answer
            workflow.logger.info("Synthesis: SUCCESS")
            
        except ActivityError as e:
            # Synthesis failed - generate deterministic fallback
            workflow.logger.warning(f"Synthesis FAILED: {str(e)}, using fallback")
            
            fallback_answer = self._generate_fallback_answer(query, successful_tools)
            
            response["final_synthesis"]["status"] = "fallback"
            response["final_synthesis"]["fallback_reason"] = str(e)
            response["final_synthesis"]["used_tool_outputs"] = [
                tc["tool"] for tc in successful_tools
            ]
            response["final_synthesis"]["answer"] = fallback_answer

        return response

    def _is_research_tool(self, tool_name: str) -> bool:
        """Check if a tool is a research/academic tool."""
        research_keywords = [
            "arxiv",
            "research",
            "paper",
            "scholar",
            "academic",
            "publication"
        ]
        return any(keyword in tool_name.lower() for keyword in research_keywords)

    def _parse_research_output(self, raw_output: str, tool_name: str) -> list[dict]:
        """Parse research tool output into structured paper objects."""
        import json
        
        if not raw_output:
            return []
        
        try:
            # Try to parse as JSON first
            parsed = json.loads(raw_output)
            
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict):
                # Single paper
                return [parsed]
            else:
                workflow.logger.warning(f"Unexpected research output format: {type(parsed)}")
                return []
        except json.JSONDecodeError:
            # Try ast.literal_eval as fallback
            try:
                import ast
                parsed = ast.literal_eval(raw_output)
                
                if isinstance(parsed, list):
                    return parsed
                elif isinstance(parsed, dict):
                    return [parsed]
                else:
                    return []
            except Exception as e:
                workflow.logger.error(f"Failed to parse research output: {e}")
                return []

    def _extract_important_output(self, raw_output: str, tool_name: str) -> list[str]:
        """Extract key facts from raw tool output. Never dump full raw output."""
        if not raw_output:
            return ["No output returned"]
        
        # Limit output size
        if len(raw_output) > 2000:
            raw_output = raw_output[:2000]
        
        # Split into sentences/lines and extract meaningful ones
        lines = raw_output.split("\n")
        important = []
        
        for line in lines[:20]:  # Max 20 lines
            line = line.strip()
            if len(line) > 20 and len(line) < 500:  # Reasonable length
                important.append(line)
        
        if not important:
            # Fallback: just take a preview
            preview = raw_output[:500].strip()
            important.append(f"{preview}...")
        
        return important[:10]  # Max 10 important lines

    def _generate_fallback_answer(self, query: str, successful_tools: list[dict]) -> str:
        """Generate deterministic fallback answer when synthesis fails."""
        tool_names = [tc["tool"] for tc in successful_tools]
        
        # Check if any research tools were used
        research_tools = [tc for tc in successful_tools if self._is_research_tool(tc["tool"])]
        
        if research_tools:
            # For research tools, just mention paper count
            answer_parts = [f"Based on {', '.join(tool_names)} results:"]
            
            for tc in research_tools:
                papers_found = tc.get("output_summary", {}).get("papers_found", 0)
                answer_parts.append(f"\n{tc['tool']}: Found {papers_found} research papers")
            
            # Add non-research tools
            for tc in successful_tools:
                if not self._is_research_tool(tc["tool"]):
                    outputs = tc.get("important_output", [])
                    if outputs:
                        answer_parts.append(f"\n{tc['tool']}:")
                        for output in outputs[:3]:
                            answer_parts.append(f"  - {output}")
        else:
            # No research tools, use regular output
            answer_parts = [f"Based on {', '.join(tool_names)} results:"]
            
            for tc in successful_tools:
                outputs = tc.get("important_output", [])
                if outputs:
                    answer_parts.append(f"\n{tc['tool']}:")
                    for output in outputs[:3]:
                        answer_parts.append(f"  - {output}")
        
        return "\n".join(answer_parts)

