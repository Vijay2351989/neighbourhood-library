"""Book + book-copy persistence.

This module owns every line of SQL touching the ``books`` and ``book_copies``
tables. Service code calls these functions; nothing here imports protobuf.

Repository contract (per :doc:`docs/design/03-backend.md` §3):

* Inputs and outputs are plain Python primitives or SQLAlchemy ORM objects.
* Domain errors are raised with the typed exceptions in :mod:`library.errors`.
* Caller (the service layer) owns the transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.db.models import Book, BookCopy, CopyStatus
from library.errors import FailedPrecondition, NotFound


class BookRow(NamedTuple):
    """A book plus its copy aggregates as returned to the service layer."""

    book: Book
    total_copies: int
    available_copies: int


@dataclass(slots=True)
class ListBooksResult:
    rows: list[BookRow]
    total_count: int


def _escape_like(text: str) -> str:
    """Escape ``LIKE`` metacharacters so user input is treated as a literal prefix."""

    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_predicate(search: str):
    """Build the case-insensitive prefix-match predicate over title/author."""

    pattern = _escape_like(search.lower()) + "%"
    return or_(
        func.lower(Book.title).like(pattern, escape="\\"),
        func.lower(Book.author).like(pattern, escape="\\"),
    )


async def create(
    session: AsyncSession,
    *,
    title: str,
    author: str,
    isbn: str | None,
    published_year: int | None,
    number_of_copies: int,
) -> BookRow:
    """Insert a book and N copies in one flush.

    The caller's transaction handles commit/rollback. ``number_of_copies`` is
    assumed already validated (>= 1) by the service layer.
    """

    book = Book(
        title=title,
        author=author,
        isbn=isbn,
        published_year=published_year,
    )
    session.add(book)
    await session.flush()  # populate book.id

    session.add_all(
        BookCopy(book_id=book.id, status=CopyStatus.AVAILABLE)
        for _ in range(number_of_copies)
    )
    await session.flush()

    # Freshly created — every copy starts AVAILABLE, so available == total.
    return BookRow(book=book, total_copies=number_of_copies, available_copies=number_of_copies)


async def get(session: AsyncSession, book_id: int) -> BookRow:
    """Fetch a single book with its copy aggregates.

    Raises:
        NotFound: when no book has the given id.
    """

    stmt = (
        select(
            Book,
            func.count(BookCopy.id).label("total_copies"),
            func.count(BookCopy.id)
            .filter(BookCopy.status == CopyStatus.AVAILABLE)
            .label("available_copies"),
        )
        .outerjoin(BookCopy, BookCopy.book_id == Book.id)
        .where(Book.id == book_id)
        .group_by(Book.id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise NotFound(f"book {book_id} not found")
    book, total_copies, available_copies = row
    return BookRow(book=book, total_copies=total_copies, available_copies=available_copies)


async def list_books(
    session: AsyncSession,
    *,
    search: str | None,
    limit: int,
    offset: int,
) -> ListBooksResult:
    """List books with copy aggregates plus a total-count for pagination."""

    # Count distinct books matching the filter — separate from the paged
    # query so total_count reflects the full filtered set, not the page.
    count_stmt = select(func.count()).select_from(Book)
    if search:
        count_stmt = count_stmt.where(_search_predicate(search))
    total_count = (await session.scalar(count_stmt)) or 0

    list_stmt = (
        select(
            Book,
            func.count(BookCopy.id).label("total_copies"),
            func.count(BookCopy.id)
            .filter(BookCopy.status == CopyStatus.AVAILABLE)
            .label("available_copies"),
        )
        .outerjoin(BookCopy, BookCopy.book_id == Book.id)
        .group_by(Book.id)
        # title is the user-visible sort key; id breaks ties deterministically
        # so pagination is stable across pages.
        .order_by(Book.title.asc(), Book.id.asc())
        .limit(limit)
        .offset(offset)
    )
    if search:
        list_stmt = list_stmt.where(_search_predicate(search))

    result = await session.execute(list_stmt)
    rows = [
        BookRow(book=book, total_copies=total_copies, available_copies=available_copies)
        for book, total_copies, available_copies in result.all()
    ]
    return ListBooksResult(rows=rows, total_count=total_count)


async def update_book(
    session: AsyncSession,
    book_id: int,
    *,
    title: str,
    author: str,
    isbn: str | None,
    published_year: int | None,
    number_of_copies: int | None,
) -> BookRow:
    """Update fields on a book; reconcile copy rows when ``number_of_copies`` is set.

    Reconciliation rule: only ``AVAILABLE`` rows are added or removed —
    ``BORROWED`` and ``LOST`` rows are untouched. Reducing the count is
    therefore bounded by how many ``AVAILABLE`` copies exist; if the request
    would require removing borrowed/lost rows, we raise
    :class:`FailedPrecondition`. Removal is ordered by ``id ASC`` so behavior
    is deterministic and tests can pin which copies disappear.

    Raises:
        NotFound: when ``book_id`` doesn't exist.
        FailedPrecondition: when the requested count would require removing
            non-available copies (i.e. dropping below currently-borrowed).
    """

    book = await session.get(Book, book_id)
    if book is None:
        raise NotFound(f"book {book_id} not found")

    book.title = title
    book.author = author
    book.isbn = isbn
    book.published_year = published_year
    # Application-managed updated_at (per design/01-database.md decision row 14
    # — no DB trigger). Python datetime so the value is observable on the
    # in-memory ORM instance immediately, without an async refresh.
    book.updated_at = datetime.now(timezone.utc)

    if number_of_copies is not None:
        await _reconcile_copies(session, book_id, target=number_of_copies)

    await session.flush()
    # Re-derive the aggregates from the (now-mutated) DB rather than
    # bookkeeping by hand — one extra query, simpler invariant.
    return await get(session, book_id)


async def _reconcile_copies(
    session: AsyncSession,
    book_id: int,
    *,
    target: int,
) -> None:
    """Add or remove ``AVAILABLE`` copies until total == target."""

    counts = await session.execute(
        select(
            func.count().label("total"),
            func.count()
            .filter(BookCopy.status == CopyStatus.AVAILABLE)
            .label("available"),
        ).where(BookCopy.book_id == book_id)
    )
    total, available = counts.one()
    delta = target - total

    if delta == 0:
        return

    if delta > 0:
        # Add new copies, all AVAILABLE.
        session.add_all(
            BookCopy(book_id=book_id, status=CopyStatus.AVAILABLE)
            for _ in range(delta)
        )
        return

    # Reduction path: remove |delta| AVAILABLE rows. We can only touch the
    # AVAILABLE pool — borrowed copies have outstanding loans, lost copies
    # are historical record. If the request would require touching either,
    # surface a FailedPrecondition with both numbers so the caller can show
    # an accurate error message in the UI.
    needed = -delta
    if needed > available:
        untouchable = total - available  # borrowed + lost
        raise FailedPrecondition(
            f"cannot reduce copies to {target}: {untouchable} copies are currently "
            f"borrowed or lost and cannot be removed"
        )

    # Pick the lowest-id AVAILABLE copies for removal so the behavior is
    # deterministic across runs (matters for tests; nice-to-have in prod).
    victims_stmt = (
        select(BookCopy.id)
        .where(BookCopy.book_id == book_id, BookCopy.status == CopyStatus.AVAILABLE)
        .order_by(BookCopy.id.asc())
        .limit(needed)
    )
    victim_ids = list((await session.scalars(victims_stmt)).all())
    await session.execute(delete(BookCopy).where(BookCopy.id.in_(victim_ids)))


__all__ = [
    "BookRow",
    "ListBooksResult",
    "create",
    "get",
    "list_books",
    "update_book",
]
