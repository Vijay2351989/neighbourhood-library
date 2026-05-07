"""JSON-structured logging with trace correlation and a redaction utility.

The `library.*` modules continue using stdlib :mod:`logging` (``logger.info``,
``logger.exception`` etc.) — this module installs a formatter and a stream
handler so every record becomes a single JSON line on stderr, with the
active OTel trace context and the request-scoped contextvar values stamped
in. When ``OTEL_LOGS_EXPORTER=otlp`` is set, an additional OTel
``LoggingHandler`` is attached so the same records also flow through the
OTLP pipeline; the stderr emission stays on as a runtime-visible fallback.

PII handling is policy: never put raw emails / names / addresses into log
messages. The :func:`redact_email` helper masks an email when one truly has
to appear.
"""

from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging
import os
import sys
from typing import Final

from opentelemetry import trace

# Service name is read once at import; stable across the process. Falls back
# to a sensible default if the env var isn't set (during tests, scripts).
_SERVICE_NAME: Final[str] = os.environ.get("OTEL_SERVICE_NAME", "library-api")

# Per-request UUID stamped by the gRPC interceptor; read by the formatter.
# Lives in this module so the interceptor and the formatter share one source
# of truth without a circular import.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "library.request_id", default=None
)

# Standard LogRecord attributes we don't want to splat as "extra" fields.
_STD_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Formats LogRecord instances as one JSON object per line.

    Standard fields: ``ts``, ``level``, ``logger``, ``msg``, ``service.name``.
    When an OTel span is active, ``trace_id`` and ``span_id`` are stamped on.
    When the gRPC interceptor has set :data:`request_id_var`, ``request.id``
    is included. Any structured kwargs passed via ``logger.info("x", extra={...})``
    are merged in too.
    """

    def format(self, record: logging.LogRecord) -> str:
        # logging.Formatter.formatTime uses time.struct_time which can't
        # express microseconds, so %f comes through literal. Format via the
        # datetime API on record.created instead — gives a real ISO 8601 UTC
        # timestamp with ms precision.
        ts = (
            _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        payload: dict[str, object] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service.name": _SERVICE_NAME,
        }

        request_id = request_id_var.get()
        if request_id is not None:
            payload["request.id"] = request_id

        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is not None and ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Pull through any structured fields passed via ``logger.X("msg", extra={...})``.
        for key, value in record.__dict__.items():
            if key in _STD_RECORD_FIELDS or key.startswith("_") or key in payload:
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value

        return json.dumps(payload, default=str)


def redact_email(email: str) -> str:
    """Return ``j***@example.com`` for ``jane@example.com``.

    Use sparingly; default to logging member IDs instead. This exists for
    rare cases where a raw email genuinely has to appear in a log message
    (e.g., a debug pass investigating a specific reported issue).
    """

    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def configure_logging(level: int | str = logging.INFO) -> None:
    """Replace the root logger's handlers with the JSON-stderr handler.

    Idempotent — calling twice doesn't double-attach handlers. Designed to
    be called from :func:`library.observability.setup.init_telemetry` once
    at server startup.
    """

    root = logging.getLogger()
    root.setLevel(level)

    # Drop any pre-existing handlers (e.g., basicConfig from a previous run
    # or from a library that auto-configured logging on import).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


__all__ = [
    "JsonFormatter",
    "configure_logging",
    "redact_email",
    "request_id_var",
]
