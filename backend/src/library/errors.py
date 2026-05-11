"""Domain errors and a gRPC-status mapping decorator.

The service layer raises the typed exceptions defined here; the servicer
applies :func:`map_domain_errors` to translate them into the matching
``grpc.StatusCode`` via ``context.abort``. Two layering benefits:

1. Services and repositories never import grpc — they speak in domain
   exceptions, and the boundary translation lives in exactly one place.
2. The servicer methods read as pure proto-in/proto-out glue. Error mapping
   is declarative (a decorator) instead of try/except scattered throughout.

Phase 5.6 extension: post-retry transient infrastructure errors (deadlock,
serialization, lock-timeout, connection-drop, pool-timeout, statement-
timeout) are mapped to ``UNAVAILABLE`` / ``RESOURCE_EXHAUSTED`` so well-
behaved clients can retry. ``record_exception`` runs in both branches so
the trace UI shows the failure.

See [docs/design/02-api-contract.md §2](../../docs/design/02-api-contract.md)
for the canonical failure -> status table.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import grpc
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger("library.errors")


class DomainError(Exception):
    """Base for all errors that should map to a gRPC status (not INTERNAL)."""


class NotFound(DomainError):
    """Resource lookup by ID returned no rows. -> ``grpc.StatusCode.NOT_FOUND``."""


class AlreadyExists(DomainError):
    """Insert violated a uniqueness constraint. -> ``grpc.StatusCode.ALREADY_EXISTS``."""


class InvalidArgument(DomainError):
    """Caller-supplied input failed validation. -> ``grpc.StatusCode.INVALID_ARGUMENT``."""


class FailedPrecondition(DomainError):
    """Operation is structurally impossible in the current state.

    -> ``grpc.StatusCode.FAILED_PRECONDITION``. Examples: borrowing when no
    copies are available, returning an already-returned loan, dropping a
    book's copy count below the number currently borrowed.
    """


_DOMAIN_TO_GRPC_STATUS: dict[type[DomainError], grpc.StatusCode] = {
    NotFound: grpc.StatusCode.NOT_FOUND,
    AlreadyExists: grpc.StatusCode.ALREADY_EXISTS,
    InvalidArgument: grpc.StatusCode.INVALID_ARGUMENT,
    FailedPrecondition: grpc.StatusCode.FAILED_PRECONDITION,
}


def _map_transient_class_to_grpc(error_class):  # type: ignore[no-untyped-def]
    """Translate a :class:`library.resilience.ErrorClass` to a gRPC status.

    Imported lazily inside the function to avoid a load-order coupling
    between ``errors`` and ``resilience``. Returns ``None`` if the class is
    not infrastructure-transient (e.g. it's INTEGRITY or DOMAIN).
    """

    from library.resilience.classify import ErrorClass

    if error_class is ErrorClass.POOL_TIMEOUT:
        return grpc.StatusCode.RESOURCE_EXHAUSTED
    if error_class in {
        ErrorClass.DEADLOCK,
        ErrorClass.SERIALIZATION,
        ErrorClass.LOCK_TIMEOUT,
        ErrorClass.CONNECTION_DROPPED,
        ErrorClass.STATEMENT_TIMEOUT,
    }:
        return grpc.StatusCode.UNAVAILABLE
    return None


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def map_domain_errors(fn: F) -> F:
    """Decorate a servicer coroutine so domain errors become gRPC status codes.

    The wrapped function is expected to have the standard async-servicer
    signature ``async def Method(self, request, context)``. Any
    :class:`DomainError` subclass raised within is converted to the
    corresponding ``StatusCode`` via ``context.abort`` (which raises
    :class:`grpc.aio.AioRpcError` after sending the trailers). Any other
    exception is logged with full traceback and surfaced as ``INTERNAL`` so
    we never leak unfiltered Python error messages to clients.
    """

    @functools.wraps(fn)
    async def wrapper(self: Any, request: Any, context: grpc.aio.ServicerContext) -> Any:
        try:
            return await fn(self, request, context)
        except DomainError as exc:
            # Mark the active span as errored so trace UIs render it red and
            # the stack trace is queryable. We call `record_exception` on the
            # domain error specifically because operators want to see "what
            # rule was violated" without having to grep logs for a request id.
            span = trace.get_current_span()
            if span is not None and span.is_recording():
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
            status = _DOMAIN_TO_GRPC_STATUS.get(type(exc), grpc.StatusCode.UNKNOWN)
            await context.abort(status, str(exc))
        except grpc.aio.AioRpcError:
            # context.abort raises this; let it propagate untouched so the
            # already-set status reaches the client.
            raise
        except Exception as exc:
            # Phase 5.6: classify post-retry transient errors and surface
            # them with a meaningful gRPC status (UNAVAILABLE for transient
            # DB issues, RESOURCE_EXHAUSTED for pool exhaustion). Anything
            # else is a real bug → INTERNAL.
            from library.resilience.classify import classify

            cls = classify(exc)
            grpc_status = _map_transient_class_to_grpc(cls)

            span = trace.get_current_span()
            if span is not None and span.is_recording():
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)

            # Two mutually exclusive paths: a classified transient infra
            # error maps to UNAVAILABLE/RESOURCE_EXHAUSTED, anything else
            # is a real bug → INTERNAL. We use an explicit if/else (rather
            # than relying on context.abort() raising AbortError to skip
            # the next block) so the control flow stays correct even when
            # context is mocked in tests or the grpc-aio contract shifts.
            if grpc_status is not None:
                # Don't dump traceback at WARNING — these are expected under
                # load. INFO with the classification keeps logs readable.
                logger.info(
                    "transient %s in %s mapped to %s",
                    cls.value,
                    fn.__qualname__,
                    grpc_status.name,
                )
                await context.abort(grpc_status, f"{cls.value}: {exc}")
            else:
                logger.exception("uncaught error in %s", fn.__qualname__)
                await context.abort(grpc.StatusCode.INTERNAL, "internal error")

    return wrapper  # type: ignore[return-value]


__all__ = [
    "AlreadyExists",
    "DomainError",
    "FailedPrecondition",
    "InvalidArgument",
    "NotFound",
    "map_domain_errors",
]
