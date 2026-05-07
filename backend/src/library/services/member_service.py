"""Member-domain service.

Mirrors :mod:`library.services.book_service` for the four member RPCs. The
``outstanding_fines_cents`` field on the ``Member`` proto is hardcoded to 0
here; Phase 5 swaps this for a real aggregate over the member's loans once
the fine formula and loan repository land.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from library.db.models import Member
from library.errors import InvalidArgument
from library.generated.library.v1 import library_pb2
from library.repositories import members as members_repo
from library.services.conversions import (
    clamp_pagination,
    datetime_to_pb,
    normalize_search,
)


class MemberService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ---------- mutations ----------

    async def create_member(
        self, request: library_pb2.CreateMemberRequest
    ) -> library_pb2.CreateMemberResponse:
        name = request.name.strip()
        email = request.email.strip()
        if not name:
            raise InvalidArgument("name is required")
        if not email:
            raise InvalidArgument("email is required")

        phone = request.phone.value if request.HasField("phone") else None
        address = request.address.value if request.HasField("address") else None

        async with self._session_factory.begin() as session:
            member = await members_repo.create(
                session,
                name=name,
                email=email,
                phone=phone,
                address=address,
            )
            member_proto = _member_to_proto(member)

        return library_pb2.CreateMemberResponse(member=member_proto)

    async def update_member(
        self, request: library_pb2.UpdateMemberRequest
    ) -> library_pb2.UpdateMemberResponse:
        if request.id <= 0:
            raise InvalidArgument("id is required")
        name = request.name.strip()
        email = request.email.strip()
        if not name:
            raise InvalidArgument("name is required")
        if not email:
            raise InvalidArgument("email is required")

        phone = request.phone.value if request.HasField("phone") else None
        address = request.address.value if request.HasField("address") else None

        async with self._session_factory.begin() as session:
            member = await members_repo.update_member(
                session,
                request.id,
                name=name,
                email=email,
                phone=phone,
                address=address,
            )
            member_proto = _member_to_proto(member)

        return library_pb2.UpdateMemberResponse(member=member_proto)

    # ---------- reads ----------

    async def get_member(
        self, request: library_pb2.GetMemberRequest
    ) -> library_pb2.GetMemberResponse:
        if request.id <= 0:
            raise InvalidArgument("id is required")

        async with self._session_factory() as session:
            member = await members_repo.get(session, request.id)
            member_proto = _member_to_proto(member)

        return library_pb2.GetMemberResponse(member=member_proto)

    async def list_members(
        self, request: library_pb2.ListMembersRequest
    ) -> library_pb2.ListMembersResponse:
        page_size, offset = clamp_pagination(
            page_size=request.page_size,
            offset=request.offset,
        )
        search = (
            normalize_search(request.search.value) if request.HasField("search") else None
        )

        async with self._session_factory() as session:
            result = await members_repo.list_members(
                session,
                search=search,
                limit=page_size,
                offset=offset,
            )

        return library_pb2.ListMembersResponse(
            members=[_member_to_proto(m) for m in result.rows],
            total_count=result.total_count,
        )


def _member_to_proto(member: Member) -> library_pb2.Member:
    proto = library_pb2.Member(
        id=member.id,
        name=member.name,
        email=member.email,
        # TODO(phase-5): replace with sum of compute_fine_cents over the
        # member's loans once the loan repository and fine formula exist.
        outstanding_fines_cents=0,
    )
    if member.phone is not None:
        proto.phone.value = member.phone
    if member.address is not None:
        proto.address.value = member.address
    proto.created_at.CopyFrom(datetime_to_pb(member.created_at))
    proto.updated_at.CopyFrom(datetime_to_pb(member.updated_at))
    return proto


__all__ = ["MemberService"]
