"""Domain errors and a gRPC-status mapping decorator.

The service layer raises the typed exceptions defined here; the servicer
applies :func:`map_domain_errors` to translate them into the matching
``grpc.StatusCode`` via ``context.abort``. Two layering benefits:

1. Services and repositories never import grpc — they speak in domain
   exceptions, and the boundary translation lives in exactly one place.
2. The servicer methods read as pure proto-in/proto-out glue. Error mapping
   is declarative (a decorator) instead of try/except scattered throughout.

See [docs/design/02-api-contract.md §2](../../docs/design/02-api-contract.md)
for the canonical failure -> status table.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import grpc

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
            status = _DOMAIN_TO_GRPC_STATUS.get(type(exc), grpc.StatusCode.UNKNOWN)
            await context.abort(status, str(exc))
        except grpc.aio.AioRpcError:
            # context.abort raises this; let it propagate untouched so the
            # already-set status reaches the client.
            raise
        except Exception:
            # Anything we don't recognize is a bug. Log with traceback and
            # surface as INTERNAL with a generic message.
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
