"""Member-domain service.

Mirrors :mod:`library.services.book_service` for the four member RPCs.
``outstanding_fines_cents`` is computed via the loans repository's
``sum_member_fines`` aggregate on the ``GetMember`` path (per phase-5 spec).
The other paths (Create / Update / List) leave it at zero — Create has no
loans by definition, and ListMembers paying N+1 queries for fines on every
row in a paginated table isn't worth it for the dashboard's purposes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from library.config import Settings
from library.db.models import Member
from library.errors import InvalidArgument
from library.generated.library.v1 import library_pb2
from library.repositories import loans as loans_repo
from library.repositories import members as members_repo
from library.repositories.loans import FineConfig
from library.services.conversions import (
    clamp_pagination,
    datetime_to_pb,
    normalize_search,
)


class MemberService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._fines = FineConfig(
            grace_days=settings.fine_grace_days,
            per_day_cents=settings.fine_per_day_cents,
            cap_cents=settings.fine_cap_cents,
        )

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
            # A freshly created member has no loans, so fines are 0 by
            # definition — no need to issue the aggregate query.
            member_proto = _member_to_proto(member, outstanding_fines_cents=0)

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
            # Phase-5 spec only requires the real fines value on GetMember.
            # Update is a write op; clients can refresh via GetMember if
            # they need the post-update aggregate.
            member_proto = _member_to_proto(member, outstanding_fines_cents=0)

        return library_pb2.UpdateMemberResponse(member=member_proto)

    # ---------- reads ----------

    async def get_member(
        self, request: library_pb2.GetMemberRequest
    ) -> library_pb2.GetMemberResponse:
        if request.id <= 0:
            raise InvalidArgument("id is required")

        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            member = await members_repo.get(session, request.id)
            outstanding = await loans_repo.sum_member_fines(
                session,
                member_id=request.id,
                now=now,
                fines=self._fines,
            )
            member_proto = _member_to_proto(
                member, outstanding_fines_cents=outstanding
            )

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

        # ListMembers leaves outstanding_fines_cents at zero — paying the
        # per-row aggregate query for every result page would multiply the
        # query count and the dashboard's "total outstanding fines" tile
        # is system-wide rather than per-row anyway.
        return library_pb2.ListMembersResponse(
            members=[_member_to_proto(m, outstanding_fines_cents=0) for m in result.rows],
            total_count=result.total_count,
        )


def _member_to_proto(member: Member, *, outstanding_fines_cents: int) -> library_pb2.Member:
    proto = library_pb2.Member(
        id=member.id,
        name=member.name,
        email=member.email,
        outstanding_fines_cents=outstanding_fines_cents,
    )
    if member.phone is not None:
        proto.phone.value = member.phone
    if member.address is not None:
        proto.address.value = member.address
    proto.created_at.CopyFrom(datetime_to_pb(member.created_at))
    proto.updated_at.CopyFrom(datetime_to_pb(member.updated_at))
    return proto


__all__ = ["MemberService"]
