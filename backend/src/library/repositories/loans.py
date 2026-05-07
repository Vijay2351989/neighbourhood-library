"""Loan persistence: borrow / return / list / member-scope / fines aggregate.

This module owns the borrow concurrency strategy from
[design/01-database.md §3](../../../docs/design/01-database.md): pick an
``AVAILABLE`` copy with ``FOR UPDATE SKIP LOCKED`` (so two concurrent
borrowers of *different* copies never block each other), insert the
``loans`` row, flip the copy status, and trust the partial unique index
``loans_one_active_per_copy_idx`` to backstop any race that slips through.

It also owns the SQL form of the fine formula that
:mod:`library.services.fines` defines arithmetically: a single SQL
expression that computes the fine in cents per loan row, used for the
``LOAN_FILTER_HAS_FINE`` predicate and for the ``outstanding_fines_cents``
aggregate the member service consumes. The Python and SQL forms must agree
— there's a unit test pinning the Python side and integration tests
exercising the SQL side.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, NamedTuple

from opentelemetry import trace
from sqlalchemy import Integer, and_, cast, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from library.db.models import Book, BookCopy, CopyStatus, Loan, Member
from library.errors import FailedPrecondition, NotFound

_tracer = trace.get_tracer("library.repositories.loans")

_ACTIVE_LOAN_INDEX: Final[str] = "loans_one_active_per_copy_idx"


class LoanFilter(enum.IntEnum):
    """Domain mirror of ``library.v1.LoanFilter``.

    Values match the proto enum integers exactly so the service layer can
    map ``request.filter`` -> this enum trivially. Defined here (rather than
    importing the proto enum) to keep the repository proto-free.
    """

    UNSPECIFIED = 0
    ACTIVE = 1
    RETURNED = 2
    OVERDUE = 3
    HAS_FINE = 4


class FineConfig(NamedTuple):
    """Bundle the three fine knobs so we don't carry them in every signature."""

    grace_days: int
    per_day_cents: int
    cap_cents: int


class LoanRow(NamedTuple):
    """A loan plus the denormalized fields the wire response expects.

    The ``Loan`` proto includes ``book_id``, ``book_title``, ``book_author``,
    and ``member_name`` for UI convenience — the frontend should not have to
    issue follow-up calls just to render a loan row. We hydrate those at
    query time via SQL joins.
    """

    loan: Loan
    book_id: int
    book_title: str
    book_author: str
    member_name: str


@dataclass(slots=True)
class ListLoansResult:
    rows: list[LoanRow]
    total_count: int


# ---------- the SQL fine expression ----------


def _fine_expression(*, now: datetime, fines: FineConfig):
    """Build a SQLAlchemy expression computing ``fine_cents`` per loan row.

    Matches the Python formula in :func:`library.services.fines.compute_fine_cents`:
    ``min(cap, max(0, floor(elapsed_days) - grace) * per_day)``. ``elapsed_days``
    is ``COALESCE(returned_at, :now) - due_at`` interpreted as a number of days
    via ``EXTRACT(EPOCH ...)``, which avoids the month/day-normalization
    quirks of ``DATE_PART('day', interval)``.
    """

    reference = func.coalesce(Loan.returned_at, literal(now))
    elapsed_seconds = func.extract("epoch", reference - Loan.due_at)
    days_overdue = cast(func.floor(elapsed_seconds / 86400.0), Integer)
    days_past_grace = func.greatest(0, days_overdue - fines.grace_days)
    return func.least(fines.cap_cents, days_past_grace * fines.per_day_cents)


# ---------- borrow ----------


