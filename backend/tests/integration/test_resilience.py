"""End-to-end coverage of the Phase 5.6 resilience layer.

What we verify against the live testcontainer + in-process gRPC server:

* Forced deadlock via SQLAlchemy event hook: the first attempt of a
  ``BorrowBook`` raises ``DeadlockDetectedError``; the retry decorator
  classifies it as ``DEADLOCK`` and re-runs the service method, which
  opens a fresh session and succeeds. The client sees a normal
  ``BorrowBookResponse``; SigNoz would see a ``retry.attempt`` event.
* IntegrityError is NOT retried — non-retryable errors surface immediately.
* Statement timeout is enforced server-side: a query under a low
  ``SET LOCAL statement_timeout`` actually gets killed by Postgres.
* PG ``lock_timeout`` returns the clear ``LockNotAvailableError`` rather
  than hanging forever, validating the engine config.
* The retry decorator emits a ``retry.attempt`` span event with the
  expected attributes when retries fire on the gRPC happy path.

Tests requiring engine re-creation with different pool sizes (saturation
scenarios) are out of scope for the current suite — the in-memory
deadlock injection covers the decorator-path behavior, and the SigNoz
log already shows pool_timeout under genuine production load.
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import patch

import grpc
import pytest
from asyncpg import exceptions as apg_exc
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from library.generated.library.v1 import library_pb2


# ---------- fixtures ----------


@pytest.fixture(scope="module")
def resilience_spans() -> Iterator[InMemorySpanExporter]:
    """Attach an in-memory exporter to the global tracer provider for this module."""

    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    processor.shutdown()


@pytest.fixture(autouse=True)
def _reset_resilience_spans(resilience_spans: InMemorySpanExporter):
    resilience_spans.clear()
    yield


# ---------- helpers ----------


async def _create_book(library_stub, *, copies: int = 1) -> int:
    resp = await library_stub.CreateBook(
        library_pb2.CreateBookRequest(
            title="Dune", author="Frank Herbert", number_of_copies=copies
        )
    )
    return resp.book.id


async def _create_member(library_stub, *, email: str = "patron@example.com") -> int:
    resp = await library_stub.CreateMember(
        library_pb2.CreateMemberRequest(name="Patron", email=email)
    )
    return resp.member.id


def _events_named(spans, name: str):
    """Flatten all events with the given name across all finished spans."""

    out = []
    for span in spans:
        for event in span.events:
            if event.name == name:
                out.append(event)
    return out


# ---------- tests ----------


@pytest.mark.asyncio
async def test_forced_deadlock_is_retried_transparently(
    library_stub, resilience_spans: InMemorySpanExporter
) -> None:
    """A first-attempt DeadlockDetectedError should be retried and succeed
    on the second attempt; the client sees a normal ``BorrowBookResponse``.
    """

    book_id = await _create_book(library_stub, copies=2)
    member_id = await _create_member(library_stub, email="dl@example.com")

    # Patch the repository to raise DeadlockDetectedError once, then call through.
    from library.repositories import loans as loans_repo

    real_borrow = loans_repo.borrow
    state = {"calls": 0}

    async def flaky_borrow(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            # SQLAlchemy wraps driver errors in OperationalError with .orig
            # being the asyncpg exception. The decorator's classifier checks
            # both the typed exception and the sqlstate fallback.
            raise OperationalError(
                "borrow", {}, apg_exc.DeadlockDetectedError("forced")
            )
        return await real_borrow(*args, **kwargs)

    with patch.object(loans_repo, "borrow", flaky_borrow):
        resp = await library_stub.BorrowBook(
            library_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
        )

    # Client got a real response.
    assert resp.loan.id > 0
    assert resp.loan.member_id == member_id
    # Repo was called twice (one fail, one success).
    assert state["calls"] == 2

    # And a retry.attempt event landed on some span.
    finished = list(resilience_spans.get_finished_spans())
    attempt_events = _events_named(finished, "retry.attempt")
    assert len(attempt_events) >= 1
    evt = attempt_events[0]
    assert evt.attributes["retry.policy"] == "RETRY_WRITE_TX"
    assert evt.attributes["retry.error_class"] == "deadlock"
    assert evt.attributes["retry.attempt"] == 2


@pytest.mark.asyncio
async def test_integrity_error_is_not_retried(
    library_stub, resilience_spans: InMemorySpanExporter
) -> None:
    """A unique-violation on member email is non-retryable: the second
    CreateMember with the same email must fail on the first attempt without
    retrying."""

    await _create_member(library_stub, email="dup@example.com")

    # Same email a second time → either ALREADY_EXISTS (mapped from
    # IntegrityError to a domain error) or INVALID_ARGUMENT, depending on
    # the existing service mapping. Either way, it must NOT retry.
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await library_stub.CreateMember(
            library_pb2.CreateMemberRequest(name="Other", email="dup@example.com")
        )
    # Whatever code surfaces, it should not be UNAVAILABLE / RESOURCE_EXHAUSTED
    # (those would indicate the integrity error was misclassified as transient).
    assert exc_info.value.code() not in {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.DEADLINE_EXCEEDED,
    }

    # No retry events should be present.
    finished = list(resilience_spans.get_finished_spans())
    assert _events_named(finished, "retry.attempt") == []


@pytest.mark.asyncio
async def test_statement_timeout_kills_a_slow_query() -> None:
    """Verify the engine's ``statement_timeout`` actually fires server-side.

    We open a session, set ``statement_timeout`` to 100ms locally, run a
    ``pg_sleep(2)``, and assert the query is canceled. This validates the
    Postgres-side enforcement; the ``QueryCanceledError`` would be
    classified as ``STATEMENT_TIMEOUT`` and surface as ``UNAVAILABLE`` if
    it propagated all the way through the gRPC mapper.
    """

    from library.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET LOCAL statement_timeout = '100ms'"))
        with pytest.raises(Exception) as exc_info:
            await session.execute(text("SELECT pg_sleep(2)"))
        # asyncpg raises QueryCanceledError; SQLAlchemy wraps it in
        # OperationalError. Confirm the wrapped error has the expected
        # asyncpg origin or sqlstate '57014'.
        err = exc_info.value
        orig = getattr(err, "orig", None) or err
        sqlstate = getattr(orig, "sqlstate", None)
        assert isinstance(orig, apg_exc.QueryCanceledError) or sqlstate == "57014"


@pytest.mark.asyncio
async def test_lock_timeout_surfaces_as_lock_not_available() -> None:
    """Two sessions racing for the same row should produce a
    ``LockNotAvailableError`` on the second under a low ``lock_timeout`` —
    NOT a generic statement_timeout. This validates the
    ``lock_timeout < statement_timeout`` ordering invariant from the spec.
    """

    from library.db.engine import AsyncSessionLocal

    book_id = await _seed_one_book_one_copy()

    # Session A holds the lock for ~500ms.
    holder = AsyncSessionLocal()
    contender = AsyncSessionLocal()

    try:
        await holder.execute(text("BEGIN"))
        await holder.execute(
            text("SELECT * FROM book_copies WHERE book_id = :bid FOR UPDATE"),
            {"bid": book_id},
        )

        # Contender uses an aggressive 100ms lock_timeout to guarantee the
        # lock-not-available error rather than waiting for the engine's 3s
        # default. statement_timeout is left untouched (engine default 5s),
        # so the lock_timeout fires first.
        await contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
        await contender.execute(text("BEGIN"))
        with pytest.raises(Exception) as exc_info:
            await contender.execute(
                text("SELECT * FROM book_copies WHERE book_id = :bid FOR UPDATE"),
                {"bid": book_id},
            )
        err = exc_info.value
        orig = getattr(err, "orig", None) or err
        sqlstate = getattr(orig, "sqlstate", None)
        assert isinstance(orig, apg_exc.LockNotAvailableError) or sqlstate == "55P03"
    finally:
        try:
            await contender.rollback()
        except Exception:
            pass
        try:
            await holder.rollback()
        except Exception:
            pass
        await holder.close()
        await contender.close()


# ---------- helpers used only by lock_timeout test ----------


async def _seed_one_book_one_copy() -> int:
    """Insert one book + one copy directly via the engine, return book_id.

    Avoids going through the gRPC stack so the test isolates the lock-
    contention scenario.
    """

    from library.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                INSERT INTO books (title, author, created_at, updated_at)
                VALUES ('Lock Test', 'Tester', now(), now())
                RETURNING id
                """
            )
        )
        book_id = result.scalar_one()
        await session.execute(
            text(
                """
                INSERT INTO book_copies (book_id, status, created_at)
                VALUES (:bid, 'AVAILABLE', now())
                """
            ),
            {"bid": book_id},
        )
        await session.commit()
    return int(book_id)
