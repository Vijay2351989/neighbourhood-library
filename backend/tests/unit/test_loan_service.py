"""Unit tests for :class:`library.services.loan_service.LoanService`.

The loan service is the most validation-dense — it owns due-date defaulting,
overdue detection, fine computation wiring, and the unknown-filter fallback.
``_now_utc`` is monkeypatched per test so date math stays deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from library.config import Settings
from library.db.models import Loan
from library.errors import FailedPrecondition, InvalidArgument
from library.generated.library.v1 import loan_pb2
from library.repositories.loans import (
    FineConfig,
    ListLoansResult,
    LoanFilter,
    LoanRow,
)
from library.services.loan_service import LoanService

_NOW = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        default_loan_days=14,
        fine_grace_days=14,
        fine_per_day_cents=25,
        fine_cap_cents=2000,
    )


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch) -> None:
    """Pin _now_utc to a fixed instant for every test in this module."""

    monkeypatch.setattr(
        "library.services.loan_service._now_utc", lambda: _NOW
    )


def _make_loan(
    *,
    id: int = 1,
    copy_id: int = 10,
    member_id: int = 100,
    borrowed_at: datetime = _NOW,
    due_at: datetime = _NOW + timedelta(days=14),
    returned_at: datetime | None = None,
) -> Loan:
    loan = Loan(
        id=id,
        copy_id=copy_id,
        member_id=member_id,
    )
    loan.borrowed_at = borrowed_at
    loan.due_at = due_at
    loan.returned_at = returned_at
    return loan


def _make_row(*, loan: Loan, book_id: int = 50) -> LoanRow:
    return LoanRow(
        loan=loan,
        book_id=book_id,
        book_title="Mistborn",
        book_author="Sanderson",
        member_name="Ada Lovelace",
    )


# ---------- borrow_book ----------


async def test_borrow_defaults_due_at_to_now_plus_default_loan_days(
    monkeypatch, fake_session_factory, settings
) -> None:
    captured: dict = {}

    async def fake_borrow(session, *, book_id, member_id, due_at):
        captured.update(book_id=book_id, member_id=member_id, due_at=due_at)
        return _make_row(loan=_make_loan(due_at=due_at))

    monkeypatch.setattr("library.repositories.loans.borrow", fake_borrow)

    service = LoanService(fake_session_factory, settings)
    await service.borrow_book(
        loan_pb2.BorrowBookRequest(book_id=1, member_id=2)
    )

    assert captured["due_at"] == _NOW + timedelta(days=settings.default_loan_days)


async def test_borrow_honors_client_supplied_due_at(
    monkeypatch, fake_session_factory, settings
) -> None:
    custom_due = _NOW + timedelta(days=30)
    captured: dict = {}

    async def fake_borrow(session, *, book_id, member_id, due_at):
        captured["due_at"] = due_at
        return _make_row(loan=_make_loan(due_at=due_at))

    monkeypatch.setattr("library.repositories.loans.borrow", fake_borrow)

    request = loan_pb2.BorrowBookRequest(book_id=1, member_id=2)
    request.due_at.FromDatetime(custom_due)

    service = LoanService(fake_session_factory, settings)
    await service.borrow_book(request)

    assert captured["due_at"] == custom_due


@pytest.mark.parametrize(
    "book_id,member_id,expected_field",
    [
        (0, 1, "book_id"),
        (-1, 1, "book_id"),
        (1, 0, "member_id"),
        (1, -1, "member_id"),
    ],
)
async def test_borrow_rejects_invalid_ids(
    monkeypatch,
    fake_session_factory,
    settings,
    book_id: int,
    member_id: int,
    expected_field: str,
) -> None:
    repo_borrow = AsyncMock()
    monkeypatch.setattr("library.repositories.loans.borrow", repo_borrow)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument) as exc_info:
        await service.borrow_book(
            loan_pb2.BorrowBookRequest(book_id=book_id, member_id=member_id)
        )
    assert expected_field in str(exc_info.value)
    repo_borrow.assert_not_awaited()


async def test_borrow_rejects_past_due_at(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_borrow = AsyncMock()
    monkeypatch.setattr("library.repositories.loans.borrow", repo_borrow)

    request = loan_pb2.BorrowBookRequest(book_id=1, member_id=2)
    request.due_at.FromDatetime(_NOW - timedelta(days=1))

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.borrow_book(request)
    repo_borrow.assert_not_awaited()


async def test_borrow_rejects_due_at_equal_to_now(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_borrow = AsyncMock()
    monkeypatch.setattr("library.repositories.loans.borrow", repo_borrow)

    request = loan_pb2.BorrowBookRequest(book_id=1, member_id=2)
    request.due_at.FromDatetime(_NOW)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.borrow_book(request)
    repo_borrow.assert_not_awaited()


async def test_borrow_propagates_failed_precondition(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_borrow = AsyncMock(side_effect=FailedPrecondition("no copies available"))
    monkeypatch.setattr("library.repositories.loans.borrow", repo_borrow)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(FailedPrecondition):
        await service.borrow_book(
            loan_pb2.BorrowBookRequest(book_id=1, member_id=2)
        )


async def test_borrow_response_carries_zero_fine_and_not_overdue(
    monkeypatch, fake_session_factory, settings
) -> None:
    """A fresh borrow is in-flight; fine_cents=0, overdue=False."""

    loan = _make_loan(due_at=_NOW + timedelta(days=14), returned_at=None)
    monkeypatch.setattr(
        "library.repositories.loans.borrow",
        AsyncMock(return_value=_make_row(loan=loan)),
    )

    service = LoanService(fake_session_factory, settings)
    response = await service.borrow_book(
        loan_pb2.BorrowBookRequest(book_id=1, member_id=2)
    )

    assert response.loan.fine_cents == 0
    assert response.loan.overdue is False


# ---------- return_book ----------


async def test_return_book_computes_fine_for_late_return(
    monkeypatch, fake_session_factory, settings
) -> None:
    """Due 20 days ago, returned now: 20 - 14 (grace) = 6 days × 25¢ = 150¢."""

    returned_loan = _make_loan(
        due_at=_NOW - timedelta(days=20),
        returned_at=_NOW,
    )
    repo_return = AsyncMock(return_value=_make_row(loan=returned_loan))
    monkeypatch.setattr("library.repositories.loans.return_loan", repo_return)

    service = LoanService(fake_session_factory, settings)
    response = await service.return_book(loan_pb2.ReturnBookRequest(loan_id=1))

    assert response.loan.fine_cents == 150
    assert response.loan.HasField("returned_at")


async def test_return_book_on_time_has_zero_fine(
    monkeypatch, fake_session_factory, settings
) -> None:
    on_time = _make_loan(
        due_at=_NOW + timedelta(days=5),
        returned_at=_NOW,
    )
    repo_return = AsyncMock(return_value=_make_row(loan=on_time))
    monkeypatch.setattr("library.repositories.loans.return_loan", repo_return)

    service = LoanService(fake_session_factory, settings)
    response = await service.return_book(loan_pb2.ReturnBookRequest(loan_id=1))

    assert response.loan.fine_cents == 0


async def test_return_book_rejects_loan_id_zero(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_return = AsyncMock()
    monkeypatch.setattr("library.repositories.loans.return_loan", repo_return)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.return_book(loan_pb2.ReturnBookRequest(loan_id=0))
    repo_return.assert_not_awaited()


async def test_return_book_propagates_failed_precondition(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_return = AsyncMock(side_effect=FailedPrecondition("already returned"))
    monkeypatch.setattr("library.repositories.loans.return_loan", repo_return)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(FailedPrecondition):
        await service.return_book(loan_pb2.ReturnBookRequest(loan_id=1))


# ---------- list_loans ----------


async def test_list_loans_forwards_all_filters(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_list = AsyncMock(return_value=ListLoansResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.loans.list_loans", repo_list)

    request = loan_pb2.ListLoansRequest(
        filter=loan_pb2.LOAN_FILTER_OVERDUE,
        page_size=10,
        offset=20,
    )
    request.member_id.value = 5
    request.book_id.value = 7

    service = LoanService(fake_session_factory, settings)
    await service.list_loans(request)

    kwargs = repo_list.call_args.kwargs
    assert kwargs["member_id"] == 5
    assert kwargs["book_id"] == 7
    assert kwargs["filter_value"] == LoanFilter.OVERDUE
    assert kwargs["limit"] == 10
    assert kwargs["offset"] == 20


async def test_list_loans_unknown_filter_falls_back_to_unspecified(
    monkeypatch, fake_session_factory, settings
) -> None:
    """A future client sending an enum value we don't recognize gets
    treated as 'no filter' rather than rejected."""

    repo_list = AsyncMock(return_value=ListLoansResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.loans.list_loans", repo_list)

    # Bypass the proto enum's normal validation by assigning the raw int
    # via the descriptor — protobuf accepts unknown ints on the int field.
    request = loan_pb2.ListLoansRequest()
    request.filter = 9999  # not a defined LoanFilter member

    service = LoanService(fake_session_factory, settings)
    await service.list_loans(request)

    assert repo_list.call_args.kwargs["filter_value"] == LoanFilter.UNSPECIFIED


async def test_list_loans_rejects_negative_pagination(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_list = AsyncMock()
    monkeypatch.setattr("library.repositories.loans.list_loans", repo_list)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.list_loans(loan_pb2.ListLoansRequest(offset=-1))
    repo_list.assert_not_awaited()


async def test_list_loans_marks_overdue_in_response(
    monkeypatch, fake_session_factory, settings
) -> None:
    """An active loan with due_at < now should surface as overdue=True."""

    overdue_loan = _make_loan(
        due_at=_NOW - timedelta(days=5),
        returned_at=None,
    )
    repo_list = AsyncMock(
        return_value=ListLoansResult(
            rows=[_make_row(loan=overdue_loan)], total_count=1
        )
    )
    monkeypatch.setattr("library.repositories.loans.list_loans", repo_list)

    service = LoanService(fake_session_factory, settings)
    response = await service.list_loans(loan_pb2.ListLoansRequest())

    assert len(response.loans) == 1
    assert response.loans[0].overdue is True


# ---------- get_member_loans ----------


async def test_get_member_loans_forwards_pagination(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_gml = AsyncMock(return_value=ListLoansResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.loans.get_member_loans", repo_gml)

    service = LoanService(fake_session_factory, settings)
    await service.get_member_loans(
        loan_pb2.GetMemberLoansRequest(
            member_id=5, page_size=3, offset=6
        )
    )

    kwargs = repo_gml.call_args.kwargs
    assert kwargs["member_id"] == 5
    assert kwargs["limit"] == 3
    assert kwargs["offset"] == 6


async def test_get_member_loans_returns_total_count_in_response(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_gml = AsyncMock(return_value=ListLoansResult(rows=[], total_count=42))
    monkeypatch.setattr("library.repositories.loans.get_member_loans", repo_gml)

    service = LoanService(fake_session_factory, settings)
    response = await service.get_member_loans(
        loan_pb2.GetMemberLoansRequest(member_id=5)
    )

    assert response.total_count == 42


async def test_get_member_loans_rejects_member_id_zero(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_gml = AsyncMock()
    monkeypatch.setattr("library.repositories.loans.get_member_loans", repo_gml)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.get_member_loans(
            loan_pb2.GetMemberLoansRequest(member_id=0)
        )
    repo_gml.assert_not_awaited()


async def test_get_member_loans_rejects_negative_pagination(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_gml = AsyncMock()
    monkeypatch.setattr("library.repositories.loans.get_member_loans", repo_gml)

    service = LoanService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.get_member_loans(
            loan_pb2.GetMemberLoansRequest(member_id=1, offset=-1)
        )
    repo_gml.assert_not_awaited()


async def test_loan_service_forwards_fine_config_from_settings(
    monkeypatch, fake_session_factory, settings
) -> None:
    """Tests pin settings; the service should build FineConfig from them."""

    captured: dict = {}

    async def fake_list(session, **kwargs):
        captured["fines"] = kwargs["fines"]
        return ListLoansResult(rows=[], total_count=0)

    monkeypatch.setattr("library.repositories.loans.list_loans", fake_list)

    service = LoanService(fake_session_factory, settings)
    await service.list_loans(loan_pb2.ListLoansRequest())

    fines: FineConfig = captured["fines"]
    assert fines.grace_days == settings.fine_grace_days
    assert fines.per_day_cents == settings.fine_per_day_cents
    assert fines.cap_cents == settings.fine_cap_cents
