"""Unit tests for :class:`library.services.book_service.BookService`.

These instantiate the service directly with a fake session factory and
monkeypatch the repository functions, so every assertion runs in
milliseconds against pure-Python logic. Integration tests still cover
the SQL behavior; these cover the validation, conversion, and
input-routing decisions the service itself makes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from library.db.models import Book
from library.errors import FailedPrecondition, InvalidArgument, NotFound
from library.generated.library.v1 import book_pb2
from library.repositories.books import BookRow, ListBooksResult
from library.services.book_service import BookService

_FIXED_TS = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


def _make_book(
    *,
    id: int = 1,
    title: str = "Mistborn",
    author: str = "Sanderson",
    isbn: str | None = None,
    published_year: int | None = None,
) -> Book:
    """Build a detached Book ORM instance for use as a stub return value."""

    book = Book(
        id=id,
        title=title,
        author=author,
        isbn=isbn,
        published_year=published_year,
    )
    book.created_at = _FIXED_TS
    book.updated_at = _FIXED_TS
    return book


# ---------- create_book ----------


async def test_create_book_strips_input_and_returns_proto(
    monkeypatch, fake_session_factory
) -> None:
    repo_create = AsyncMock(
        return_value=BookRow(
            book=_make_book(id=42, title="Mistborn", author="Sanderson"),
            total_copies=3,
            available_copies=3,
        )
    )
    monkeypatch.setattr("library.repositories.books.create", repo_create)

    service = BookService(fake_session_factory)
    response = await service.create_book(
        book_pb2.CreateBookRequest(
            title="  Mistborn  ",
            author="  Sanderson  ",
            number_of_copies=3,
        )
    )

    repo_create.assert_awaited_once()
    kwargs = repo_create.call_args.kwargs
    assert kwargs["title"] == "Mistborn"        # service stripped both
    assert kwargs["author"] == "Sanderson"
    assert kwargs["isbn"] is None               # optional field absent -> None
    assert kwargs["published_year"] is None
    assert kwargs["number_of_copies"] == 3
    assert response.book.id == 42
    assert response.book.total_copies == 3
    assert response.book.available_copies == 3


async def test_create_book_forwards_optional_fields_when_present(
    monkeypatch, fake_session_factory
) -> None:
    repo_create = AsyncMock(
        return_value=BookRow(
            book=_make_book(isbn="9780765316790", published_year=2006),
            total_copies=1,
            available_copies=1,
        )
    )
    monkeypatch.setattr("library.repositories.books.create", repo_create)

    request = book_pb2.CreateBookRequest(
        title="Mistborn", author="Sanderson", number_of_copies=1
    )
    request.isbn.value = "9780765316790"
    request.published_year.value = 2006

    service = BookService(fake_session_factory)
    await service.create_book(request)

    kwargs = repo_create.call_args.kwargs
    assert kwargs["isbn"] == "9780765316790"
    assert kwargs["published_year"] == 2006


@pytest.mark.parametrize(
    "title,author,copies,expected_field",
    [
        ("", "Author", 1, "title"),
        ("   ", "Author", 1, "title"),
        ("Title", "", 1, "author"),
        ("Title", "   ", 1, "author"),
        ("Title", "Author", 0, "number_of_copies"),
        ("Title", "Author", -1, "number_of_copies"),
    ],
)
async def test_create_book_rejects_invalid_input_without_calling_repo(
    monkeypatch,
    fake_session_factory,
    title: str,
    author: str,
    copies: int,
    expected_field: str,
) -> None:
    repo_create = AsyncMock()
    monkeypatch.setattr("library.repositories.books.create", repo_create)

    service = BookService(fake_session_factory)
    with pytest.raises(InvalidArgument) as exc_info:
        await service.create_book(
            book_pb2.CreateBookRequest(
                title=title, author=author, number_of_copies=copies
            )
        )

    assert expected_field in str(exc_info.value)
    repo_create.assert_not_awaited()  # validation blocked before SQL


# ---------- update_book ----------


async def test_update_book_passes_optional_copies_through_when_set(
    monkeypatch, fake_session_factory
) -> None:
    """When the client supplies number_of_copies, the service unwraps and forwards it."""

    repo_update = AsyncMock(
        return_value=BookRow(
            book=_make_book(id=7, title="X", author="Y"),
            total_copies=5,
            available_copies=5,
        )
    )
    monkeypatch.setattr("library.repositories.books.update_book", repo_update)

    request = book_pb2.UpdateBookRequest(id=7, title="X", author="Y")
    request.number_of_copies.value = 5

    service = BookService(fake_session_factory)
    await service.update_book(request)

    repo_update.assert_awaited_once()
    assert repo_update.call_args.kwargs["number_of_copies"] == 5


async def test_update_book_passes_none_when_copies_omitted(
    monkeypatch, fake_session_factory
) -> None:
    """Wrapper absent -> repo sees None (meaning "leave copy count alone")."""

    repo_update = AsyncMock(
        return_value=BookRow(
            book=_make_book(id=7),
            total_copies=2,
            available_copies=2,
        )
    )
    monkeypatch.setattr("library.repositories.books.update_book", repo_update)

    service = BookService(fake_session_factory)
    await service.update_book(
        book_pb2.UpdateBookRequest(id=7, title="X", author="Y")
    )

    assert repo_update.call_args.kwargs["number_of_copies"] is None


async def test_update_book_accepts_zero_copies(
    monkeypatch, fake_session_factory
) -> None:
    """Taking a title out of circulation: 0 is valid on update, unlike create."""

    repo_update = AsyncMock(
        return_value=BookRow(
            book=_make_book(id=7),
            total_copies=0,
            available_copies=0,
        )
    )
    monkeypatch.setattr("library.repositories.books.update_book", repo_update)

    request = book_pb2.UpdateBookRequest(id=7, title="X", author="Y")
    request.number_of_copies.value = 0

    service = BookService(fake_session_factory)
    await service.update_book(request)

    assert repo_update.call_args.kwargs["number_of_copies"] == 0


async def test_update_book_rejects_negative_copies(
    monkeypatch, fake_session_factory
) -> None:
    repo_update = AsyncMock()
    monkeypatch.setattr("library.repositories.books.update_book", repo_update)

    request = book_pb2.UpdateBookRequest(id=7, title="X", author="Y")
    request.number_of_copies.value = -1

    service = BookService(fake_session_factory)
    with pytest.raises(InvalidArgument):
        await service.update_book(request)
    repo_update.assert_not_awaited()


async def test_update_book_rejects_id_zero(
    monkeypatch, fake_session_factory
) -> None:
    repo_update = AsyncMock()
    monkeypatch.setattr("library.repositories.books.update_book", repo_update)

    service = BookService(fake_session_factory)
    with pytest.raises(InvalidArgument):
        await service.update_book(
            book_pb2.UpdateBookRequest(id=0, title="X", author="Y")
        )
    repo_update.assert_not_awaited()


async def test_update_book_propagates_failed_precondition_from_repo(
    monkeypatch, fake_session_factory
) -> None:
    """Reconciliation rejection bubbles up so the servicer can map it to FAILED_PRECONDITION."""

    repo_update = AsyncMock(side_effect=FailedPrecondition("cannot drop below borrowed"))
    monkeypatch.setattr("library.repositories.books.update_book", repo_update)

    request = book_pb2.UpdateBookRequest(id=7, title="X", author="Y")
    request.number_of_copies.value = 0

    service = BookService(fake_session_factory)
    with pytest.raises(FailedPrecondition):
        await service.update_book(request)


# ---------- get_book ----------


async def test_get_book_happy_path(monkeypatch, fake_session_factory) -> None:
    repo_get = AsyncMock(
        return_value=BookRow(
            book=_make_book(id=10, title="Elantris", author="Sanderson"),
            total_copies=2,
            available_copies=1,
        )
    )
    monkeypatch.setattr("library.repositories.books.get", repo_get)

    service = BookService(fake_session_factory)
    response = await service.get_book(book_pb2.GetBookRequest(id=10))

    repo_get.assert_awaited_once()
    assert response.book.id == 10
    assert response.book.title == "Elantris"
    assert response.book.available_copies == 1


async def test_get_book_rejects_id_zero(monkeypatch, fake_session_factory) -> None:
    repo_get = AsyncMock()
    monkeypatch.setattr("library.repositories.books.get", repo_get)

    service = BookService(fake_session_factory)
    with pytest.raises(InvalidArgument):
        await service.get_book(book_pb2.GetBookRequest(id=0))
    repo_get.assert_not_awaited()


async def test_get_book_propagates_not_found(
    monkeypatch, fake_session_factory
) -> None:
    repo_get = AsyncMock(side_effect=NotFound("missing"))
    monkeypatch.setattr("library.repositories.books.get", repo_get)

    service = BookService(fake_session_factory)
    with pytest.raises(NotFound):
        await service.get_book(book_pb2.GetBookRequest(id=999))


# ---------- list_books ----------


async def test_list_books_forwards_pagination_and_search(
    monkeypatch, fake_session_factory
) -> None:
    repo_list = AsyncMock(return_value=ListBooksResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.books.list_books", repo_list)

    request = book_pb2.ListBooksRequest(page_size=15, offset=30)
    request.search.value = "  mist  "

    service = BookService(fake_session_factory)
    await service.list_books(request)

    kwargs = repo_list.call_args.kwargs
    assert kwargs["limit"] == 15
    assert kwargs["offset"] == 30
    assert kwargs["search"] == "mist"  # stripped


async def test_list_books_applies_default_page_size_when_zero(
    monkeypatch, fake_session_factory
) -> None:
    repo_list = AsyncMock(return_value=ListBooksResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.books.list_books", repo_list)

    service = BookService(fake_session_factory)
    await service.list_books(book_pb2.ListBooksRequest(page_size=0, offset=0))

    # DEFAULT_PAGE_SIZE = 25 in conversions.py
    assert repo_list.call_args.kwargs["limit"] == 25


async def test_list_books_clamps_oversized_page_size(
    monkeypatch, fake_session_factory
) -> None:
    repo_list = AsyncMock(return_value=ListBooksResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.books.list_books", repo_list)

    service = BookService(fake_session_factory)
    await service.list_books(book_pb2.ListBooksRequest(page_size=10_000, offset=0))

    # MAX_PAGE_SIZE = 100
    assert repo_list.call_args.kwargs["limit"] == 100


async def test_list_books_treats_empty_search_as_none(
    monkeypatch, fake_session_factory
) -> None:
    repo_list = AsyncMock(return_value=ListBooksResult(rows=[], total_count=0))
    monkeypatch.setattr("library.repositories.books.list_books", repo_list)

    request = book_pb2.ListBooksRequest()
    request.search.value = "   "    # whitespace-only

    service = BookService(fake_session_factory)
    await service.list_books(request)

    assert repo_list.call_args.kwargs["search"] is None


async def test_list_books_rejects_negative_pagination(
    monkeypatch, fake_session_factory
) -> None:
    repo_list = AsyncMock()
    monkeypatch.setattr("library.repositories.books.list_books", repo_list)

    service = BookService(fake_session_factory)
    with pytest.raises(InvalidArgument):
        await service.list_books(book_pb2.ListBooksRequest(offset=-1))
    repo_list.assert_not_awaited()
