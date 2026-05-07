"""Loan-domain service: borrow / return / list / member-scoped queries.

Orchestrates :mod:`library.repositories.loans` (SQL) and
:mod:`library.services.fines` (formula). The repository computes fines in
SQL where it has to (the ``HAS_FINE`` predicate, the member-fines
aggregate); for individual loan responses the service computes fines in
Python via :func:`compute_fine_cents` so a single source of truth — the
function — drives both the unit tests and the wire output.

``due_at`` defaults to ``now + DEFAULT_LOAN_DAYS`` when the client doesn't
specify one. ``now`` and the fine config are captured at service
construction from :mod:`library.config`; tests can swap them in by
constructing the service with overrides.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from library.config import Settings
from library.errors import InvalidArgument
from library.generated.library.v1 import library_pb2
from library.repositories import loans as loans_repo
from library.repositories.loans import FineConfig, LoanFilter, LoanRow
from library.services.conversions import clamp_pagination, datetime_to_pb
from library.services.fines import compute_fine_cents


class LoanService:
    """Handlers for the four loan RPCs.

    The settings are injected so tests can construct the service with
    different fine knobs without touching env vars at import time.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._fines = FineConfig(
            grace_days=settings.fine_grace_days,
            per_day_cents=settings.fine_per_day_cents,
            cap_cents=settings.fine_cap_cents,
        )

    # ---------- mutations ----------

    async def borrow_book(
        self, request: library_pb2.BorrowBookRequest
    ) -> library_pb2.BorrowBookResponse:
        if request.book_id <= 0:
            raise InvalidArgument("book_id is required")
        if request.member_id <= 0:
            raise InvalidArgument("member_id is required")

        now = _now_utc()
        due_at = (
            request.due_at.ToDatetime(tzinfo=timezone.utc)
            if request.HasField("due_at")
            else now + timedelta(days=self._settings.default_loan_days)
        )
        if due_at <= now:
            raise InvalidArgument("due_at must be in the future")

        async with self._session_factory.begin() as session:
            row = await loans_repo.borrow(
                session,
                book_id=request.book_id,
                member_id=request.member_id,
                due_at=due_at,
            )
            loan_proto = self._loan_row_to_proto(row, now=now)

        return library_pb2.BorrowBookResponse(loan=loan_proto)

    async def return_book(
        self, request: library_pb2.ReturnBookRequest
    ) -> library_pb2.ReturnBookResponse:
        if request.loan_id <= 0:
            raise InvalidArgument("loan_id is required")

        now = _now_utc()
        async with self._session_factory.begin() as session:
            row = await loans_repo.return_loan(
                session, loan_id=request.loan_id, now=now
            )
            loan_proto = self._loan_row_to_proto(row, now=now)

        return library_pb2.ReturnBookResponse(loan=loan_proto)

    # ---------- reads ----------

    async def list_loans(
        self, request: library_pb2.ListLoansRequest
    ) -> library_pb2.ListLoansResponse:
        page_size, offset = clamp_pagination(
            page_size=request.page_size, offset=request.offset
        )
        member_id = (
            request.member_id.value if request.HasField("member_id") else None
        )
        book_id = request.book_id.value if request.HasField("book_id") else None
        filter_value = _proto_to_domain_filter(request.filter)

        now = _now_utc()
        async with self._session_factory() as session:
            result = await loans_repo.list_loans(
                session,
                member_id=member_id,
                book_id=book_id,
                filter_value=filter_value,
                limit=page_size,
                offset=offset,
                now=now,
                fines=self._fines,
            )

        return library_pb2.ListLoansResponse(
            loans=[self._loan_row_to_proto(row, now=now) for row in result.rows],
            total_count=result.total_count,
        )

    async def get_member_loans(
        self, request: library_pb2.GetMemberLoansRequest
    ) -> library_pb2.GetMemberLoansResponse:
        if request.member_id <= 0:
            raise InvalidArgument("member_id is required")
        filter_value = _proto_to_domain_filter(request.filter)

        now = _now_utc()
        async with self._session_factory() as session:
            rows = await loans_repo.get_member_loans(
                session,
                member_id=request.member_id,
                filter_value=filter_value,
                now=now,
                fines=self._fines,
            )

        return library_pb2.GetMemberLoansResponse(
            loans=[self._loan_row_to_proto(row, now=now) for row in rows],
        )

    # ---------- helpers ----------

    def _loan_row_to_proto(self, row: LoanRow, *, now: datetime) -> library_pb2.Loan:
        loan = row.loan
        is_active = loan.returned_at is None
        is_overdue = is_active and loan.due_at < now
        fine_cents = compute_fine_cents(
            due_at=loan.due_at,
            returned_at=loan.returned_at,
            now=now,
            grace_days=self._fines.grace_days,
            per_day_cents=self._fines.per_day_cents,
            cap_cents=self._fines.cap_cents,
        )

        proto = library_pb2.Loan(
            id=loan.id,
            member_id=loan.member_id,
            book_id=row.book_id,
            copy_id=loan.copy_id,
            book_title=row.book_title,
            book_author=row.book_author,
            member_name=row.member_name,
            overdue=is_overdue,
            fine_cents=fine_cents,
        )
        proto.borrowed_at.CopyFrom(datetime_to_pb(loan.borrowed_at))
        proto.due_at.CopyFrom(datetime_to_pb(loan.due_at))
        if loan.returned_at is not None:
            proto.returned_at.CopyFrom(datetime_to_pb(loan.returned_at))
        return proto


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _proto_to_domain_filter(proto_filter: int) -> LoanFilter:
    """Map the proto enum int to the domain enum.

    The integer values match by construction (LoanFilter is defined to
    mirror the proto enum), but going through this function makes the
    layering explicit and gives us a single place to handle unknown values.
    """

    try:
        return LoanFilter(proto_filter)
    except ValueError:
        # Unknown enum value (e.g. a future client sending a filter we
        # don't recognize). Treat as UNSPECIFIED — return everything —
        # rather than reject.
        return LoanFilter.UNSPECIFIED


__all__ = ["LoanService"]
