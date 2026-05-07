"""Book-domain service.

Sits between the gRPC servicer and the repository: validates incoming proto
requests, opens a transaction per RPC, converts repository results into
response messages. No SQL is written here; no proto is touched in the
repository layer below it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from library.errors import InvalidArgument
from library.generated.library.v1 import library_pb2
from library.repositories import books as books_repo
from library.services.conversions import (
    clamp_pagination,
    datetime_to_pb,
    normalize_search,
)


class BookService:
    """Handlers for the four book RPCs.

    The session factory is injected so tests can swap in their own
    sessionmaker without touching module-level state.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ---------- mutations ----------

    async def create_book(
        self, request: library_pb2.CreateBookRequest
    ) -> library_pb2.CreateBookResponse:
        title = request.title.strip()
        author = request.author.strip()
        if not title:
            raise InvalidArgument("title is required")
        if not author:
            raise InvalidArgument("author is required")
        if request.number_of_copies < 1:
            raise InvalidArgument("number_of_copies must be at least 1")

        isbn = request.isbn.value if request.HasField("isbn") else None
        published_year = (
            request.published_year.value if request.HasField("published_year") else None
        )

        async with self._session_factory.begin() as session:
            row = await books_repo.create(
                session,
                title=title,
                author=author,
                isbn=isbn,
                published_year=published_year,
                number_of_copies=request.number_of_copies,
            )
            book_proto = _book_row_to_proto(row)

        return library_pb2.CreateBookResponse(book=book_proto)

    async def update_book(
        self, request: library_pb2.UpdateBookRequest
    ) -> library_pb2.UpdateBookResponse:
        if request.id <= 0:
            raise InvalidArgument("id is required")
        title = request.title.strip()
        author = request.author.strip()
        if not title:
            raise InvalidArgument("title is required")
        if not author:
            raise InvalidArgument("author is required")

        isbn = request.isbn.value if request.HasField("isbn") else None
        published_year = (
            request.published_year.value if request.HasField("published_year") else None
        )
        number_of_copies: int | None = None
        if request.HasField("number_of_copies"):
            number_of_copies = request.number_of_copies.value
            # Update may go to zero — a librarian taking a title out of
            # circulation. Create still requires >=1 because creating a book
            # with no copies is meaningless input.
            if number_of_copies < 0:
                raise InvalidArgument("number_of_copies must be non-negative")

        async with self._session_factory.begin() as session:
            row = await books_repo.update_book(
                session,
                request.id,
                title=title,
                author=author,
                isbn=isbn,
                published_year=published_year,
                number_of_copies=number_of_copies,
            )
            book_proto = _book_row_to_proto(row)

        return library_pb2.UpdateBookResponse(book=book_proto)

    # ---------- reads ----------

    async def get_book(
        self, request: library_pb2.GetBookRequest
    ) -> library_pb2.GetBookResponse:
        if request.id <= 0:
            raise InvalidArgument("id is required")

        async with self._session_factory() as session:
            row = await books_repo.get(session, request.id)
            book_proto = _book_row_to_proto(row)

        return library_pb2.GetBookResponse(book=book_proto)

    async def list_books(
        self, request: library_pb2.ListBooksRequest
    ) -> library_pb2.ListBooksResponse:
        page_size, offset = clamp_pagination(
            page_size=request.page_size,
            offset=request.offset,
        )
        search = (
            normalize_search(request.search.value) if request.HasField("search") else None
        )

        async with self._session_factory() as session:
            result = await books_repo.list_books(
                session,
                search=search,
                limit=page_size,
                offset=offset,
            )

        return library_pb2.ListBooksResponse(
            books=[_book_row_to_proto(row) for row in result.rows],
            total_count=result.total_count,
        )


def _book_row_to_proto(row: books_repo.BookRow) -> library_pb2.Book:
    """Translate a repository BookRow into the wire proto Book message."""

    book = row.book
    proto = library_pb2.Book(
        id=book.id,
        title=book.title,
        author=book.author,
        total_copies=row.total_copies,
        available_copies=row.available_copies,
    )
    # Wrapper-typed fields are only set when the underlying value is non-None.
    # Assigning to .value materializes the wrapper; leaving it untouched keeps
    # HasField(...) False on the wire, which the frontend reads as "null".
    if book.isbn is not None:
        proto.isbn.value = book.isbn
    if book.published_year is not None:
        proto.published_year.value = book.published_year
    proto.created_at.CopyFrom(datetime_to_pb(book.created_at))
    proto.updated_at.CopyFrom(datetime_to_pb(book.updated_at))
    return proto


__all__ = ["BookService"]
