"""End-to-end coverage of the four book RPCs.

Tests run a real client against the in-process gRPC server defined in
``conftest.py``, which in turn talks to a real Postgres testcontainer with
the migrated schema. No mocks.
"""

from __future__ import annotations

import grpc
import pytest
from google.protobuf import wrappers_pb2
from sqlalchemy import text

from library.generated.library.v1 import library_pb2


# ---------- helpers ----------


def _create_book_request(
    *,
    title: str = "Dune",
    author: str = "Frank Herbert",
    isbn: str | None = "978-0441172719",
    published_year: int | None = 1965,
    number_of_copies: int = 2,
) -> library_pb2.CreateBookRequest:
    req = library_pb2.CreateBookRequest(
        title=title,
        author=author,
        number_of_copies=number_of_copies,
    )
    if isbn is not None:
        req.isbn.value = isbn
    if published_year is not None:
        req.published_year.value = published_year
    return req


# ---------- CreateBook ----------


async def test_create_book_happy(library_stub) -> None:
    response = await library_stub.CreateBook(_create_book_request())
    book = response.book
    assert book.id > 0
    assert book.title == "Dune"
    assert book.author == "Frank Herbert"
    assert book.HasField("isbn") and book.isbn.value == "978-0441172719"
    assert book.HasField("published_year") and book.published_year.value == 1965
    assert book.total_copies == 2
    assert book.available_copies == 2
    assert book.created_at.seconds > 0
    assert book.updated_at.seconds > 0


async def test_create_book_optional_fields_omitted(library_stub) -> None:
    response = await library_stub.CreateBook(
        _create_book_request(isbn=None, published_year=None, number_of_copies=1)
    )
    assert not response.book.HasField("isbn")
    assert not response.book.HasField("published_year")
    assert response.book.total_copies == 1


