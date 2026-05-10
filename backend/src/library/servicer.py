"""gRPC servicers for the three library subdomains.

Each servicer is intentionally thin — every RPC delegates to the matching
service-layer object and lets :func:`library.errors.map_domain_errors`
translate any typed domain exception into the matching gRPC status. No SQL,
no validation, no business logic lives here.

The split mirrors the proto split (``book.proto`` / ``member.proto`` /
``loan.proto``): one Python servicer class per service definition, all
backed by the same async session factory so they share a single connection
pool. ``main.py`` registers all three on the same gRPC server.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from library.config import Settings, get_settings
from library.errors import map_domain_errors
from library.generated.library.v1 import (
    book_pb2,
    book_pb2_grpc,
    loan_pb2,
    loan_pb2_grpc,
    member_pb2,
    member_pb2_grpc,
)
from library.services.book_service import BookService
from library.services.loan_service import LoanService
from library.services.member_service import MemberService


class BookServicer(book_pb2_grpc.BookServiceServicer):
    """Implements the four RPCs on ``library.v1.BookService``."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._book_service = BookService(session_factory)

    @map_domain_errors
    async def CreateBook(self, request: book_pb2.CreateBookRequest, context):
        return await self._book_service.create_book(request)

    @map_domain_errors
    async def UpdateBook(self, request: book_pb2.UpdateBookRequest, context):
        return await self._book_service.update_book(request)

    @map_domain_errors
    async def GetBook(self, request: book_pb2.GetBookRequest, context):
        return await self._book_service.get_book(request)

    @map_domain_errors
    async def ListBooks(self, request: book_pb2.ListBooksRequest, context):
        return await self._book_service.list_books(request)


class MemberServicer(member_pb2_grpc.MemberServiceServicer):
    """Implements the four RPCs on ``library.v1.MemberService``."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings | None = None,
    ) -> None:
        # Settings are resolved lazily so the in-process server picks up
        # env-driven config; tests can pass an explicit Settings to override
        # the fine knobs that affect computed Member.outstanding_fines_cents.
        self._member_service = MemberService(session_factory, settings or get_settings())

    @map_domain_errors
    async def CreateMember(self, request: member_pb2.CreateMemberRequest, context):
        return await self._member_service.create_member(request)

    @map_domain_errors
    async def UpdateMember(self, request: member_pb2.UpdateMemberRequest, context):
        return await self._member_service.update_member(request)

    @map_domain_errors
    async def GetMember(self, request: member_pb2.GetMemberRequest, context):
        return await self._member_service.get_member(request)

    @map_domain_errors
    async def ListMembers(self, request: member_pb2.ListMembersRequest, context):
        return await self._member_service.list_members(request)


class LoanServicer(loan_pb2_grpc.LoanServiceServicer):
    """Implements the four RPCs on ``library.v1.LoanService``."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings | None = None,
    ) -> None:
        # Loan fines depend on FINE_GRACE_DAYS / FINE_PER_DAY_CENTS / FINE_CAP_CENTS;
        # tests inject a Settings to make those knobs deterministic.
        self._loan_service = LoanService(session_factory, settings or get_settings())

    @map_domain_errors
    async def BorrowBook(self, request: loan_pb2.BorrowBookRequest, context):
        return await self._loan_service.borrow_book(request)

    @map_domain_errors
    async def ReturnBook(self, request: loan_pb2.ReturnBookRequest, context):
        return await self._loan_service.return_book(request)

    @map_domain_errors
    async def ListLoans(self, request: loan_pb2.ListLoansRequest, context):
        return await self._loan_service.list_loans(request)

    @map_domain_errors
    async def GetMemberLoans(self, request: loan_pb2.GetMemberLoansRequest, context):
        return await self._loan_service.get_member_loans(request)


__all__ = ["BookServicer", "MemberServicer", "LoanServicer"]
