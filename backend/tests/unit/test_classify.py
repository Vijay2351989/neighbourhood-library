"""Unit tests for ``library.resilience.classify``.

Table-driven: every documented error class maps to the right
:class:`ErrorClass`. Includes asyncpg-typed exceptions and the SQLSTATE
fallback path.
"""

from __future__ import annotations

import asyncio

import pytest
from asyncpg import exceptions as apg_exc
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
)
from sqlalchemy.exc import TimeoutError as SAPoolTimeout

from library.errors import FailedPrecondition, NotFound
from library.resilience.classify import (
    ErrorClass,
    classify,
    is_classified_transient,
)


def _wrap(driver_exc: BaseException) -> OperationalError:
    """Build a SQLAlchemy OperationalError that wraps an asyncpg exception.

    SQLAlchemy's exceptions take ``(statement, params, orig)``; we pass
    ``None`` for statement/params since classify() only consults ``.orig``.
    """

    return OperationalError("test stmt", {}, driver_exc)


@pytest.mark.parametrize(
    "exc,expected",
    [
        # Domain — short-circuit branch.
        (NotFound("x"), ErrorClass.DOMAIN),
        (FailedPrecondition("y"), ErrorClass.DOMAIN),
        # Pool exhaustion — SQLAlchemy raises this directly, not a wrapper.
        (SAPoolTimeout("pool"), ErrorClass.POOL_TIMEOUT),
        # Driver-side command_timeout surfaces as bare asyncio.TimeoutError.
        (asyncio.TimeoutError(), ErrorClass.STATEMENT_TIMEOUT),
        # asyncpg-typed exceptions wrapped by SQLAlchemy OperationalError.
        (_wrap(apg_exc.DeadlockDetectedError("d")), ErrorClass.DEADLOCK),
        (_wrap(apg_exc.SerializationError("s")), ErrorClass.SERIALIZATION),
        (_wrap(apg_exc.LockNotAvailableError("lock")), ErrorClass.LOCK_TIMEOUT),
        (
            _wrap(apg_exc.QueryCanceledError("cancel")),
            ErrorClass.STATEMENT_TIMEOUT,
        ),
        (
            _wrap(apg_exc.ConnectionDoesNotExistError("gone")),
            ErrorClass.CONNECTION_DROPPED,
        ),
        # IntegrityError — non-retryable; checked before OperationalError.
        (IntegrityError("ins", {}, Exception("dup")), ErrorClass.INTEGRITY),
        # Unknown DBAPIError → BUG (so it surfaces in observability).
        (
            ProgrammingError("syntax", {}, Exception("bad sql")),
            ErrorClass.BUG,
        ),
        # Generic operational without sqlstate → assume connection-level.
        (
            OperationalError("conn lost", {}, Exception("RST")),
            ErrorClass.CONNECTION_DROPPED,
        ),
        # Plain unrelated exception → BUG.
        (ValueError("garbage"), ErrorClass.BUG),
    ],
)
def test_classify_dispatches_correctly(exc: BaseException, expected: ErrorClass) -> None:
    assert classify(exc) is expected


def test_classify_falls_back_to_sqlstate_when_orig_lacks_typed_class() -> None:
    """If the underlying error doesn't isinstance into our typed groups but
    carries a ``sqlstate`` attribute, classify() must still pick the right
    bucket."""

    class _Pseudo(Exception):
        sqlstate = "40P01"

    wrapped = OperationalError("x", {}, _Pseudo("deadlock"))
    assert classify(wrapped) is ErrorClass.DEADLOCK

    class _PseudoLock(Exception):
        sqlstate = "55P03"

    wrapped_lock = OperationalError("x", {}, _PseudoLock("lock"))
    assert classify(wrapped_lock) is ErrorClass.LOCK_TIMEOUT


def test_classify_handles_dbapi_error_with_no_known_pattern() -> None:
    """An unrecognized DBAPIError lands in BUG, not in a transient class."""

    err = DBAPIError("?", {}, Exception("?"))
    cls = classify(err)
    assert cls is ErrorClass.BUG
    assert is_classified_transient(cls) is False


@pytest.mark.parametrize(
    "cls,expected",
    [
        (ErrorClass.DEADLOCK, True),
        (ErrorClass.SERIALIZATION, True),
        (ErrorClass.LOCK_TIMEOUT, True),
        (ErrorClass.CONNECTION_DROPPED, True),
        (ErrorClass.POOL_TIMEOUT, True),
        (ErrorClass.STATEMENT_TIMEOUT, True),
        (ErrorClass.INTEGRITY, False),
        (ErrorClass.DOMAIN, False),
        (ErrorClass.BUG, False),
    ],
)
def test_is_classified_transient_partition(cls: ErrorClass, expected: bool) -> None:
    """Every ErrorClass member is either transient (retry-class) or not."""

    assert is_classified_transient(cls) is expected
