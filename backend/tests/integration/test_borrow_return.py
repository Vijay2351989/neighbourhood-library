"""End-to-end coverage of the four loan RPCs.

Tests run a real client against the in-process server, real Postgres
testcontainer with the migrated schema, real concurrency machinery — same
setup as the Phase 4 tests (see ``conftest.py``).

For tests that need loans in specific time states (overdue, fined, etc.)
we mutate ``loans.due_at`` / ``loans.returned_at`` directly via SQL after
borrowing through the API. The borrow flow uses ``now()`` server-side, so
backdating in the test is the only practical way to simulate "borrowed
two months ago" without making the production code time-injectable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from sqlalchemy import text

from library.generated.library.v1 import book_pb2, loan_pb2, member_pb2

# Match the env defaults so tests don't have to import them from settings.
GRACE_DAYS = 14
PER_DAY_CENTS = 25
CAP_CENTS = 2000


# ---------- helpers ----------


async def _create_book(book_stub, *, copies: int = 1, title: str = "Dune") -> int:
    req = book_pb2.CreateBookRequest(
        title=title, author="Frank Herbert", number_of_copies=copies
    )
    resp = await book_stub.CreateBook(req)
    return resp.book.id


async def _create_member(member_stub, *, email: str = "ada@example.com") -> int:
    resp = await member_stub.CreateMember(
        member_pb2.CreateMemberRequest(name="Ada Lovelace", email=email)
    )
    return resp.member.id


async def _backdate_loan(loan_id: int, *, due_at: datetime, returned_at: datetime | None = None):
    """Force a loan's due_at / returned_at to specific moments in the past.

    Borrow happens at ``now()`` server-side; to test fines and overdue we
    rewrite the row directly. Production code never touches these columns
    after creation except via ``ReturnBook``.
    """

    from library.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE loans SET due_at = :due, returned_at = :ret WHERE id = :id"
            ),
            {"id": loan_id, "due": due_at, "ret": returned_at},
        )
        await session.commit()


# =====================================================================
# BorrowBook
# =====================================================================


async def test_borrow_happy_path(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub, copies=2)
    member_id = await _create_member(member_stub)

    resp = await loan_stub.BorrowBook(
        loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
    )
    loan = resp.loan
    assert loan.id > 0
    assert loan.book_id == book_id
    assert loan.member_id == member_id
    assert loan.copy_id > 0
    # Denormalized fields populated for UI use
    assert loan.book_title == "Dune"
    assert loan.book_author == "Frank Herbert"
    assert loan.member_name == "Ada Lovelace"
    # Fresh loan: not overdue, not fined, not returned
    assert loan.overdue is False
    assert loan.fine_cents == 0
    assert not loan.HasField("returned_at")
    assert loan.borrowed_at.seconds > 0
    assert loan.due_at.seconds > loan.borrowed_at.seconds  # default due_at is +14d

    # And the book's available_copies dropped from 2 to 1
    book = (await book_stub.GetBook(book_pb2.GetBookRequest(id=book_id))).book
    assert book.total_copies == 2
    assert book.available_copies == 1


async def test_borrow_with_explicit_due_at(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub)
    member_id = await _create_member(member_stub)

    due = datetime.now(timezone.utc) + timedelta(days=7)
    req = loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
    req.due_at.FromDatetime(due)
    resp = await loan_stub.BorrowBook(req)

    # Allow a few seconds of skew between Python "now" and DB-side now().
    assert abs(resp.loan.due_at.seconds - int(due.timestamp())) < 5


async def test_borrow_with_past_due_at_rejected(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub)
    member_id = await _create_member(member_stub)

    past = datetime.now(timezone.utc) - timedelta(days=1)
    req = loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
    req.due_at.FromDatetime(past)
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.BorrowBook(req)
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_borrow_no_copies_available(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub, copies=1)
    a_id = await _create_member(member_stub, email="a@example.com")
    b_id = await _create_member(member_stub, email="b@example.com")

    # First borrow takes the only copy.
    await loan_stub.BorrowBook(
        loan_pb2.BorrowBookRequest(book_id=book_id, member_id=a_id)
    )
    # Second borrow has nothing to take.
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=b_id)
        )
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert "no available copies" in exc_info.value.details().lower()


async def test_borrow_book_not_found(book_stub, loan_stub, member_stub) -> None:
    member_id = await _create_member(member_stub)
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=999_999, member_id=member_id)
        )
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
    assert "book" in exc_info.value.details().lower()


async def test_borrow_member_not_found(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub)
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=999_999)
        )
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
    assert "member" in exc_info.value.details().lower()


async def test_borrow_invalid_args(book_stub, loan_stub, member_stub) -> None:
    for req in [
        loan_pb2.BorrowBookRequest(book_id=0, member_id=1),
        loan_pb2.BorrowBookRequest(book_id=1, member_id=0),
    ]:
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await loan_stub.BorrowBook(req)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# =====================================================================
# ReturnBook
# =====================================================================


async def test_return_happy_path(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub, copies=1)
    member_id = await _create_member(member_stub)
    borrow_resp = await loan_stub.BorrowBook(
        loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
    )
    loan_id = borrow_resp.loan.id

    return_resp = await loan_stub.ReturnBook(
        loan_pb2.ReturnBookRequest(loan_id=loan_id)
    )
    assert return_resp.loan.id == loan_id
    assert return_resp.loan.HasField("returned_at")
    assert return_resp.loan.returned_at.seconds > 0
    assert return_resp.loan.fine_cents == 0  # within grace
    assert return_resp.loan.overdue is False  # returned -> not overdue

    # And the copy is back to AVAILABLE.
    book = (await book_stub.GetBook(book_pb2.GetBookRequest(id=book_id))).book
    assert book.available_copies == 1


async def test_return_already_returned(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub)
    member_id = await _create_member(member_stub)
    loan_id = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
        )
    ).loan.id
    await loan_stub.ReturnBook(loan_pb2.ReturnBookRequest(loan_id=loan_id))

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.ReturnBook(loan_pb2.ReturnBookRequest(loan_id=loan_id))
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert "already returned" in exc_info.value.details().lower()


async def test_return_not_found(book_stub, loan_stub, member_stub) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.ReturnBook(loan_pb2.ReturnBookRequest(loan_id=999_999))
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


async def test_return_invalid_arg(book_stub, loan_stub, member_stub) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.ReturnBook(loan_pb2.ReturnBookRequest(loan_id=0))
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# =====================================================================
# Overdue + fine_cents on a single loan
# =====================================================================


async def test_active_loan_overdue_flag(book_stub, loan_stub, member_stub) -> None:
    """Backdate due_at to yesterday → overdue=true, but fine still 0 (within grace)."""

    book_id = await _create_book(book_stub)
    member_id = await _create_member(member_stub)
    loan_id = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
        )
    ).loan.id

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    await _backdate_loan(loan_id, due_at=yesterday)

    loans = (
        await loan_stub.ListLoans(
            loan_pb2.ListLoansRequest(filter=loan_pb2.LOAN_FILTER_ACTIVE)
        )
    ).loans
    assert len(loans) == 1
    assert loans[0].overdue is True
    assert loans[0].fine_cents == 0  # 1 day overdue is well within 14-day grace


async def test_active_loan_one_day_past_grace_charges(book_stub, loan_stub, member_stub) -> None:
    book_id = await _create_book(book_stub)
    member_id = await _create_member(member_stub)
    loan_id = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
        )
    ).loan.id

    # 15 days overdue: 1 day past the 14-day grace => 1 * 25 cents
    due = datetime.now(timezone.utc) - timedelta(days=15)
    await _backdate_loan(loan_id, due_at=due)

    loans = (
        await loan_stub.ListLoans(
            loan_pb2.ListLoansRequest(filter=loan_pb2.LOAN_FILTER_HAS_FINE)
        )
    ).loans
    assert len(loans) == 1
    assert loans[0].fine_cents == PER_DAY_CENTS


async def test_active_loan_at_cap(book_stub, loan_stub, member_stub) -> None:
    """A loan ~100 days overdue is well past the cap; fine clamps to cap."""

    book_id = await _create_book(book_stub)
    member_id = await _create_member(member_stub)
    loan_id = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
        )
    ).loan.id

    due = datetime.now(timezone.utc) - timedelta(days=100)
    await _backdate_loan(loan_id, due_at=due)

    member_loans = (
        await loan_stub.GetMemberLoans(
            loan_pb2.GetMemberLoansRequest(
                member_id=member_id, filter=loan_pb2.LOAN_FILTER_ACTIVE
            )
        )
    ).loans
    assert len(member_loans) == 1
    assert member_loans[0].fine_cents == CAP_CENTS


async def test_returned_late_snapshot_fine(book_stub, loan_stub, member_stub) -> None:
    """Returned 20 days after due → 6 days past grace × 25 = 150 cents,
    snapshot frozen at returned_at."""

    book_id = await _create_book(book_stub)
    member_id = await _create_member(member_stub)
    loan_id = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
        )
    ).loan.id

    # Force the loan to look like it was due 20 days ago and returned today.
    due = datetime.now(timezone.utc) - timedelta(days=20)
    returned = datetime.now(timezone.utc)
    await _backdate_loan(loan_id, due_at=due, returned_at=returned)

    loans = (
        await loan_stub.ListLoans(
            loan_pb2.ListLoansRequest(filter=loan_pb2.LOAN_FILTER_RETURNED)
        )
    ).loans
    assert len(loans) == 1
    assert loans[0].fine_cents == 6 * PER_DAY_CENTS
    assert loans[0].overdue is False  # returned -> overdue is false even if late


# =====================================================================
# outstanding_fines_cents (Member aggregate)
# =====================================================================


async def test_member_outstanding_fines_aggregates_across_loans(book_stub, loan_stub, member_stub) -> None:
    """Sum of fines across all of a member's loans surfaces on GetMember."""

    member_id = await _create_member(member_stub)

    # Three books, three loans, three different fine states:
    book_a = await _create_book(book_stub, title="A")
    book_b = await _create_book(book_stub, title="B")
    book_c = await _create_book(book_stub, title="C")

    loan_a = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_a, member_id=member_id)
        )
    ).loan.id
    loan_b = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_b, member_id=member_id)
        )
    ).loan.id
    loan_c = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_c, member_id=member_id)
        )
    ).loan.id

    # A: 1 day past grace → 25c
    await _backdate_loan(loan_a, due_at=datetime.now(timezone.utc) - timedelta(days=15))
    # B: 100 days overdue, capped at 2000c
    await _backdate_loan(loan_b, due_at=datetime.now(timezone.utc) - timedelta(days=100))
    # C: returned-late snapshot, 6 days past grace × 25 = 150c
    await _backdate_loan(
        loan_c,
        due_at=datetime.now(timezone.utc) - timedelta(days=20),
        returned_at=datetime.now(timezone.utc),
    )

    member = (
        await member_stub.GetMember(member_pb2.GetMemberRequest(id=member_id))
    ).member
    assert member.outstanding_fines_cents == PER_DAY_CENTS + CAP_CENTS + 6 * PER_DAY_CENTS


