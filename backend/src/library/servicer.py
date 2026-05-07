"""gRPC servicer that fronts the book / member / loan services.

The servicer is intentionally thin: each method delegates to a service-layer
handler and lets :func:`library.errors.map_domain_errors` translate any
typed domain exception into the matching gRPC status. No SQL, no validation,
no business logic lives here.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from library.config import Settings, get_settings
from library.errors import map_domain_errors
from library.generated.library.v1 import library_pb2, library_pb2_grpc
from library.services.book_service import BookService
from library.services.loan_service import LoanService
from library.services.member_service import MemberService


class LibraryServicer(library_pb2_grpc.LibraryServiceServicer):
    """Implements all twelve RPCs on ``library.v1.LibraryService``."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings | None = None,
    ) -> None:
        # Resolve settings lazily on construction so the in-process server
        # picks up env-driven config; tests can pass an explicit Settings to
        # override the fine knobs.
        settings = settings or get_settings()
        self._book_service = BookService(session_factory)
        self._member_service = MemberService(session_factory, settings)
        self._loan_service = LoanService(session_factory, settings)

    # ---------- books ----------

    @map_domain_errors
    async def CreateBook(self, request: library_pb2.CreateBookRequest, context):
        return await self._book_service.create_book(request)

    @map_domain_errors
    async def UpdateBook(self, request: library_pb2.UpdateBookRequest, context):
        return await self._book_service.update_book(request)

    @map_domain_errors
    async def GetBook(self, request: library_pb2.GetBookRequest, context):
        return await self._book_service.get_book(request)

    @map_domain_errors
    async def ListBooks(self, request: library_pb2.ListBooksRequest, context):
        return await self._book_service.list_books(request)

    # ---------- members ----------

    @map_domain_errors
    async def CreateMember(self, request: library_pb2.CreateMemberRequest, context):
        return await self._member_service.create_member(request)

    @map_domain_errors
    async def UpdateMember(self, request: library_pb2.UpdateMemberRequest, context):
        return await self._member_service.update_member(request)

    @map_domain_errors
    async def GetMember(self, request: library_pb2.GetMemberRequest, context):
        return await self._member_service.get_member(request)

    @map_domain_errors
    async def ListMembers(self, request: library_pb2.ListMembersRequest, context):
        return await self._member_service.list_members(request)

    # ---------- loans ----------

    @map_domain_errors
    async def BorrowBook(self, request: library_pb2.BorrowBookRequest, context):
        return await self._loan_service.borrow_book(request)

    @map_domain_errors
    async def ReturnBook(self, request: library_pb2.ReturnBookRequest, context):
        return await self._loan_service.return_book(request)

    @map_domain_errors
    async def ListLoans(self, request: library_pb2.ListLoansRequest, context):
        return await self._loan_service.list_loans(request)

    @map_domain_errors
    async def GetMemberLoans(self, request: library_pb2.GetMemberLoansRequest, context):
        return await self._loan_service.get_member_loans(request)


__all__ = ["LibraryServicer"]
