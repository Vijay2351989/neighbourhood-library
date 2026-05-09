"""Classify raw DB / pool exceptions into a small enum the retry layer reads.

Phase 5.6 — see ``docs/phases/phase-5-6-resilience.md`` §"Design decisions D4".

The classifier dispatches on:

* SQLAlchemy exception type (``OperationalError`` / ``IntegrityError`` /
  ``TimeoutError`` / generic ``DBAPIError``).
* For SQLAlchemy-wrapped errors, the ``orig`` attribute (the underlying
  driver exception, almost always an ``asyncpg`` exception class).
* The Postgres ``sqlstate`` code on asyncpg errors when one is present.

It deliberately does **not** match against exception message strings — those
shift across PG versions and would silently break under upgrade.
"""

from __future__ import annotations

import asyncio
import enum
from typing import Final

from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    OperationalError,
)
from sqlalchemy.exc import TimeoutError as SQLAlchemyPoolTimeout

# asyncpg raises typed exceptions; SQLAlchemy wraps them in DBAPIError but
# preserves the original at ``.orig``. Importing the structured exceptions
# lets us isinstance-check rather than string-match sqlstate.
try:  # pragma: no cover - asyncpg is always installed in app contexts
    from asyncpg import exceptions as _apg_exc

    _APG_DEADLOCK = (_apg_exc.DeadlockDetectedError,)
    _APG_SERIALIZATION = (_apg_exc.SerializationError,)
    _APG_LOCK_NOT_AVAILABLE = (_apg_exc.LockNotAvailableError,)
    _APG_QUERY_CANCELED = (_apg_exc.QueryCanceledError,)
    _APG_CONNECTION = (
        _apg_exc.ConnectionDoesNotExistError,
        _apg_exc.ConnectionFailureError,
        _apg_exc.InterfaceError,
        _apg_exc.PostgresConnectionError,
    )
except Exception:  # pragma: no cover - keep the module importable in absentia
    _APG_DEADLOCK = ()
    _APG_SERIALIZATION = ()
    _APG_LOCK_NOT_AVAILABLE = ()
    _APG_QUERY_CANCELED = ()
    _APG_CONNECTION = ()


class ErrorClass(enum.Enum):
    """Coarse-grained categories the retry decorator and error mapper consume.

    Members map onto the failure modes documented in
    ``docs/phases/phase-5-6-resilience.md``. Each policy declares which
    classes it considers retryable; the decorator consults
    :func:`classify` and checks membership.
    """

    DEADLOCK = "deadlock"  # 40P01
    SERIALIZATION = "serialization"  # 40001
    LOCK_TIMEOUT = "lock_timeout"  # 55P03
    CONNECTION_DROPPED = "connection_dropped"
    POOL_TIMEOUT = "pool_timeout"
    STATEMENT_TIMEOUT = "statement_timeout"  # 57014 / driver-side cancel
    INTEGRITY = "integrity"  # FK / unique violation — non-retryable
    DOMAIN = "domain"  # NotFound / FailedPrecondition / InvalidArgument
    BUG = "bug"  # ProgrammingError / DataError / unknown


# Postgres SQLSTATE codes that we map to specific classes when the structured
# asyncpg exception isn't available (older asyncpg, or a wrapping that lost
# the ``orig`` link). Order matters only for clarity — each code is unique.
_SQLSTATE_DEADLOCK: Final[str] = "40P01"
_SQLSTATE_SERIALIZATION: Final[str] = "40001"
_SQLSTATE_LOCK_NOT_AVAILABLE: Final[str] = "55P03"
_SQLSTATE_QUERY_CANCELED: Final[str] = "57014"


