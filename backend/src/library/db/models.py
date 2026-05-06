"""SQLAlchemy 2.0 typed ORM models for the Neighborhood Library.

These mappings mirror the canonical schema in ``docs/design/01-database.md`` §1
exactly. The Alembic migration ``alembic/versions/0001_initial.py`` is the
authoritative DDL — it is hand-authored against that design doc, and these
models are kept in lockstep with it.

Modeling notes
--------------
* All timestamps are ``TIMESTAMPTZ`` (timezone-aware) — the application stores
  UTC and the UI renders in the staff member's local timezone (see overview).
* ``updated_at`` is application-managed (decision row 14): the repository
  layer in Phase 4 sets it on update; we do **not** install a database trigger.
  We still declare a server default of ``NOW()`` so a freshly inserted row has
  a sensible value without the application having to set it.
* The ``copy_status`` Postgres enum is created by the migration. We map to a
  Python ``str``-valued enum for ergonomic equality checks (``status ==
  CopyStatus.AVAILABLE``).
* Relationships are declared with ``Mapped[...]`` types so static analyzers
  see real types rather than ``Any``.
* No ``unique=True`` on ``Member.email``: case-insensitive uniqueness is
  enforced by the ``members_email_unique_idx`` index on ``lower(email)``,
  which is created in the migration. Declaring a column-level unique here
  would create a duplicate (case-sensitive) constraint we don't want.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base class for all ORM models in this project.

    Using a single base class lets Alembic's ``env.py`` collect every model's
    metadata via ``Base.metadata`` for autogenerate support down the line.
    """


class CopyStatus(str, enum.Enum):
    """Lifecycle status of a single physical book copy.

    Inheriting from ``str`` makes ``CopyStatus.AVAILABLE == "AVAILABLE"`` true,
    which keeps protobuf-string comparisons readable in the service layer.
    """

    AVAILABLE = "AVAILABLE"
    BORROWED = "BORROWED"
    LOST = "LOST"


# Single shared SQLAlchemy enum type. ``create_type=False`` tells SQLAlchemy not
# to emit ``CREATE TYPE`` when issuing ``CREATE TABLE`` — the Alembic migration
# owns the type's lifecycle so the model definition and the migration agree on
# exactly one creation site. ``native_enum=True`` (the default for Postgres)
# means we get a real ``copy_status`` ENUM type, not a CHECK constraint.
_copy_status_type = SAEnum(
    CopyStatus,
    name="copy_status",
    native_enum=True,
    create_type=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Book(Base):
    """An abstract title (one row per ISBN/title-edition).

    Two physical copies of the same novel are represented by two
    :class:`BookCopy` rows pointing at one ``Book``. ``isbn`` is nullable to
    accommodate pre-ISBN, locally produced, or uncatalogued items.
    """

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    isbn: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    copies: Mapped[list["BookCopy"]] = relationship(
        back_populates="book",
        cascade="save-update, merge",
        passive_deletes=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"Book(id={self.id!r}, title={self.title!r}, author={self.author!r})"


class Member(Base):
    """A library patron.

    Email is the natural staff-facing identifier. Case-insensitive uniqueness
    is enforced by ``members_email_unique_idx`` on ``lower(email)`` (created
    in the migration), not by a column-level unique constraint.
    """

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    loans: Mapped[list["Loan"]] = relationship(
        back_populates="member",
        cascade="save-update, merge",
        passive_deletes=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"Member(id={self.id!r}, name={self.name!r}, email={self.email!r})"


class BookCopy(Base):
    """A physical copy of a :class:`Book` on the shelf.

    Status transitions (handled by the loan service in Phase 5):

    * ``AVAILABLE`` -> ``BORROWED`` when a loan is created.
    * ``BORROWED`` -> ``AVAILABLE`` when a loan is returned.
    * ``AVAILABLE`` or ``BORROWED`` -> ``LOST`` is admin-driven.
    """

    __tablename__ = "book_copies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("books.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[CopyStatus] = mapped_column(
        _copy_status_type,
        nullable=False,
        server_default=CopyStatus.AVAILABLE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    book: Mapped["Book"] = relationship(back_populates="copies")
    loans: Mapped[list["Loan"]] = relationship(
        back_populates="copy",
        cascade="save-update, merge",
        passive_deletes=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"BookCopy(id={self.id!r}, book_id={self.book_id!r}, status={self.status!r})"


class Loan(Base):
    """One borrow event linking a :class:`Member` to a :class:`BookCopy`.

    ``returned_at IS NULL`` is the canonical "active loan" predicate — the
    partial unique index ``loans_one_active_per_copy_idx`` (see the migration)
    enforces at most one active loan per copy at the database level.
    """

    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    copy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("book_copies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    borrowed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    copy: Mapped["BookCopy"] = relationship(back_populates="loans")
    member: Mapped["Member"] = relationship(back_populates="loans")

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return (
            "Loan("
            f"id={self.id!r}, copy_id={self.copy_id!r}, member_id={self.member_id!r}, "
            f"due_at={self.due_at!r}, returned_at={self.returned_at!r}"
            ")"
        )


__all__ = [
    "Base",
    "Book",
    "BookCopy",
    "CopyStatus",
    "Loan",
    "Member",
]
