"""OpenTelemetry SDK initialization for the library backend.

Reads the standard ``OTEL_*`` env vars (see ``docs/design/06-observability.md``
§8.4) and configures the global :class:`TracerProvider` and
:class:`LoggerProvider`. Auto-instrumentation for grpc.aio (server),
SQLAlchemy, and asyncpg is wired up here so every RPC and SQL query produces
a span without per-call-site changes.

Default behavior (Phase 5.5): both traces and logs go to the console
exporter (stdout). Switching to OTLP is a matter of env vars — the SDK
re-resolves the exporter based on ``OTEL_TRACES_EXPORTER`` /
``OTEL_LOGS_EXPORTER`` / ``OTEL_EXPORTER_OTLP_ENDPOINT``.

This module is **import-safe** — calling the helpers is required to actually
wire anything up. Tests opt out by simply not calling ``init_telemetry``,
or call it with an in-memory exporter instead.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.grpc import aio_server_interceptor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

from library.observability.logging_config import configure_logging

logger = logging.getLogger("library.observability.setup")


@dataclass(slots=True)
class TelemetryHandles:
    """Return value of :func:`init_telemetry` exposing shutdown hooks.

    The server's drain path should call :meth:`shutdown` to flush any
    pending OTLP batches before the process exits.
    """

    tracer_provider: TracerProvider
    logger_provider: LoggerProvider

    def shutdown(self) -> None:
        try:
            self.tracer_provider.shutdown()
        except Exception:  # pragma: no cover - best effort
            logger.debug("tracer provider shutdown raised", exc_info=True)
        try:
            self.logger_provider.shutdown()
        except Exception:  # pragma: no cover - best effort
            logger.debug("logger provider shutdown raised", exc_info=True)


def init_telemetry() -> TelemetryHandles:
    """Wire up OTel: set providers, install exporters, run instrumentations.

    Reads standard ``OTEL_*`` env vars from the process environment (which
    Compose populates from the api service's env block). Returns handles so
    the caller can shut down providers cleanly during graceful drain.
    """

    configure_logging(level=_resolve_log_level())

    resource = Resource.create(
        attributes={
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "library-api"),
        }
    )
    # OTel's Resource also picks up OTEL_RESOURCE_ATTRIBUTES automatically when
    # constructed via Resource.create; the explicit service.name above just
    # ensures a sensible default if the var isn't set.

    tracer_provider = _build_tracer_provider(resource)
    trace.set_tracer_provider(tracer_provider)

    logger_provider = _build_logger_provider(resource)
    set_logger_provider(logger_provider)

    # Attach an OTel LoggingHandler so the same records that flow to the JSON
    # stderr handler also become OTel log records (with trace context). The
    # exporter inside the LoggerProvider decides whether they go anywhere
    # remote — in console mode the OTel side just prints; in otlp mode it
    # ships to the configured endpoint.
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    )

    # Auto-instrumentation. SQLAlchemy and asyncpg patch globally; the gRPC
    # server interceptor is returned so the caller can include it in the
    # server's interceptor list (must come *before* our request-context
    # interceptor so the OTel root span exists when we stamp request.id).
    SQLAlchemyInstrumentor().instrument()
    AsyncPGInstrumentor().instrument()

    logger.info(
        "telemetry initialized",
        extra={
            "traces_exporter": os.environ.get("OTEL_TRACES_EXPORTER", "console"),
            "logs_exporter": os.environ.get("OTEL_LOGS_EXPORTER", "console"),
            "otlp_endpoint": os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        },
    )

    return TelemetryHandles(
        tracer_provider=tracer_provider, logger_provider=logger_provider
    )


def grpc_otel_server_interceptor():
    """Return the OTel-provided gRPC aio server interceptor.

    Exposed as a function so callers don't have to know the import path.
    Must be registered *before* :class:`RequestContextInterceptor` in the
    server's interceptor list — OTel creates the root span; our interceptor
    decorates it.
    """

    return aio_server_interceptor()


# ---- helpers ----


def _build_tracer_provider(resource: Resource) -> TracerProvider:
    provider = TracerProvider(resource=resource)
    exporter_kind = os.environ.get("OTEL_TRACES_EXPORTER", "console").lower()
    if exporter_kind == "otlp":
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    elif exporter_kind == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter_kind == "none":
        pass  # tracing disabled
    else:
        logger.warning(
            "unknown OTEL_TRACES_EXPORTER=%r, defaulting to console",
            exporter_kind,
        )
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    return provider


def _build_logger_provider(resource: Resource) -> LoggerProvider:
    provider = LoggerProvider(resource=resource)
    exporter_kind = os.environ.get("OTEL_LOGS_EXPORTER", "console").lower()
    if exporter_kind == "otlp":
        provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter())
        )
    elif exporter_kind == "console":
        provider.add_log_record_processor(
            BatchLogRecordProcessor(ConsoleLogExporter())
        )
    elif exporter_kind == "none":
        pass
    else:
        logger.warning(
            "unknown OTEL_LOGS_EXPORTER=%r, defaulting to console",
            exporter_kind,
        )
        provider.add_log_record_processor(
            BatchLogRecordProcessor(ConsoleLogExporter())
        )
    return provider


def _resolve_log_level() -> int:
    raw = os.environ.get("LIBRARY_LOG_LEVEL") or os.environ.get(
        "OTEL_LOG_LEVEL", "INFO"
    )
    try:
        return logging.getLevelName(raw.upper())
    except Exception:  # pragma: no cover - defensive
        return logging.INFO


__all__ = ["TelemetryHandles", "grpc_otel_server_interceptor", "init_telemetry"]
