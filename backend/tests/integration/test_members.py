"""End-to-end coverage of the four member RPCs.

Same setup as :mod:`tests.integration.test_books` — real client, in-process
server, real Postgres. The DB is truncated between tests by the autouse
fixture in ``conftest.py``.
"""

from __future__ import annotations

import grpc
import pytest

from library.generated.library.v1 import member_pb2


def _create_member_request(
    *,
    name: str = "Ada Lovelace",
    email: str = "ada@example.com",
    phone: str | None = "+44 20 7946 0000",
    address: str | None = "1 St James's Square, London",
) -> member_pb2.CreateMemberRequest:
    req = member_pb2.CreateMemberRequest(name=name, email=email)
    if phone is not None:
        req.phone.value = phone
    if address is not None:
        req.address.value = address
    return req


# ---------- CreateMember ----------


async def test_create_member_happy(member_stub) -> None:
    response = await member_stub.CreateMember(_create_member_request())
    member = response.member
    assert member.id > 0
    assert member.name == "Ada Lovelace"
    assert member.email == "ada@example.com"
    assert member.HasField("phone") and member.phone.value == "+44 20 7946 0000"
    assert member.HasField("address")
    assert member.outstanding_fines_cents == 0  # Phase 5 wires the real value
    assert member.created_at.seconds > 0


async def test_create_member_optional_fields_omitted(member_stub) -> None:
    response = await member_stub.CreateMember(
        _create_member_request(phone=None, address=None)
    )
    assert not response.member.HasField("phone")
    assert not response.member.HasField("address")


@pytest.mark.parametrize(
    ("name", "email", "expected_field"),
    [
        ("", "x@example.com", "name"),
        ("   ", "x@example.com", "name"),
        ("Name", "", "email"),
    ],
)
async def test_create_member_invalid_argument(
    member_stub, name: str, email: str, expected_field: str
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await member_stub.CreateMember(
            member_pb2.CreateMemberRequest(name=name, email=email)
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert expected_field in exc_info.value.details()


async def test_create_member_duplicate_email_case_insensitive(member_stub) -> None:
    await member_stub.CreateMember(_create_member_request(email="bob@example.com"))
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await member_stub.CreateMember(
            _create_member_request(name="Bob Two", email="BOB@example.com")
        )
    assert exc_info.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert "already exists" in exc_info.value.details().lower()


# ---------- GetMember ----------


async def test_get_member_happy(member_stub) -> None:
    created = (await member_stub.CreateMember(_create_member_request())).member
    fetched = (
        await member_stub.GetMember(member_pb2.GetMemberRequest(id=created.id))
    ).member
    assert fetched.id == created.id
    assert fetched.email == created.email


async def test_get_member_not_found(member_stub) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await member_stub.GetMember(member_pb2.GetMemberRequest(id=999_999))
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


# ---------- ListMembers ----------


async def test_list_members_empty(member_stub) -> None:
    response = await member_stub.ListMembers(member_pb2.ListMembersRequest())
    assert response.total_count == 0
    assert list(response.members) == []


async def test_list_members_orders_by_name(member_stub) -> None:
    await member_stub.CreateMember(
        _create_member_request(name="Charlie", email="c@example.com")
    )
    await member_stub.CreateMember(
        _create_member_request(name="Alice", email="a@example.com")
    )
    await member_stub.CreateMember(
        _create_member_request(name="Bob", email="b@example.com")
    )
    response = await member_stub.ListMembers(member_pb2.ListMembersRequest())
    assert [m.name for m in response.members] == ["Alice", "Bob", "Charlie"]


async def test_list_members_search_by_name_or_email(member_stub) -> None:
    await member_stub.CreateMember(
        _create_member_request(name="Ada Lovelace", email="ada@example.com")
    )
    await member_stub.CreateMember(
        _create_member_request(name="Grace Hopper", email="grace@example.com")
    )

    req = member_pb2.ListMembersRequest()
    req.search.value = "Ada"
    response = await member_stub.ListMembers(req)
    assert response.total_count == 1
    assert response.members[0].name == "Ada Lovelace"

    req.search.value = "grace@"
    response = await member_stub.ListMembers(req)
    assert response.total_count == 1
    assert response.members[0].name == "Grace Hopper"


async def test_list_members_pagination(member_stub) -> None:
    for i in range(5):
        await member_stub.CreateMember(
            _create_member_request(name=f"Member {i:02d}", email=f"m{i}@example.com")
        )
    page1 = await member_stub.ListMembers(
        member_pb2.ListMembersRequest(page_size=2, offset=0)
    )
    assert page1.total_count == 5
    assert [m.name for m in page1.members] == ["Member 00", "Member 01"]

    page2 = await member_stub.ListMembers(
        member_pb2.ListMembersRequest(page_size=2, offset=2)
    )
    assert [m.name for m in page2.members] == ["Member 02", "Member 03"]


# ---------- UpdateMember ----------


async def test_update_member_happy(member_stub) -> None:
    created = (
        await member_stub.CreateMember(_create_member_request(phone="OLD"))
    ).member
    request = member_pb2.UpdateMemberRequest(
        id=created.id,
        name="Ada L.",
        email=created.email,
    )
    request.phone.value = "NEW"
    response = await member_stub.UpdateMember(request)
    assert response.member.name == "Ada L."
    assert response.member.phone.value == "NEW"


async def test_update_member_clear_optional_fields(member_stub) -> None:
    created = (
        await member_stub.CreateMember(_create_member_request(phone="X", address="Y"))
    ).member
    response = await member_stub.UpdateMember(
        member_pb2.UpdateMemberRequest(id=created.id, name="Ada", email=created.email)
    )
    assert not response.member.HasField("phone")
    assert not response.member.HasField("address")


async def test_update_member_to_duplicate_email_rejected(member_stub) -> None:
    a = (await member_stub.CreateMember(_create_member_request(email="a@example.com"))).member
    await member_stub.CreateMember(
        _create_member_request(name="Bob", email="b@example.com")
    )
    request = member_pb2.UpdateMemberRequest(
        id=a.id, name=a.name, email="B@EXAMPLE.COM"
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await member_stub.UpdateMember(request)
    assert exc_info.value.code() == grpc.StatusCode.ALREADY_EXISTS


async def test_update_member_not_found(member_stub) -> None:
    request = member_pb2.UpdateMemberRequest(
        id=999_999, name="X", email="x@example.com"
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await member_stub.UpdateMember(request)
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
