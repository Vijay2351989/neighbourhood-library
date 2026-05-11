"""Unit tests for :func:`library.errors.map_domain_errors`.

The decorator turns typed domain exceptions into gRPC status codes via
``context.abort``. These tests use a fake context (no real grpc-aio
plumbing) so the decorator's branching is exercised directly.

The transient-error regression test is the load-bearing one: the previous
implementation relied on ``context.abort()`` raising ``AbortError`` to
skip the INTERNAL fallback below it. When ``context`` is mocked (or a
future grpc-aio changes that contract) ``abort()`` may return normally,
which used to cause a second abort with ``INTERNAL`` to fire on top of
the real status. The fix uses explicit ``if/else`` so exactly one abort
runs per call regardless of whether it raises.
"""

from __future__ import annotations

import grpc
import pytest
from asyncpg import exceptions as apg_exc
from sqlalchemy.exc import OperationalError

from library.errors import (
    AlreadyExists,
    FailedPrecondition,
    InvalidArgument,
    NotFound,
    map_domain_errors,
)


class _FakeAbort(Exception):
    """Stand-in for grpc-aio's AbortError that doesn't require grpc internals."""

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(details)
        self.code = code
        self.details = details


class FakeContext:
    """Records abort calls; optionally simulates the abort-raises contract.

    Setting ``abort_raises=False`` reproduces the common test-mocking case
    where ``context.abort`` returns normally instead of raising. The fixed
    decorator must still produce exactly one abort under that condition.
    """

    def __init__(self, *, abort_raises: bool = True) -> None:
        self.abort_calls: list[tuple[grpc.StatusCode, str]] = []
        self._abort_raises = abort_raises

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.abort_calls.append((code, details))
        if self._abort_raises:
            raise _FakeAbort(code, details)


class _DummyServicer:
    """Stand-in for ``self`` in the decorator's wrapper signature."""


def _wrap_deadlock() -> OperationalError:
    """SQLAlchemy OperationalError wrapping an asyncpg DeadlockDetectedError.

    Matches the shape ``classify()`` recognizes as ``ErrorClass.DEADLOCK``.
    """

    return OperationalError("borrow", {}, apg_exc.DeadlockDetectedError("dl"))


# ---------- DomainError mapping ----------


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (NotFound("missing"), grpc.StatusCode.NOT_FOUND),
        (AlreadyExists("dup"), grpc.StatusCode.ALREADY_EXISTS),
        (InvalidArgument("bad input"), grpc.StatusCode.INVALID_ARGUMENT),
        (FailedPrecondition("no copies"), grpc.StatusCode.FAILED_PRECONDITION),
    ],
)
@pytest.mark.asyncio
async def test_domain_error_maps_to_grpc_status(
    exc: Exception, expected_code: grpc.StatusCode
) -> None:
    @map_domain_errors
    async def handler(self, request, context):
        raise exc

    ctx = FakeContext()
    with pytest.raises(_FakeAbort):
        await handler(_DummyServicer(), None, ctx)

    assert len(ctx.abort_calls) == 1
    code, details = ctx.abort_calls[0]
    assert code is expected_code
    assert details == str(exc)


# ---------- Transient infra error mapping ----------


@pytest.mark.asyncio
async def test_transient_error_maps_to_unavailable() -> None:
    @map_domain_errors
    async def handler(self, request, context):
        raise _wrap_deadlock()

    ctx = FakeContext()
    with pytest.raises(_FakeAbort):
        await handler(_DummyServicer(), None, ctx)

    assert len(ctx.abort_calls) == 1
    code, _details = ctx.abort_calls[0]
    assert code is grpc.StatusCode.UNAVAILABLE


@pytest.mark.asyncio
async def test_transient_does_not_double_abort_when_abort_does_not_raise() -> None:
    """Regression for the implicit-AbortError reliance.

    With ``abort_raises=False`` the previous implementation fell through
    from the transient branch into the INTERNAL block and called abort
    a second time with the wrong status. The fixed implementation uses
    explicit ``if/else`` and produces exactly one abort.
    """

    @map_domain_errors
    async def handler(self, request, context):
        raise _wrap_deadlock()

    ctx = FakeContext(abort_raises=False)
    # No exception expected: abort returns normally; wrapper returns None.
    await handler(_DummyServicer(), None, ctx)

    assert len(ctx.abort_calls) == 1, (
        f"expected exactly one abort, got {ctx.abort_calls}"
    )
    code, _ = ctx.abort_calls[0]
    assert code is grpc.StatusCode.UNAVAILABLE


# ---------- Unknown/unclassified error ----------


@pytest.mark.asyncio
async def test_unknown_error_maps_to_internal_with_sanitized_message() -> None:
    @map_domain_errors
    async def handler(self, request, context):
        raise RuntimeError("kaboom internal detail")

    ctx = FakeContext()
    with pytest.raises(_FakeAbort):
        await handler(_DummyServicer(), None, ctx)

    assert len(ctx.abort_calls) == 1
    code, details = ctx.abort_calls[0]
    assert code is grpc.StatusCode.INTERNAL
    # Internal Python details must not leak to the wire.
    assert "kaboom" not in details
    assert details == "internal error"


@pytest.mark.asyncio
async def test_unknown_does_not_double_abort_when_abort_does_not_raise() -> None:
    """Symmetric regression test: the INTERNAL branch should also fire
    exactly once when abort doesn't raise."""

    @map_domain_errors
    async def handler(self, request, context):
        raise RuntimeError("boom")

    ctx = FakeContext(abort_raises=False)
    await handler(_DummyServicer(), None, ctx)

    assert len(ctx.abort_calls) == 1
    code, _ = ctx.abort_calls[0]
    assert code is grpc.StatusCode.INTERNAL
