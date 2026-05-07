"""Member persistence.

Same contract as :mod:`library.repositories.books`: every line of SQL touching
``members`` lives here, and the only errors that escape are the typed
:mod:`library.errors` exceptions. The service layer owns transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from library.db.models import Member
from library.errors import AlreadyExists, NotFound

# The unique constraint on lower(email) is named in the migration; checking
# the index name rather than the message text gives us a stable hook for
# IntegrityError -> AlreadyExists translation across Postgres versions.
_EMAIL_UNIQUE_INDEX: Final[str] = "members_email_unique_idx"


@dataclass(slots=True)
class ListMembersResult:
    rows: list[Member]
    total_count: int


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_predicate(search: str):
    pattern = _escape_like(search.lower()) + "%"
    return or_(
        func.lower(Member.name).like(pattern, escape="\\"),
        func.lower(Member.email).like(pattern, escape="\\"),
    )


def _is_email_unique_violation(exc: IntegrityError) -> bool:
    """Return True iff the IntegrityError came from the email uniqueness index.

    asyncpg's ``UniqueViolationError`` carries the offending constraint name
    in the chained DBAPI error. Falling back to substring match on the
    serialized error text covers the case where the diagnostic shape changes.
    """

    orig = getattr(exc, "orig", None)
    constraint = getattr(orig, "constraint_name", None)
    if constraint == _EMAIL_UNIQUE_INDEX:
        return True
    return _EMAIL_UNIQUE_INDEX in str(exc)


async def create(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    phone: str | None,
    address: str | None,
) -> Member:
    """Insert a member; translate the email-uniqueness violation."""

    member = Member(name=name, email=email, phone=phone, address=address)
    session.add(member)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_email_unique_violation(exc):
            raise AlreadyExists(f"a member with email {email!r} already exists") from exc
        raise
    return member


async def get(session: AsyncSession, member_id: int) -> Member:
    member = await session.get(Member, member_id)
    if member is None:
        raise NotFound(f"member {member_id} not found")
    return member


async def list_members(
    session: AsyncSession,
    *,
    search: str | None,
    limit: int,
    offset: int,
) -> ListMembersResult:
    count_stmt = select(func.count()).select_from(Member)
    if search:
        count_stmt = count_stmt.where(_search_predicate(search))
    total_count = (await session.scalar(count_stmt)) or 0

    list_stmt = select(Member).order_by(Member.name.asc(), Member.id.asc()).limit(limit).offset(offset)
    if search:
        list_stmt = list_stmt.where(_search_predicate(search))

    rows = list((await session.scalars(list_stmt)).all())
    return ListMembersResult(rows=rows, total_count=total_count)


async def update_member(
    session: AsyncSession,
    member_id: int,
    *,
    name: str,
    email: str,
    phone: str | None,
    address: str | None,
) -> Member:
    member = await session.get(Member, member_id)
    if member is None:
        raise NotFound(f"member {member_id} not found")

    member.name = name
    member.email = email
    member.phone = phone
    member.address = address
    # Application-managed updated_at (per design/01-database.md decision row 14
    # — no DB trigger). Use a Python datetime so the value is observable on
    # the in-memory ORM instance immediately, without needing a post-flush
    # refresh that would have to go through the async bridge.
    member.updated_at = datetime.now(timezone.utc)

    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_email_unique_violation(exc):
            raise AlreadyExists(f"a member with email {email!r} already exists") from exc
        raise
    return member


__all__ = [
    "ListMembersResult",
    "create",
    "get",
    "list_members",
    "update_member",
]