async def test_member_with_no_loans_has_zero_fines(book_stub, loan_stub, member_stub) -> None:
    member_id = await _create_member(member_stub)
    member = (
        await member_stub.GetMember(member_pb2.GetMemberRequest(id=member_id))
    ).member
    assert member.outstanding_fines_cents == 0


# =====================================================================
# ListLoans filters
# =====================================================================


async def test_list_loans_filters(book_stub, loan_stub, member_stub) -> None:
    """One row per filter category, then assert each filter sees only its own."""

    member_id = await _create_member(member_stub)
    book_a = await _create_book(book_stub, title="A")
    book_b = await _create_book(book_stub, title="B")
    book_c = await _create_book(book_stub, title="C")
    book_d = await _create_book(book_stub, title="D")

    # Active, not overdue
    active_normal = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_a, member_id=member_id)
        )
    ).loan.id

    # Active, overdue but within grace (no fine)
    active_overdue_no_fine = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_b, member_id=member_id)
        )
    ).loan.id
    await _backdate_loan(
        active_overdue_no_fine,
        due_at=datetime.now(timezone.utc) - timedelta(days=2),
    )

    # Active, overdue and fined
    active_with_fine = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_c, member_id=member_id)
        )
    ).loan.id
    await _backdate_loan(
        active_with_fine,
        due_at=datetime.now(timezone.utc) - timedelta(days=20),
    )

    # Returned (late, so it has a snapshot fine)
    returned_late = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_d, member_id=member_id)
        )
    ).loan.id
    await _backdate_loan(
        returned_late,
        due_at=datetime.now(timezone.utc) - timedelta(days=30),
        returned_at=datetime.now(timezone.utc),
    )

    def ids_for(filter_value):
        return None  # placeholder; we'll query inline below

    # UNSPECIFIED -> all 4
    all_resp = await loan_stub.ListLoans(loan_pb2.ListLoansRequest())
    assert all_resp.total_count == 4
    assert {l.id for l in all_resp.loans} == {
        active_normal,
        active_overdue_no_fine,
        active_with_fine,
        returned_late,
    }

    # ACTIVE -> 3
    active_resp = await loan_stub.ListLoans(
        loan_pb2.ListLoansRequest(filter=loan_pb2.LOAN_FILTER_ACTIVE)
    )
    assert {l.id for l in active_resp.loans} == {
        active_normal,
        active_overdue_no_fine,
        active_with_fine,
    }

    # RETURNED -> 1
    returned_resp = await loan_stub.ListLoans(
        loan_pb2.ListLoansRequest(filter=loan_pb2.LOAN_FILTER_RETURNED)
    )
    assert {l.id for l in returned_resp.loans} == {returned_late}

    # OVERDUE -> 2 (active and overdue, regardless of fine status)
    overdue_resp = await loan_stub.ListLoans(
        loan_pb2.ListLoansRequest(filter=loan_pb2.LOAN_FILTER_OVERDUE)
    )
    assert {l.id for l in overdue_resp.loans} == {
        active_overdue_no_fine,
        active_with_fine,
    }

    # HAS_FINE -> 2 (the active-fined and the returned-late)
    has_fine_resp = await loan_stub.ListLoans(
        loan_pb2.ListLoansRequest(filter=loan_pb2.LOAN_FILTER_HAS_FINE)
    )
    assert {l.id for l in has_fine_resp.loans} == {
        active_with_fine,
        returned_late,
    }


