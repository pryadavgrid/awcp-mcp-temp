"""Laminar exporter — two-path strategy for native Laminar dashboard support.

Preferred path (lmnr SDK installed):
  lmnr.Laminar.initialize() creates its own TracerProvider (lmnr_provider) and
  installs LLM SDK auto-instrumentors (OpenAI, Anthropic, etc.). Because
  setup_otel() already set a real TracerProvider, lmnr does NOT replace the
  global one — it only replaces a ProxyTracerProvider (sdk/tracing/__init__.py
  line 116-119). So the two providers coexist:

    lmnr_provider (lmnr-internal)
      └─ LaminarSpanProcessor → Laminar OTLP   (auto-instrumented LLM calls)

    global TracerProvider (setup_otel's)
      ├─ BatchSpanProcessor → local OTel Collector → Tempo/Grafana  (existing)
      └─ BatchSpanProcessor → Laminar HTTP OTLP                     (added here)

  Manual spans from bridge.py (laminar.token.usage) use the global tracer
  (trace.get_tracer("awcp.laminar")) → global TracerProvider → both local OTel
  AND Laminar via the second processor we add.

Fallback path (lmnr not installed):
  Manual OTLP fan-out only: adds a second BatchSpanProcessor to the existing
  global TracerProvider. No LLM auto-instrumentation — only bridge.py spans.
  Also fixes the original bug: HTTP fallback now uses port 443 (not 8443).
"""

from __future__ import annotations

import logging

from awcp.laminar import config

log = logging.getLogger("awcp.laminar")

_attached: bool = False


def _is_llm_or_governance_span(span) -> bool:
    """True for the spans Laminar actually renders value for: the LLM/token
    usage spans, AWCP governance spans, and the task-lifecycle request spans
    (config.EXPORT_KEEP_SPAN_NAMES) that PARENT the token spans — keeping the
    latter is what lets `laminar.token.usage` nest inside its full
    `POST /tasks/execution/...` trace instead of arriving as an orphan row.
    Everything else (GET /agents and other high-frequency polling spans) is HTTP
    noise that buries the token data, so it is dropped from the Laminar fan-out
    only (it still reaches Tempo/Grafana)."""
    name = getattr(span, "name", "") or ""
    if name == "laminar.token.usage" or name.startswith("awcp"):
        return True
    # Keep the configured task-lifecycle request spans (and their ASGI
    # http-receive/http-send children, which share the substring) so the token
    # span's parent trace is present and it renders as a full tree.
    if any(keep in name for keep in config.EXPORT_KEEP_SPAN_NAMES):
        return True
    attrs = getattr(span, "attributes", None) or {}
    try:
        if attrs.get("lmnr.span.type"):
            return True
        return any(str(k).startswith("gen_ai.") for k in attrs)
    except Exception:  # noqa: BLE001 — attribute view edge cases must not drop spans
        return True