def classify(exc: BaseException) -> ErrorClass:
    """Return the :class:`ErrorClass` that best describes ``exc``.

    The function never raises — unknown exceptions land in
    :attr:`ErrorClass.BUG` so callers can still log + map them, just without
    retrying.
    """

    # Fast paths for our own domain errors. They live in `library.errors`;
    # we import lazily here to avoid a hard dependency cycle (errors imports
    # resilience for the post-retry mapping).
    from library.errors import DomainError

    if isinstance(exc, DomainError):
        return ErrorClass.DOMAIN

    # SQLAlchemy's pool-side timeout when no connection is available.
    if isinstance(exc, SQLAlchemyPoolTimeout):
        return ErrorClass.POOL_TIMEOUT

    # FK / unique violation. Important: check IntegrityError BEFORE the
    # generic OperationalError branch — IntegrityError is a subclass of
    # DBAPIError but distinct in semantics.
    if isinstance(exc, IntegrityError):
        return ErrorClass.INTEGRITY

    # asyncpg's command_timeout (driver-side wall-clock cap) surfaces as a
    # bare asyncio.TimeoutError up the stack — SQLAlchemy passes it through.
    if isinstance(exc, asyncio.TimeoutError):
        return ErrorClass.STATEMENT_TIMEOUT

    # SQLAlchemy wraps driver exceptions in OperationalError / DBAPIError;
    # the original asyncpg exception lives on `.orig`.
    inner = getattr(exc, "orig", None)
    if inner is None and isinstance(exc, BaseException):
        # Caller may have unwrapped already, or asyncpg exception may have
        # leaked through directly (e.g. from a low-level connection check).
        inner = exc

    # asyncpg-typed checks first (most specific).
    if _APG_DEADLOCK and isinstance(inner, _APG_DEADLOCK):
        return ErrorClass.DEADLOCK
    if _APG_SERIALIZATION and isinstance(inner, _APG_SERIALIZATION):
        return ErrorClass.SERIALIZATION
    if _APG_LOCK_NOT_AVAILABLE and isinstance(inner, _APG_LOCK_NOT_AVAILABLE):
        return ErrorClass.LOCK_TIMEOUT
    if _APG_QUERY_CANCELED and isinstance(inner, _APG_QUERY_CANCELED):
        return ErrorClass.STATEMENT_TIMEOUT
    if _APG_CONNECTION and isinstance(inner, _APG_CONNECTION):
        return ErrorClass.CONNECTION_DROPPED

    # Fallback to sqlstate matching for asyncpg releases that don't expose
    # the typed exception we expected.
    sqlstate = getattr(inner, "sqlstate", None) or getattr(exc, "sqlstate", None)
    if sqlstate == _SQLSTATE_DEADLOCK:
        return ErrorClass.DEADLOCK
    if sqlstate == _SQLSTATE_SERIALIZATION:
        return ErrorClass.SERIALIZATION
    if sqlstate == _SQLSTATE_LOCK_NOT_AVAILABLE:
        return ErrorClass.LOCK_TIMEOUT
    if sqlstate == _SQLSTATE_QUERY_CANCELED:
        return ErrorClass.STATEMENT_TIMEOUT

    # Generic OperationalError without a known sqlstate — most often a
    # connection-level issue (TCP RST, server shutdown, bad credentials on
    # reconnect). Treat as connection_dropped; the policy decides whether
    # that's retryable for the call site.
    if isinstance(exc, OperationalError):
        return ErrorClass.CONNECTION_DROPPED

    # Any other DBAPIError that didn't match above is an unexpected DB
    # failure — surface as BUG so it shows up clearly in observability.
    if isinstance(exc, DBAPIError):
        return ErrorClass.BUG

    return ErrorClass.BUG


def is_classified_transient(cls: ErrorClass) -> bool:
    """True for classes that are infrastructure-level (vs. domain or bug).

    Used by the gRPC error mapper to decide whether a *post-retry* exception
    should surface as ``UNAVAILABLE`` / ``RESOURCE_EXHAUSTED`` /
    ``DEADLINE_EXCEEDED`` instead of ``INTERNAL``.
    """

    return cls in {
        ErrorClass.DEADLOCK,
        ErrorClass.SERIALIZATION,
        ErrorClass.LOCK_TIMEOUT,
        ErrorClass.CONNECTION_DROPPED,
        ErrorClass.POOL_TIMEOUT,
        ErrorClass.STATEMENT_TIMEOUT,
    }


__all__ = ["ErrorClass", "classify", "is_classified_transient"]
