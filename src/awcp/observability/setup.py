"""
AWCP OpenTelemetry Setup
========================
Call setup_otel("service-name") once at the top of each entrypoint:
  - control/api.py         → setup_otel("awcp-control-api")
  - temporal/worker/run_worker.py → setup_otel("awcp-temporal-worker")
  - mcp/server.py          → setup_otel("awcp-mcp-server")

All telemetry is sent to the OTel Collector at OTEL_EXPORTER_OTLP_ENDPOINT
(default: http://localhost:4317 via gRPC).
"""

import logging
import os

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

# OTel Logs bridge
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor

logger = logging.getLogger(__name__)

# Read from environment — default points to local OTel Collector
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() == "true"


def setup_otel(service_name: str, service_version: str = "1.0.0") -> None:
    """
    Initialize OpenTelemetry SDK for the given service.
    Sets up Traces, Metrics, and Logs exporters pointing at the OTel Collector.
    Safe to call multiple times — subsequent calls are no-ops if already configured.
    """
    if not OTEL_ENABLED:
        logger.info("[OTel] Disabled via OTEL_ENABLED=false, skipping setup.")
        return

    logger.info(
        "[OTel] Setting up for service=%s endpoint=%s",
        service_name,
        OTEL_ENDPOINT,
    )

    # ── Shared Resource (labels attached to ALL telemetry from this process) ──
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: service_version,
            "deployment.environment": os.getenv("AWCP_ENV", "development"),
            "host.name": os.uname().nodename,
        }
    )

    # 1. TRACES

    tracer_provider = TracerProvider(resource=resource)

    otlp_span_exporter = OTLPSpanExporter(
        endpoint=OTEL_ENDPOINT,
        insecure=True,  # No TLS for local dev
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(otlp_span_exporter))
    trace.set_tracer_provider(tracer_provider)

    
    # 2. METRICS
    
    otlp_metric_exporter = OTLPMetricExporter(
        endpoint=OTEL_ENDPOINT,
        insecure=True,
    )
    metric_reader = PeriodicExportingMetricReader(
        exporter=otlp_metric_exporter,
        export_interval_millis=15_000,  # Export every 15 seconds
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    
    # 3. LOGS (bridges Python logging → OTel)
    
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=OTEL_ENDPOINT, insecure=True)
        )
    )
    set_logger_provider(logger_provider)

    # This injects trace_id + span_id into every Python log record automatically
    LoggingInstrumentor().instrument(set_logging_format=True)

    logger.info("[OTel] Setup complete for service=%s", service_name)


def get_tracer(name: str):
    """Convenience wrapper — get a tracer for a module."""
    return trace.get_tracer(name)


def get_meter(name: str):
    """Convenience wrapper — get a meter for a module."""
    return metrics.get_meter(name)
