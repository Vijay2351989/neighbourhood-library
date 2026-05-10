"""Concurrent-borrow tests for the partial-unique-index + FOR UPDATE SKIP LOCKED flow.

We deliberately exercise the *contention* case: N borrowers race for a
1-copy book. The structural invariants we assert:

* Exactly one borrow succeeds — the partial unique index ``loans_one_active_per_copy_idx``
  guarantees no two active loans for the same copy can coexist.
* The losing borrows fail with ``FAILED_PRECONDITION`` (mapped from the
  ``no available copies`` precondition) — no other status leaks out.
* Final DB state is consistent: exactly one row in ``loans`` with
  ``returned_at IS NULL``, and that copy's status is ``BORROWED``.

The default ``FOR UPDATE SKIP LOCKED`` path handles the contention by
having one transaction lock the row and the others walk past it (their
``LIMIT 1`` query returns zero rows since they "skipped" the locked one
and there are no other AVAILABLE copies). The partial-unique-index is
the safety net.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest
from sqlalchemy import text

from library.generated.library.v1 import book_pb2, loan_pb2, member_pb2

# Number of concurrent borrowers. Larger N stresses the lock manager more
# but slows test runtime; 10 is enough to surface ordering bugs reliably
# while keeping the suite fast.
N_BORROWERS = 10


async def _create_member(member_stub, *, email: str) -> int:
    resp = await member_stub.CreateMember(
        member_pb2.CreateMemberRequest(name=f"M-{email}", email=email)
    )
    return resp.member.id


async def _create_book(book_stub, *, copies: int) -> int:
    resp = await book_stub.CreateBook(
        book_pb2.CreateBookRequest(
            title="Hot Title", author="Z", number_of_copies=copies
        )
    )
    return resp.book.id


async def test_concurrent_borrow_one_winner(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub, copies=1)
    member_ids = [
        await _create_member(member_stub, email=f"m{i}@example.com")
        for i in range(N_BORROWERS)
    ]

    # Fire all borrows in parallel. ``return_exceptions=True`` so that
    # AioRpcError losses are returned as values rather than tearing down
    # the gather.
    coros = [
        loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=mid)
        )
        for mid in member_ids
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, grpc.aio.AioRpcError)]
    other_errors = [
        r
        for r in results
        if isinstance(r, BaseException) and not isinstance(r, grpc.aio.AioRpcError)
    ]

    assert other_errors == [], f"unexpected non-gRPC errors: {other_errors!r}"
    assert len(successes) == 1, (
        f"expected exactly 1 winning borrow, got {len(successes)}"
    )
    assert len(failures) == N_BORROWERS - 1
    assert all(
        f.code() == grpc.StatusCode.FAILED_PRECONDITION for f in failures
    ), f"all losers must be FAILED_PRECONDITION: {[f.code().name for f in failures]}"

    # Final DB state: exactly one active loan against the only copy, and
    # that copy is BORROWED. We check directly so we observe the state the
    # database actually committed, not what the API decides to return.
    from library.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        active_loans = await session.scalar(
            text(
                "SELECT COUNT(*) FROM loans WHERE returned_at IS NULL "
                "AND copy_id IN (SELECT id FROM book_copies WHERE book_id = :b)"
            ),
            {"b": book_id},
        )
        borrowed_copies = await session.scalar(
            text(
                "SELECT COUNT(*) FROM book_copies WHERE book_id = :b "
                "AND status = 'BORROWED'"
            ),
            {"b": book_id},
        )
        available_copies = await session.scalar(
            text(
                "SELECT COUNT(*) FROM book_copies WHERE book_id = :b "
                "AND status = 'AVAILABLE'"
            ),
            {"b": book_id},
        )

    assert active_loans == 1
    assert borrowed_copies == 1
    assert available_copies == 0


async def test_concurrent_borrow_two_copies_two_winners(book_stub, loan_stub, member_stub) -> None:
    """With 2 copies and 10 racers, exactly 2 should win.

    This is what makes ``FOR UPDATE SKIP LOCKED`` matter: without
    ``SKIP LOCKED`` the second copy's borrowers would all queue behind
    the first row's lock instead of taking the second copy in parallel.
    """

    book_id = await _create_book(book_stub, copies=2)
    member_ids = [
        await _create_member(member_stub, email=f"m{i}@example.com")
        for i in range(N_BORROWERS)
    ]

    coros = [
        loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=mid)
        )
        for mid in member_ids
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, grpc.aio.AioRpcError)]

    assert len(successes) == 2
    assert len(failures) == N_BORROWERS - 2
    # Each winner picked a distinct copy.
    won_copy_ids = {s.loan.copy_id for s in successes}
    assert len(won_copy_ids) == 2