async def test_list_loans_member_and_book_filters(book_stub, loan_stub, member_stub) -> None:
    """``member_id`` / ``book_id`` filters scope the results."""

    a_id = await _create_member(member_stub, email="a@example.com")
    b_id = await _create_member(member_stub, email="b@example.com")
    book_id = await _create_book(book_stub, copies=2)

    a_loan = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=a_id)
        )
    ).loan.id
    b_loan = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=b_id)
        )
    ).loan.id

    # Filter by member A
    req = loan_pb2.ListLoansRequest()
    req.member_id.value = a_id
    a_resp = await loan_stub.ListLoans(req)
    assert [l.id for l in a_resp.loans] == [a_loan]

    # Filter by book id
    req = loan_pb2.ListLoansRequest()
    req.book_id.value = book_id
    book_resp = await loan_stub.ListLoans(req)
    assert {l.id for l in book_resp.loans} == {a_loan, b_loan}


# =====================================================================
# GetMemberLoans
# =====================================================================


async def test_get_member_loans_scoped_and_ordered(book_stub, loan_stub, member_stub) -> None:
    """Returns only the given member's loans, most-recent-first."""

    a_id = await _create_member(member_stub, email="a@example.com")
    b_id = await _create_member(member_stub, email="b@example.com")
    book_a = await _create_book(book_stub, title="A")
    book_b = await _create_book(book_stub, title="B")
    book_c = await _create_book(book_stub, title="C")

    # Member A borrows two books; B borrows one.
    a1 = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_a, member_id=a_id)
        )
    ).loan.id
    a2 = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_b, member_id=a_id)
        )
    ).loan.id
    _ = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_c, member_id=b_id)
        )
    ).loan.id

    resp = await loan_stub.GetMemberLoans(
        loan_pb2.GetMemberLoansRequest(member_id=a_id)
    )
    assert [l.id for l in resp.loans] == [a2, a1]  # most recent first


async def test_get_member_loans_member_not_found(book_stub, loan_stub, member_stub) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await loan_stub.GetMemberLoans(
            loan_pb2.GetMemberLoansRequest(member_id=999_999)
        )
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


async def test_get_member_loans_filter_active(book_stub, loan_stub, member_stub) -> None:
    member_id = await _create_member(member_stub)
    book_a = await _create_book(book_stub, title="A")
    book_b = await _create_book(book_stub, title="B")

    active_id = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_a, member_id=member_id)
        )
    ).loan.id
    returned_id = (
        await loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(book_id=book_b, member_id=member_id)
        )
    ).loan.id
    await loan_stub.ReturnBook(loan_pb2.ReturnBookRequest(loan_id=returned_id))

    resp = await loan_stub.GetMemberLoans(
        loan_pb2.GetMemberLoansRequest(
            member_id=member_id, filter=loan_pb2.LOAN_FILTER_ACTIVE
        )
    )
    assert [l.id for l in resp.loans] == [active_id]
