"""
AWCP OTel Middleware & Instrumented Wrappers
============================================
Provides:
  - instrument_fastapi(app) : Auto-trace all HTTP routes
  - instrument_requests()   : Auto-trace Ollama HTTP calls (requests lib)
  - AWCPMetrics             : All custom business metrics for this project
  - span_context()          : Convenience context manager for manual spans
"""

import time
import logging
from contextlib import contextmanager
from typing import Optional

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.trace import Status, StatusCode

from awcp.observability.setup import get_tracer, get_meter

logger = logging.getLogger(__name__)


# FastAPI Auto-Instrumentation


def instrument_fastapi(app) -> None:
    """
    Call this after creating your FastAPI app.
    Auto-creates a trace span for EVERY HTTP request with:
      - http.method, http.route, http.status_code
      - Duration histogram
    """
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,metrics",  # Don't trace health checks
    )
    logger.info("[OTel] FastAPI instrumented — all routes will be traced")


def instrument_requests() -> None:
    """
    Auto-traces all `requests` library calls (i.e., Ollama HTTP calls).
    Every ask_ollama() call will appear as a child span automatically.
    """
    RequestsInstrumentor().instrument()
    logger.info("[OTel] requests library instrumented — Ollama calls will be traced")



# Custom Business Metrics


class AWCPMetrics:
    """
    All custom Prometheus metrics for the AWCP project.
    Instantiate once per process and pass around.

    Usage:
        metrics = AWCPMetrics()
        metrics.record_ask_request(duration=4.2, status="success")
        metrics.record_tool_call(tool_name="web_search", status="success", duration=3.1)
    """

    def __init__(self):
        meter = get_meter("awcp.business")

        # ── /ask endpoint metrics ───────────────────────────────────────────
        self.ask_requests_total = meter.create_counter(
            name="awcp.ask.requests.total",
            description="Total number of /ask requests received",
            unit="1",
        )
        self.ask_duration = meter.create_histogram(
            name="awcp.ask.duration.seconds",
            description="End-to-end /ask request duration in seconds",
            unit="s",
        )

        # ── Workflow metrics ────────────────────────────────────────────────
        self.workflow_started = meter.create_counter(
            name="awcp.workflow.started.total",
            description="Total Temporal workflows started",
            unit="1",
        )
        self.workflow_completed = meter.create_counter(
            name="awcp.workflow.completed.total",
            description="Total Temporal workflows completed",
            unit="1",
        )
        self.workflow_duration = meter.create_histogram(
            name="awcp.workflow.duration.seconds",
            description="DynamicAskWorkflow total duration",
            unit="s",
        )

        # ── Activity metrics ────────────────────────────────────────────────
        self.activity_duration = meter.create_histogram(
            name="awcp.activity.duration.seconds",
            description="Duration of each Temporal activity",
            unit="s",
        )
        self.activity_failures = meter.create_counter(
            name="awcp.activity.failures.total",
            description="Total Temporal activity failures",
            unit="1",
        )

        # ── Tool metrics ────────────────────────────────────────────────────
        self.tool_calls_total = meter.create_counter(
            name="awcp.tool.calls.total",
            description="Total tool executions",
            unit="1",
        )
        self.tool_duration = meter.create_histogram(
            name="awcp.tool.duration.seconds",
            description="Tool execution duration",
            unit="s",
        )
        self.tool_failures = meter.create_counter(
            name="awcp.tool.failures.total",
            description="Total tool execution failures",
            unit="1",
        )
        self.tool_result_size = meter.create_histogram(
            name="awcp.tool.result.chars",
            description="Size of tool output in characters",
            unit="char",
        )

        # ── LLM (Ollama) metrics ────────────────────────────────────────────
        self.ollama_calls_total = meter.create_counter(
            name="awcp.ollama.calls.total",
            description="Total Ollama LLM calls",
            unit="1",
        )
        self.ollama_duration = meter.create_histogram(
            name="awcp.ollama.duration.seconds",
            description="Ollama LLM response latency",
            unit="s",
        )
        self.ollama_failures = meter.create_counter(
            name="awcp.ollama.failures.total",
            description="Total Ollama call failures",
            unit="1",
        )

        # ── LLM Decision metrics ────────────────────────────────────────────
        self.llm_decision = meter.create_counter(
            name="awcp.llm.decision.total",
            description="LLM routing decision: direct vs tools",
            unit="1",
        )

        # ── MCP subprocess metrics ──────────────────────────────────────────
        self.mcp_spawn_total = meter.create_counter(
            name="awcp.mcp.subprocess.spawns.total",
            description="Total MCP server subprocess spawns",
            unit="1",
        )
        self.mcp_call_duration = meter.create_histogram(
            name="awcp.mcp.call.duration.seconds",
            description="Duration of a single MCP tool call (including subprocess spawn)",
            unit="s",
        )

    # ── Convenience recording methods ──────────────────────────────────────

    def record_ask_request(self, duration: float, status: str, execution_path: str = "unknown"):
        self.ask_requests_total.add(1, {"status": status, "execution_path": execution_path})
        self.ask_duration.record(duration, {"status": status, "execution_path": execution_path})

    def record_workflow(self, duration: float, status: str):
        self.workflow_completed.add(1, {"status": status})
        self.workflow_duration.record(duration, {"status": status})

    def record_activity(self, activity_name: str, duration: float, status: str):
        self.activity_duration.record(duration, {"activity": activity_name, "status": status})
        if status == "failed":
            self.activity_failures.add(1, {"activity": activity_name})

    def record_tool_call(self, tool_name: str, duration: float, status: str, result_size: int = 0):
        self.tool_calls_total.add(1, {"tool": tool_name, "status": status})
        self.tool_duration.record(duration, {"tool": tool_name})
        if result_size > 0:
            self.tool_result_size.record(result_size, {"tool": tool_name})
        if status == "failed":
            self.tool_failures.add(1, {"tool": tool_name})

    def record_ollama_call(self, model: str, duration: float, status: str):
        self.ollama_calls_total.add(1, {"model": model, "status": status})
        self.ollama_duration.record(duration, {"model": model, "status": status})
        if status == "failed":
            self.ollama_failures.add(1, {"model": model})

    def record_llm_decision(self, decision: str):
        """decision = 'direct' or 'tools'"""
        self.llm_decision.add(1, {"decision": decision})

    def record_mcp_call(self, tool_name: str, duration: float):
        self.mcp_spawn_total.add(1, {"tool": tool_name})
        self.mcp_call_duration.record(duration, {"tool": tool_name})



# Manual Span Helpers


@contextmanager
def span_context(tracer_name: str, span_name: str, attributes: Optional[dict] = None):
    """
    Convenience context manager for creating a manual OTel span.

    Usage:
        with span_context("awcp.mcp", "call_llm", {"query": query}) as span:
            result = do_something()
            span.set_attribute("result_size", len(result))
    """
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, str(value)[:256])  # OTel attr limit
        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def get_current_trace_id() -> str:
    """Extract the current trace ID as a hex string (useful for log correlation)."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return "no-trace"


def get_current_span_id() -> str:
    """Extract the current span ID as a hex string."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.span_id, "016x")
    return "no-span"