@pytest.mark.parametrize(
    ("title", "author", "copies", "expected_field"),
    [
        ("", "Author", 1, "title"),
        ("   ", "Author", 1, "title"),
        ("Title", "", 1, "author"),
        ("Title", "Author", 0, "number_of_copies"),
        ("Title", "Author", -1, "number_of_copies"),
    ],
)
async def test_create_book_invalid_argument(
    library_stub, title: str, author: str, copies: int, expected_field: str
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await library_stub.CreateBook(
            library_pb2.CreateBookRequest(title=title, author=author, number_of_copies=copies)
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert expected_field in exc_info.value.details()


# ---------- GetBook ----------


async def test_get_book_happy(library_stub) -> None:
    created = (await library_stub.CreateBook(_create_book_request())).book
    fetched = (await library_stub.GetBook(library_pb2.GetBookRequest(id=created.id))).book
    assert fetched.id == created.id
    assert fetched.title == created.title
    assert fetched.total_copies == 2
    assert fetched.available_copies == 2


async def test_get_book_not_found(library_stub) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await library_stub.GetBook(library_pb2.GetBookRequest(id=999_999))
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


async def test_get_book_invalid_id(library_stub) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await library_stub.GetBook(library_pb2.GetBookRequest(id=0))
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---------- ListBooks ----------


async def test_list_books_empty(library_stub) -> None:
    response = await library_stub.ListBooks(library_pb2.ListBooksRequest())
    assert response.total_count == 0
    assert list(response.books) == []


async def test_list_books_returns_aggregates_and_orders_by_title(library_stub) -> None:
    await library_stub.CreateBook(_create_book_request(title="Foundation", author="Asimov"))
    await library_stub.CreateBook(_create_book_request(title="Dune", author="Herbert", number_of_copies=3))
    await library_stub.CreateBook(_create_book_request(title="Anathem", author="Stephenson", number_of_copies=1))

    response = await library_stub.ListBooks(library_pb2.ListBooksRequest())
    assert response.total_count == 3
    titles = [b.title for b in response.books]
    assert titles == ["Anathem", "Dune", "Foundation"]
    by_title = {b.title: b for b in response.books}
    assert by_title["Dune"].total_copies == 3
    assert by_title["Dune"].available_copies == 3


async def test_list_books_search_prefix_case_insensitive(library_stub) -> None:
    await library_stub.CreateBook(_create_book_request(title="Dune", author="Frank Herbert"))
    await library_stub.CreateBook(_create_book_request(title="Foundation", author="Isaac Asimov"))
    await library_stub.CreateBook(_create_book_request(title="Anathem", author="Neal Stephenson"))

    req = library_pb2.ListBooksRequest()
    req.search.value = "DUN"
    response = await library_stub.ListBooks(req)
    assert response.total_count == 1
    assert response.books[0].title == "Dune"

    # Match by author prefix.
    req.search.value = "isaac"
    response = await library_stub.ListBooks(req)
    assert response.total_count == 1
    assert response.books[0].title == "Foundation"


async def test_list_books_pagination(library_stub) -> None:
    for i in range(5):
        await library_stub.CreateBook(
            _create_book_request(title=f"Book {i:02d}", author="A", number_of_copies=1)
        )

    page1 = await library_stub.ListBooks(library_pb2.ListBooksRequest(page_size=2, offset=0))
    assert page1.total_count == 5
    assert [b.title for b in page1.books] == ["Book 00", "Book 01"]

    page2 = await library_stub.ListBooks(library_pb2.ListBooksRequest(page_size=2, offset=2))
    assert [b.title for b in page2.books] == ["Book 02", "Book 03"]

    page3 = await library_stub.ListBooks(library_pb2.ListBooksRequest(page_size=2, offset=4))
    assert [b.title for b in page3.books] == ["Book 04"]


async def test_list_books_page_size_defaults_when_zero(library_stub) -> None:
    """page_size=0 (proto3 default) silently uses DEFAULT_PAGE_SIZE."""

    for i in range(3):
        await library_stub.CreateBook(_create_book_request(title=f"T{i}", number_of_copies=1))
    response = await library_stub.ListBooks(library_pb2.ListBooksRequest())
    assert response.total_count == 3
    assert len(response.books) == 3  # all fit under default 25


@pytest.mark.parametrize(
    ("page_size", "offset"),
    [(-1, 0), (10, -1)],
)
async def test_list_books_negative_args_invalid(
    library_stub, page_size: int, offset: int
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await library_stub.ListBooks(
            library_pb2.ListBooksRequest(page_size=page_size, offset=offset)
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---------- UpdateBook ----------


async def test_update_book_basic_fields(library_stub) -> None:
    created = (await library_stub.CreateBook(_create_book_request())).book
    request = library_pb2.UpdateBookRequest(
        id=created.id,
        title="Dune (Special Edition)",
        author="Frank Herbert",
    )
    request.isbn.value = "978-0593098233"
    request.published_year.value = 2019
    response = await library_stub.UpdateBook(request)
    assert response.book.title == "Dune (Special Edition)"
    assert response.book.isbn.value == "978-0593098233"
    assert response.book.published_year.value == 2019
    # Copy count untouched when number_of_copies wrapper is unset.
    assert response.book.total_copies == 2
    assert response.book.available_copies == 2


async def test_update_book_copy_count_up(library_stub) -> None:
    created = (await library_stub.CreateBook(_create_book_request(number_of_copies=2))).book
    request = library_pb2.UpdateBookRequest(
        id=created.id, title="Dune", author="Frank Herbert"
    )
    request.number_of_copies.value = 5
    response = await library_stub.UpdateBook(request)
    assert response.book.total_copies == 5
    assert response.book.available_copies == 5


async def test_update_book_copy_count_down(library_stub) -> None:
    created = (await library_stub.CreateBook(_create_book_request(number_of_copies=4))).book
    request = library_pb2.UpdateBookRequest(
        id=created.id, title="Dune", author="Frank Herbert"
    )
    request.number_of_copies.value = 2
    response = await library_stub.UpdateBook(request)
    assert response.book.total_copies == 2
    assert response.book.available_copies == 2


async def test_update_book_drop_below_borrowed_rejected(library_stub) -> None:
    """When borrowed copies would have to be removed, fail with FAILED_PRECONDITION."""

    created = (await library_stub.CreateBook(_create_book_request(number_of_copies=3))).book

    # Phase 5 will own the borrow flow; for Phase 4 we mutate copy status
    # directly so we can exercise the reconciliation safeguard. Mark two of
    # the three copies as BORROWED — only one is then AVAILABLE to remove.
    from library.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE book_copies SET status='BORROWED' "
                "WHERE id IN ("
                "  SELECT id FROM book_copies WHERE book_id=:book_id "
                "  ORDER BY id ASC LIMIT 2"
                ")"
            ),
            {"book_id": created.id},
        )
        await session.commit()

    request = library_pb2.UpdateBookRequest(
        id=created.id, title="Dune", author="Frank Herbert"
    )
    request.number_of_copies.value = 0  # would need to remove all 3, only 1 AVAILABLE
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await library_stub.UpdateBook(request)
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert "borrowed" in exc_info.value.details().lower()

    # And the rejection is observably non-destructive: the book and its copies
    # are still in place after the failed call.
    fetched = (await library_stub.GetBook(library_pb2.GetBookRequest(id=created.id))).book
    assert fetched.total_copies == 3
    assert fetched.available_copies == 1


async def test_update_book_not_found(library_stub) -> None:
    request = library_pb2.UpdateBookRequest(
        id=999_999, title="X", author="Y"
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await library_stub.UpdateBook(request)
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


async def test_update_book_clear_optional_fields(library_stub) -> None:
    """Omitting wrapper fields on UpdateBook clears the underlying values."""

    created = (
        await library_stub.CreateBook(_create_book_request(isbn="X", published_year=1900))
    ).book
    # Send an update with no isbn / published_year wrapper set -> clears them.
    response = await library_stub.UpdateBook(
        library_pb2.UpdateBookRequest(id=created.id, title="Dune", author="Herbert")
    )
    assert not response.book.HasField("isbn")
    assert not response.book.HasField("published_year")
