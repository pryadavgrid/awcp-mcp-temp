"""Laminar dual-export — attach a SECOND OTLP exporter to the EXISTING provider.

Laminar is OpenTelemetry-native: it ingests standard OTLP spans and renders the
LLM-specific ones (anything carrying gen_ai.* / token attributes) with token and
cost views, plus labeling queues for human approval. The radar already runs a
full OTel pipeline (awcp.observability.setup -> OTel Collector -> Tempo/Grafana),
so the correct integration is exporter FAN-OUT, not a second pipeline:

    TracerProvider (created once by setup_otel)
      ├─ BatchSpanProcessor -> OTLP -> local Collector -> Tempo/Grafana   (existing)
      └─ BatchSpanProcessor -> OTLP -> Laminar                            (added here)

Why NOT call Laminar's own SDK init (lmnr.Laminar.initialize)?
  * it installs its OWN global TracerProvider, and OpenTelemetry's global
    provider is a set-once singleton — whoever sets it second is silently
    ignored, so half the spans would vanish with no error;
  * fan-out needs zero new dependencies: the OTLP exporters are already in
    requirements for the Collector path.

Everything degrades gracefully:
  * no LMNR_PROJECT_API_KEY        -> skipped (local monitoring still works)
  * OTEL_ENABLED=false             -> global provider is a no-op Proxy -> skipped
  * exporter import/connect errors -> logged, never crash the radar
"""

from __future__ import annotations

import logging

from awcp.laminar import config

log = logging.getLogger("awcp.laminar")

_attached: bool = False        # idempotence guard (mirrors setup_otel's style)


def attach_laminar_exporter() -> bool:
    """Add the Laminar BatchSpanProcessor to the current global TracerProvider.

    Must be called AFTER setup_otel() has installed the real SDK provider
    (the radar guarantees this: api.py calls setup_otel at import, then
    init_laminar). Returns True when the exporter is attached."""
    global _attached
    if _attached:
        return True
    if not (config.ENABLED and config.PROJECT_API_KEY):
        log.info("laminar.exporter.skipped reason=%s",
                 "disabled" if not config.ENABLED else "no_api_key")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = trace.get_tracer_provider()
        # If OTEL_ENABLED=false, this is a ProxyTracerProvider with no
        # add_span_processor — nothing to fan out from, so skip cleanly.
        if not isinstance(provider, TracerProvider):
            log.info("laminar.exporter.skipped reason=no_sdk_provider "
                     "(is OTEL_ENABLED=true and setup_otel called?)")
            return False

        # Laminar authenticates OTLP with a Bearer project key.
        headers = (("authorization", f"Bearer {config.PROJECT_API_KEY}"),)

        exporter = None
        if config.OTLP_PROTOCOL == "grpc":
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter as _Grpc,
                )
                exporter = _Grpc(endpoint=config.OTLP_ENDPOINT, headers=headers)
            except Exception as exc:                     # fall through to HTTP
                log.warning("laminar.exporter.grpc_unavailable error=%r", exc)
        if exporter is None:
            # HTTP/protobuf fallback (also the configured choice when
            # LMNR_OTLP_PROTOCOL=http/protobuf): more robust than gRPC inside
            # long-running ASGI servers, where gRPC channels can silently stall.
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as _Http,
            )
            ep = config.OTLP_ENDPOINT.rstrip("/")
            if not ep.endswith("/v1/traces"):
                ep = f"{ep}/v1/traces"
            exporter = _Http(endpoint=ep, headers=dict(headers))

        provider.add_span_processor(BatchSpanProcessor(exporter))
        _attached = True
        log.info("laminar.exporter.attached endpoint=%s protocol=%s",
                 config.OTLP_ENDPOINT, config.OTLP_PROTOCOL)
        return True
    except Exception as exc:    # noqa: BLE001 — telemetry must never crash the radar
        log.warning("laminar.exporter.failed error=%r", exc, exc_info=True)
        return False


def exporter_attached() -> bool:
    """For /laminar/status and the dashboard."""
    return _attached