def _laminar_processor(exporter):
    """Build the span processor for the Laminar fan-out: a BatchSpanProcessor
    that drops non-LLM spans, unless LMNR_EXPORT_ONLY_LLM=false.

    We SUBCLASS BatchSpanProcessor (rather than wrap it) so every SpanProcessor
    lifecycle hook the SDK calls stays intact — including private ones like
    `_on_ending`, which newer opentelemetry-sdk releases invoke on each processor
    when a span ends. Only `on_end` is overridden, to apply the filter."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if not config.EXPORT_ONLY_LLM:
        return BatchSpanProcessor(exporter)

    class _FilteringBatchSpanProcessor(BatchSpanProcessor):
        def on_end(self, span) -> None:
            if _is_llm_or_governance_span(span):
                super().on_end(span)

    log.info("laminar.exporter.filter on (LLM/governance spans only — set "
             "LMNR_EXPORT_ONLY_LLM=false to send all spans)")
    return _FilteringBatchSpanProcessor(exporter)


def attach_laminar_exporter() -> bool:
    """Attach Laminar as a span destination. Idempotent, never raises."""
    global _attached
    if _attached:
        return True
    if not (config.ENABLED and config.PROJECT_API_KEY):
        log.info("laminar.exporter.skipped reason=%s",
                 "disabled" if not config.ENABLED else "no_api_key")
        return False

    try:
        return _attach_via_lmnr_sdk()
    except ImportError:
        log.info("laminar.exporter lmnr not installed — using manual OTLP fan-out "
                 "(pip install lmnr for native LLM auto-instrumentation)")
    except Exception as exc:
        log.warning("laminar.exporter.lmnr_failed error=%r — falling back to manual OTLP", exc,
                    exc_info=True)

    return _attach_manual_otlp()


def _attach_via_lmnr_sdk() -> bool:
    """Native Laminar integration via the lmnr SDK.

    lmnr.Laminar.initialize() sets up its own provider with LLM auto-
    instrumentors (every openai/anthropic/etc. call is automatically traced to
    Laminar). We then add a Laminar OTLP exporter to the existing global
    provider so the radar's manual bridge.py spans also reach Laminar."""
    global _attached

    from urllib.parse import urlparse
    from lmnr import Laminar  # ImportError propagates to caller → triggers fallback

    # Parse the configured gRPC endpoint to pass the right host/ports to the
    # lmnr SDK (default base_url is api.lmnr.ai which is wrong for self-hosted).
    parsed = urlparse(config.OTLP_ENDPOINT)
    base_url = f"{parsed.scheme}://{parsed.hostname}" if parsed.hostname else None
    grpc_port = parsed.port or (8443 if (parsed.scheme or "https") == "https" else 8881)
    parsed_http = urlparse(config.OTLP_HTTP_ENDPOINT)
    http_port = parsed_http.port or (443 if (parsed_http.scheme or "https") == "https" else 8880)

    # A plaintext http:// endpoint means a SELF-HOSTED Laminar without TLS (the
    # lmnr docker stack: app-server gRPC→:8881, HTTP→:8880). The SDK's default
    # gRPC exporter assumes TLS, so against a plaintext server it silently drops
    # every span. Force the HTTP OTLP exporter instead — the app-server accepts
    # OTLP/HTTP at :8880/v1/traces. Laminar Cloud (https) keeps the gRPC path.
    force_http = (parsed.scheme or "https") == "http"

    # Creates lmnr_provider + installs LLM instrumentors. Does NOT replace the
    # global TracerProvider because setup_otel() already set a real SDK provider
    # (lmnr only replaces a ProxyTracerProvider).
    Laminar.initialize(
        project_api_key=config.PROJECT_API_KEY,
        base_url=base_url,
        grpc_port=grpc_port,
        http_port=http_port,
        force_http=force_http,
    )
    log.info("laminar.exporter.lmnr_init endpoint=%s force_http=%s", base_url, force_http)

    # Add a Laminar HTTP exporter to the GLOBAL provider so the radar's manual
    # spans (bridge.py's laminar.token.usage) also appear in Laminar.
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpExporter,
        )

        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            ep = config.OTLP_HTTP_ENDPOINT.rstrip("/") + "/v1/traces"
            provider.add_span_processor(
                _laminar_processor(
                    HttpExporter(
                        endpoint=ep,
                        headers={"authorization": f"Bearer {config.PROJECT_API_KEY}"},
                    )
                )
            )
            log.info("laminar.exporter.manual_spans_to_laminar endpoint=%s", ep)
    except Exception as exc:
        log.warning("laminar.exporter.add_manual_failed error=%r (lmnr_provider still active)", exc)

    _attached = True
    log.info("laminar.exporter.attached via=lmnr_sdk")
    return True


def _attach_manual_otlp() -> bool:
    """Fallback: fan-out the existing TracerProvider to Laminar via raw OTLP.

    No LLM auto-instrumentation. Only manually created spans reach Laminar.
    Fixes the original HTTP fallback bug (was using port 8443 instead of 443)."""
    global _attached

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            log.info("laminar.exporter.skipped reason=no_sdk_provider "
                     "(is OTEL_ENABLED=true and setup_otel called?)")
            return False

        headers = (("authorization", f"Bearer {config.PROJECT_API_KEY}"),)

        exporter = None
        if config.OTLP_PROTOCOL == "grpc":
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter as _Grpc,
                )
                exporter = _Grpc(endpoint=config.OTLP_ENDPOINT, headers=headers)
            except Exception as exc:
                log.warning("laminar.exporter.grpc_unavailable error=%r", exc)

        if exporter is None:
            # HTTP fallback uses OTLP_HTTP_ENDPOINT (port 443), NOT the gRPC
            # endpoint (port 8443) — that was the original bug in this path.
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as _Http,
            )
            ep = config.OTLP_HTTP_ENDPOINT.rstrip("/") + "/v1/traces"
            exporter = _Http(endpoint=ep, headers=dict(headers))

        provider.add_span_processor(_laminar_processor(exporter))
        _attached = True
        log.info("laminar.exporter.attached via=manual_otlp endpoint=%s protocol=%s",
                 config.OTLP_ENDPOINT, config.OTLP_PROTOCOL)
        return True
    except Exception as exc:
        log.warning("laminar.exporter.failed error=%r", exc, exc_info=True)
        return False


def exporter_attached() -> bool:
    """For /laminar/status and the dashboard."""
    return _attached