async def borrow(
    session: AsyncSession,
    *,
    book_id: int,
    member_id: int,
    due_at: datetime,
) -> LoanRow:
    """Insert a new active loan, locking exactly one ``AVAILABLE`` copy.

    Order of operations matters:

    1. Verify the member and the book exist (clean ``NotFound`` instead of
       a misleading "no copies available").
    2. ``SELECT ... FOR UPDATE SKIP LOCKED`` an ``AVAILABLE`` copy. The
       ``SKIP LOCKED`` clause lets concurrent borrowers of *different*
       copies of the same book proceed in parallel; without it they would
       serialize on the first row's lock.
    3. Insert the ``loans`` row and flip the picked copy's status to
       ``BORROWED``. The partial unique index
       ``loans_one_active_per_copy_idx`` is a structural backstop: any race
       that skipped past step 2 still cannot create two active loans for
       one copy.

    Raises:
        NotFound: if either the member or the book doesn't exist.
        FailedPrecondition: if no ``AVAILABLE`` copy can be locked, or (in
            the unreachable-but-defensive backstop) if the partial unique
            index rejects the insert.
    """

    member = await session.get(Member, member_id)
    if member is None:
        raise NotFound(f"member {member_id} not found")

    book = await session.get(Book, book_id)
    if book is None:
        raise NotFound(f"book {book_id} not found")

    pick_stmt = (
        select(BookCopy)
        .where(BookCopy.book_id == book_id, BookCopy.status == CopyStatus.AVAILABLE)
        .order_by(BookCopy.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    with _tracer.start_as_current_span("borrow.pick_copy") as pick_span:
        pick_span.set_attribute("library.book_id", book_id)
        copy = (await session.scalars(pick_stmt)).first()
        if copy is None:
            # Distinct from a NotFound: the book exists, but every copy is
            # locked or borrowed. Surface as a span event so dashboards can
            # count contention without grepping logs.
            pick_span.add_event(
                "loan.contention", attributes={"library.book_id": book_id}
            )
            raise FailedPrecondition(
                f"no available copies for book {book_id}"
            )
        pick_span.add_event(
            "copy_picked", attributes={"library.copy_id": copy.id}
        )

    loan = Loan(copy_id=copy.id, member_id=member_id, due_at=due_at)
    session.add(loan)
    copy.status = CopyStatus.BORROWED

    try:
        await session.flush()
    except IntegrityError as exc:
        # Backstop: even with FOR UPDATE SKIP LOCKED, the partial unique
        # index defends against any logic gap that could let two active
        # loans land on the same copy. Translate to FAILED_PRECONDITION
        # so the client sees a coherent error.
        if _ACTIVE_LOAN_INDEX in str(exc):
            raise FailedPrecondition(
                f"copy {copy.id} already has an active loan"
            ) from exc
        raise

    return LoanRow(
        loan=loan,
        book_id=book.id,
        book_title=book.title,
        book_author=book.author,
        member_name=member.name,
    )


# ---------- return ----------


async def return_loan(
    session: AsyncSession,
    *,
    loan_id: int,
    now: datetime,
) -> LoanRow:
    """Close out a loan: stamp ``returned_at`` and flip the copy AVAILABLE.

    Locks the loan row for the duration of the transaction so we don't race
    a concurrent return on the same loan (which would otherwise both see
    ``returned_at IS NULL``, both pass the precondition check, and both
    issue UPDATEs). Once locked we can safely test and set.

    Raises:
        NotFound: when no loan has the given id.
        FailedPrecondition: when the loan is already returned.
    """

    locked_stmt = (
        select(Loan).where(Loan.id == loan_id).with_for_update()
    )
    loan = (await session.scalars(locked_stmt)).first()
    if loan is None:
        raise NotFound(f"loan {loan_id} not found")
    if loan.returned_at is not None:
        raise FailedPrecondition(f"loan {loan_id} is already returned")

    loan.returned_at = now

    copy = await session.get(BookCopy, loan.copy_id)
    if copy is None:
        # Should be unreachable — FK with ON DELETE RESTRICT keeps the copy
        # alive while a loan references it. Defensive raise so we don't
        # silently corrupt state.
        raise FailedPrecondition(
            f"loan {loan_id} references a missing copy {loan.copy_id}"
        )
    copy.status = CopyStatus.AVAILABLE

    await session.flush()

    # Re-derive the joined fields for the response. One small extra query;
    # keeps the proto-conversion code free of join logic.
    return await get_with_joins(session, loan_id=loan_id)


# ---------- single-loan fetch with joins ----------


async def get_with_joins(session: AsyncSession, *, loan_id: int) -> LoanRow:
    """Fetch one loan plus the denormalized title/author/member-name fields."""

    stmt = (
        select(Loan, BookCopy.book_id, Book.title, Book.author, Member.name)
        .join(BookCopy, BookCopy.id == Loan.copy_id)
        .join(Book, Book.id == BookCopy.book_id)
        .join(Member, Member.id == Loan.member_id)
        .where(Loan.id == loan_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise NotFound(f"loan {loan_id} not found")
    loan, book_id, book_title, book_author, member_name = row
    return LoanRow(
        loan=loan,
        book_id=book_id,
        book_title=book_title,
        book_author=book_author,
        member_name=member_name,
    )


# ---------- list with filter ----------


def _apply_loan_filter(stmt, *, filter_value: LoanFilter, now: datetime, fines: FineConfig):
    """Add the predicate corresponding to a :class:`LoanFilter` value."""

    if filter_value == LoanFilter.ACTIVE:
        return stmt.where(Loan.returned_at.is_(None))
    if filter_value == LoanFilter.RETURNED:
        return stmt.where(Loan.returned_at.is_not(None))
    if filter_value == LoanFilter.OVERDUE:
        return stmt.where(and_(Loan.returned_at.is_(None), Loan.due_at < now))
    if filter_value == LoanFilter.HAS_FINE:
        return stmt.where(_fine_expression(now=now, fines=fines) > 0)
    # UNSPECIFIED -> no extra predicate
    return stmt


async def list_loans(
    session: AsyncSession,
    *,
    member_id: int | None,
    book_id: int | None,
    filter_value: LoanFilter,
    limit: int,
    offset: int,
    now: datetime,
    fines: FineConfig,
) -> ListLoansResult:
    """List loans with optional member/book filters and a :class:`LoanFilter`.

    The list response includes the same denormalized fields as the borrow /
    return responses so the UI can render a row without a follow-up call.
    """

    base_filters = []
    if member_id is not None:
        base_filters.append(Loan.member_id == member_id)
    if book_id is not None:
        base_filters.append(BookCopy.book_id == book_id)

    count_stmt = (
        select(func.count())
        .select_from(Loan)
        .join(BookCopy, BookCopy.id == Loan.copy_id)
    )
    for f in base_filters:
        count_stmt = count_stmt.where(f)
    count_stmt = _apply_loan_filter(
        count_stmt, filter_value=filter_value, now=now, fines=fines
    )
    total_count = (await session.scalar(count_stmt)) or 0

    list_stmt = (
        select(Loan, BookCopy.book_id, Book.title, Book.author, Member.name)
        .join(BookCopy, BookCopy.id == Loan.copy_id)
        .join(Book, Book.id == BookCopy.book_id)
        .join(Member, Member.id == Loan.member_id)
    )
    for f in base_filters:
        list_stmt = list_stmt.where(f)
    list_stmt = _apply_loan_filter(
        list_stmt, filter_value=filter_value, now=now, fines=fines
    )
    # Most-recent-first is what the UI wants by default. ``id DESC`` breaks
    # ties so pagination is stable when many rows share a borrowed_at second.
    list_stmt = (
        list_stmt.order_by(Loan.borrowed_at.desc(), Loan.id.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await session.execute(list_stmt)
    rows = [
        LoanRow(loan=loan, book_id=bid, book_title=t, book_author=a, member_name=n)
        for loan, bid, t, a, n in result.all()
    ]
    return ListLoansResult(rows=rows, total_count=total_count)


# ---------- member-scoped query (no pagination per spec) ----------


async def get_member_loans(
    session: AsyncSession,
    *,
    member_id: int,
    filter_value: LoanFilter,
    now: datetime,
    fines: FineConfig,
) -> list[LoanRow]:
    """All loans for one member, ordered most-recent-first.

    The proto returns the full set with no pagination — at neighborhood
    library scale a single member's loan history is bounded.
    """

    member = await session.get(Member, member_id)
    if member is None:
        raise NotFound(f"member {member_id} not found")

    stmt = (
        select(Loan, BookCopy.book_id, Book.title, Book.author, Member.name)
        .join(BookCopy, BookCopy.id == Loan.copy_id)
        .join(Book, Book.id == BookCopy.book_id)
        .join(Member, Member.id == Loan.member_id)
        .where(Loan.member_id == member_id)
    )
    stmt = _apply_loan_filter(stmt, filter_value=filter_value, now=now, fines=fines)
    stmt = stmt.order_by(Loan.borrowed_at.desc(), Loan.id.desc())

    result = await session.execute(stmt)
    return [
        LoanRow(loan=loan, book_id=bid, book_title=t, book_author=a, member_name=n)
        for loan, bid, t, a, n in result.all()
    ]


# ---------- aggregate: outstanding fines for one member ----------


async def sum_member_fines(
    session: AsyncSession,
    *,
    member_id: int,
    now: datetime,
    fines: FineConfig,
) -> int:
    """Sum of ``fine_cents`` across every loan for the given member.

    Implemented as a single SQL aggregate (one round trip) rather than
    fetching loans and summing in Python.
    """

    stmt = (
        select(func.coalesce(func.sum(_fine_expression(now=now, fines=fines)), 0))
        .select_from(Loan)
        .where(Loan.member_id == member_id)
    )
    return int(await session.scalar(stmt) or 0)


__all__ = [
    "FineConfig",
    "ListLoansResult",
    "LoanFilter",
    "LoanRow",
    "borrow",
    "get_member_loans",
    "get_with_joins",
    "list_loans",
    "return_loan",
    "sum_member_fines",
]
