"""gRPC server interceptor for request-context + access logging.

Per :doc:`../../../docs/design/06-observability.md` §6.1, a single interceptor
is responsible for:

1. Generating ``request.id = uuid4()`` per RPC.
2. Stamping it on the active span and on :data:`logging_config.request_id_var`
   so every log line within the request carries it.
3. Emitting one INFO access-log line at end-of-call with method, status,
   duration, peer, and request id.

The OTel gRPC auto-instrumentation creates the root span; this interceptor
attaches *attributes* to it. Order matters — register OTel's interceptor
before this one in the server's interceptor list.
"""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

import grpc
from grpc.aio import ServerInterceptor
from opentelemetry import trace

from library.observability.logging_config import request_id_var

logger = logging.getLogger("library.access")


class RequestContextInterceptor(ServerInterceptor):
    """Stamps a request id, propagates it to logs/spans, and emits an access log."""

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        # Resolve the inner handler; we wrap whichever method type it is so
        # we can capture status + duration around the actual call.
        handler = await continuation(handler_call_details)
        method = handler_call_details.method  # e.g. "/library.v1.LibraryService/BorrowBook"

        if handler.unary_unary is not None:
            inner = handler.unary_unary
            return grpc.unary_unary_rpc_method_handler(
                self._wrap(inner, method),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        # Streaming handlers aren't used in this service today, but if one
        # ever appears, fall through unwrapped rather than break the call.
        return handler

    def _wrap(
        self,
        inner: Callable[..., Any],
        method: str,
    ) -> Callable[[Any, grpc.aio.ServicerContext], Awaitable[Any]]:
        async def wrapper(request: Any, context: grpc.aio.ServicerContext) -> Any:
            request_id = uuid.uuid4().hex
            token = request_id_var.set(request_id)

            # Stamp on the active root span (from OTel grpc instrumentation).
            span = trace.get_current_span()
            if span is not None and span.get_span_context().is_valid:
                span.set_attribute("request.id", request_id)
                span.set_attribute("rpc.method.short", method.rsplit("/", 1)[-1])

            start = time.perf_counter()
            status_name = "OK"
            try:
                # Some servicers (notably grpc_health.HealthServicer) ship
                # sync handlers; aio.server tolerates both. Detect the result
                # type and only await when needed so we don't TypeError on
                # a plain proto response.
                result = inner(request, context)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except grpc.aio.AioRpcError as exc:
                # context.abort already raised this with its status set.
                status_name = exc.code().name if exc.code() else "UNKNOWN"
                raise
            except Exception:
                status_name = "INTERNAL"
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                # Skip the noisy reflection / health traffic — it's not
                # interesting for ops dashboards and gets fired every few
                # seconds by Compose's grpc_health_probe.
                if not (
                    method.startswith("/grpc.reflection.")
                    or method.startswith("/grpc.health.")
                ):
                    logger.info(
                        "rpc",
                        extra={
                            "rpc.method": method,
                            "rpc.status": status_name,
                            "rpc.duration_ms": round(duration_ms, 2),
                            "peer": _safe_peer(context),
                        },
                    )
                request_id_var.reset(token)

        return wrapper


def _safe_peer(context: grpc.aio.ServicerContext) -> str:
    """Best-effort peer-address extraction; returns ``"unknown"`` on failure."""

    try:
        return context.peer() or "unknown"
    except Exception:  # pragma: no cover - context surface is environment-dependent
        return "unknown"


__all__ = ["RequestContextInterceptor"]
