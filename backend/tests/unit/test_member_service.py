"""Unit tests for :class:`library.services.member_service.MemberService`.

Covers validation, email normalization, optional-field plumbing, the
GetMember fines-aggregate wiring, and the ListMembers pagination/search
clamping — all without touching the database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from library.config import Settings
from library.db.models import Member
from library.errors import AlreadyExists, InvalidArgument, NotFound
from library.generated.library.v1 import member_pb2
from library.repositories.members import ListMembersResult
from library.services.member_service import MemberService

_FIXED_TS = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


def _make_member(
    *,
    id: int = 1,
    name: str = "Ada Lovelace",
    email: str = "ada@example.com",
    phone: str | None = None,
    address: str | None = None,
) -> Member:
    m = Member(id=id, name=name, email=email, phone=phone, address=address)
    m.created_at = _FIXED_TS
    m.updated_at = _FIXED_TS
    return m


@pytest.fixture
def settings() -> Settings:
    """Construct a deterministic Settings (defaults plus pinned fine knobs)."""

    return Settings(
        fine_grace_days=14,
        fine_per_day_cents=25,
        fine_cap_cents=2000,
    )


# ---------- create_member ----------


async def test_create_member_strips_inputs_and_normalizes_email(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_create = AsyncMock(
        return_value=_make_member(id=42, name="Ada Lovelace", email="ada@example.com")
    )
    monkeypatch.setattr("library.repositories.members.create", repo_create)

    service = MemberService(fake_session_factory, settings)
    response = await service.create_member(
        member_pb2.CreateMemberRequest(
            name="  Ada Lovelace  ",
            email="  Ada@Example.COM  ",
        )
    )

    repo_create.assert_awaited_once()
    kwargs = repo_create.call_args.kwargs
    assert kwargs["name"] == "Ada Lovelace"           # stripped
    assert kwargs["email"] == "Ada@example.com"       # domain lowercased
    assert kwargs["phone"] is None                    # optional absent
    assert kwargs["address"] is None
    assert response.member.id == 42
    # Create has no loans by definition -> the response carries 0 fines.
    assert response.member.outstanding_fines_cents == 0


async def test_create_member_forwards_optional_fields_when_present(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_create = AsyncMock(
        return_value=_make_member(
            phone="+1-555-0100", address="221B Baker St"
        )
    )
    monkeypatch.setattr("library.repositories.members.create", repo_create)

    request = member_pb2.CreateMemberRequest(name="Ada", email="ada@example.com")
    request.phone.value = "+1-555-0100"
    request.address.value = "221B Baker St"

    service = MemberService(fake_session_factory, settings)
    await service.create_member(request)

    kwargs = repo_create.call_args.kwargs
    assert kwargs["phone"] == "+1-555-0100"
    assert kwargs["address"] == "221B Baker St"


@pytest.mark.parametrize(
    "name,email,expected_field",
    [
        ("", "a@b.com", "name"),
        ("   ", "a@b.com", "name"),
        ("Ada", "", "email"),
        ("Ada", "  ", "email"),
        ("Ada", "not-an-email", "email"),
        ("Ada", "missing@tld", "email"),
        ("Ada", "@b.com", "email"),
    ],
)
async def test_create_member_rejects_invalid_input_without_calling_repo(
    monkeypatch,
    fake_session_factory,
    settings,
    name: str,
    email: str,
    expected_field: str,
) -> None:
    repo_create = AsyncMock()
    monkeypatch.setattr("library.repositories.members.create", repo_create)

    service = MemberService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument) as exc_info:
        await service.create_member(
            member_pb2.CreateMemberRequest(name=name, email=email)
        )

    assert expected_field in str(exc_info.value)
    repo_create.assert_not_awaited()


async def test_create_member_propagates_already_exists_from_repo(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_create = AsyncMock(side_effect=AlreadyExists("email exists"))
    monkeypatch.setattr("library.repositories.members.create", repo_create)

    service = MemberService(fake_session_factory, settings)
    with pytest.raises(AlreadyExists):
        await service.create_member(
            member_pb2.CreateMemberRequest(name="Ada", email="ada@example.com")
        )


# ---------- update_member ----------


async def test_update_member_passes_validated_inputs(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_update = AsyncMock(return_value=_make_member(id=7, name="Ada", email="ada@example.com"))
    monkeypatch.setattr("library.repositories.members.update_member", repo_update)

    service = MemberService(fake_session_factory, settings)
    response = await service.update_member(
        member_pb2.UpdateMemberRequest(
            id=7, name="  Ada  ", email="  Ada@Example.COM  "
        )
    )

    kwargs = repo_update.call_args.kwargs
    assert kwargs["name"] == "Ada"
    assert kwargs["email"] == "Ada@example.com"
    # Spec: Update leaves outstanding_fines_cents at 0; GetMember refreshes it.
    assert response.member.outstanding_fines_cents == 0


@pytest.mark.parametrize(
    "request_args,expected_field",
    [
        ({"id": 0, "name": "X", "email": "x@y.com"}, "id"),
        ({"id": -1, "name": "X", "email": "x@y.com"}, "id"),
        ({"id": 1, "name": "", "email": "x@y.com"}, "name"),
        ({"id": 1, "name": "X", "email": "bad"}, "email"),
    ],
)
async def test_update_member_rejects_invalid_input(
    monkeypatch,
    fake_session_factory,
    settings,
    request_args: dict,
    expected_field: str,
) -> None:
    repo_update = AsyncMock()
    monkeypatch.setattr("library.repositories.members.update_member", repo_update)

    service = MemberService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument) as exc_info:
        await service.update_member(member_pb2.UpdateMemberRequest(**request_args))

    assert expected_field in str(exc_info.value)
    repo_update.assert_not_awaited()


# ---------- get_member ----------


async def test_get_member_aggregates_fines_and_sets_outstanding(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_get = AsyncMock(return_value=_make_member(id=5, name="Ada", email="ada@example.com"))
    repo_sum = AsyncMock(return_value=750)  # 7.50 outstanding
    monkeypatch.setattr("library.repositories.members.get", repo_get)
    monkeypatch.setattr("library.repositories.loans.sum_member_fines", repo_sum)

    service = MemberService(fake_session_factory, settings)
    response = await service.get_member(member_pb2.GetMemberRequest(id=5))

    repo_get.assert_awaited_once()
    repo_sum.assert_awaited_once()
    assert repo_sum.call_args.kwargs["member_id"] == 5
    # Settings-derived FineConfig is forwarded to the aggregate.
    fines = repo_sum.call_args.kwargs["fines"]
    assert fines.grace_days == settings.fine_grace_days
    assert fines.per_day_cents == settings.fine_per_day_cents
    assert fines.cap_cents == settings.fine_cap_cents

    assert response.member.id == 5
    assert response.member.outstanding_fines_cents == 750


async def test_get_member_zero_fines_is_passed_through(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_get = AsyncMock(return_value=_make_member(id=5))
    repo_sum = AsyncMock(return_value=0)
    monkeypatch.setattr("library.repositories.members.get", repo_get)
    monkeypatch.setattr("library.repositories.loans.sum_member_fines", repo_sum)

    service = MemberService(fake_session_factory, settings)
    response = await service.get_member(member_pb2.GetMemberRequest(id=5))

    assert response.member.outstanding_fines_cents == 0


async def test_get_member_rejects_id_zero(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_get = AsyncMock()
    repo_sum = AsyncMock()
    monkeypatch.setattr("library.repositories.members.get", repo_get)
    monkeypatch.setattr("library.repositories.loans.sum_member_fines", repo_sum)

    service = MemberService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.get_member(member_pb2.GetMemberRequest(id=0))

    repo_get.assert_not_awaited()
    repo_sum.assert_not_awaited()


async def test_get_member_propagates_not_found(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_get = AsyncMock(side_effect=NotFound("missing"))
    monkeypatch.setattr("library.repositories.members.get", repo_get)
    monkeypatch.setattr("library.repositories.loans.sum_member_fines", AsyncMock())

    service = MemberService(fake_session_factory, settings)
    with pytest.raises(NotFound):
        await service.get_member(member_pb2.GetMemberRequest(id=999))


# ---------- list_members ----------


async def test_list_members_forwards_pagination_and_search(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_list = AsyncMock(return_value=ListMembersResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.members.list_members", repo_list)

    request = member_pb2.ListMembersRequest(page_size=15, offset=30)
    request.search.value = "  ada  "

    service = MemberService(fake_session_factory, settings)
    await service.list_members(request)

    kwargs = repo_list.call_args.kwargs
    assert kwargs["limit"] == 15
    assert kwargs["offset"] == 30
    assert kwargs["search"] == "ada"


async def test_list_members_skips_fine_aggregation_per_row(
    monkeypatch, fake_session_factory, settings
) -> None:
    """The list response intentionally leaves outstanding_fines_cents=0 to
    avoid N+1 fine queries — only GetMember runs the aggregate."""

    rows = [_make_member(id=1), _make_member(id=2)]
    repo_list = AsyncMock(return_value=ListMembersResult(rows=rows, total_count=2))
    repo_sum = AsyncMock()
    monkeypatch.setattr("library.repositories.members.list_members", repo_list)
    monkeypatch.setattr("library.repositories.loans.sum_member_fines", repo_sum)

    service = MemberService(fake_session_factory, settings)
    response = await service.list_members(member_pb2.ListMembersRequest())

    repo_sum.assert_not_awaited()
    assert len(response.members) == 2
    assert all(m.outstanding_fines_cents == 0 for m in response.members)
    assert response.total_count == 2


async def test_list_members_rejects_negative_pagination(
    monkeypatch, fake_session_factory, settings
) -> None:
    repo_list = AsyncMock()
    monkeypatch.setattr("library.repositories.members.list_members", repo_list)

    service = MemberService(fake_session_factory, settings)
    with pytest.raises(InvalidArgument):
        await service.list_members(member_pb2.ListMembersRequest(page_size=-1))
    repo_list.assert_not_awaited()
