"""Shared pytest fixtures for the integration suite.

What the suite needs to run a single test:

* a real Postgres (testcontainer) with the schema migrated by Alembic,
* an in-process asyncio gRPC server with all three business servicers
  registered (``BookServicer`` / ``MemberServicer`` / ``LoanServicer``),
* one gRPC stub per service pointed at that server,
* a clean DB state at the start of each test.

The first three are session-scoped — spinning a fresh container per test
would dominate runtime — and the last is enforced by an autouse
function-scoped fixture that ``TRUNCATE``s the tables and resets PK sequences
between tests.

Singleton resets
----------------
``library.config`` and ``library.db.engine`` cache settings and engine
instances at module level (lazy construction by design — see Phase 1/2). For
tests we override ``DATABASE_URL`` to point at the testcontainer and reset
those caches so the next access rebuilds against the test URL. We poke the
private attributes directly rather than adding test-only public helpers to
the production modules.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import grpc
import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer

# Backend repo root (one level up from this file's parent /tests/).
BACKEND_ROOT: Path = Path(__file__).resolve().parent.parent


def _async_db_url(sync_url: str) -> str:
    """Convert testcontainers' default sync URL into an asyncpg URL."""

    # testcontainers returns postgresql+psycopg2://... by default; we want the
    # asyncpg dialect for the application engine. Strip whatever sync driver
    # is in there and pin asyncpg explicitly.
    if "+" in sync_url.split("://", 1)[0]:
        scheme, rest = sync_url.split("://", 1)
        return f"postgresql+asyncpg://{rest}"
    return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _sync_db_url(async_url: str) -> str:
    """Strip the +asyncpg dialect for use with sync tools (Alembic CLI helpers)."""

    return async_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Spin up a Postgres 16 container for the entire test session."""

    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def database_urls(postgres_container: PostgresContainer) -> dict[str, str]:
    """Compute the async + sync URLs against the running container.

    Both are needed: the application uses asyncpg for runtime queries, but
    the Alembic CLI helpers need a sync driver because Alembic's high-level
    ``command.upgrade`` is synchronous (our async ``env.py`` wraps a sync
    callback inside ``connection.run_sync(...)``, but only for online use).
    """

    sync_url = postgres_container.get_connection_url()
    async_url = _async_db_url(sync_url)
    return {"async": async_url, "sync": _sync_db_url(async_url)}


@pytest.fixture(scope="session", autouse=True)
def _configure_environment(database_urls: dict[str, str]) -> Iterator[None]:
    """Set DATABASE_URL for the test session and reset cached singletons.

    Autouse so every test fixture below sees the configured environment
    without having to depend on this fixture explicitly.
    """

    os.environ["DATABASE_URL"] = database_urls["async"]

    # Import inside the fixture so the env var is in place before any module
    # caches kick in.
    import library.config
    import library.db.engine

    # Drop any settings/engine that may have been built by accidental imports
    # (e.g. test discovery touching the SUT before this fixture ran).
    library.config._settings = None
    library.db.engine._engine = None
    library.db.engine._sessionmaker = None

    yield

    # Best-effort teardown: dispose the engine so the underlying connection
    # pool releases sockets before the container stops.
    if library.db.engine._engine is not None:
        try:
            asyncio.get_event_loop().run_until_complete(library.db.engine._engine.dispose())
        except Exception:  # pragma: no cover - teardown only
            pass


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema(_configure_environment: None, database_urls: dict[str, str]) -> None:
    """Run ``alembic upgrade head`` against the testcontainer once per session."""

    cfg = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    # The application's alembic env.py reads DATABASE_URL from settings; since
    # _configure_environment already set the env var and reset the singleton,
    # alembic will resolve to the testcontainer URL.
    alembic_command.upgrade(cfg, "head")


# ---- async server + client fixtures ----


@pytest.fixture(scope="session", autouse=True)
def _telemetry_for_tests(_configure_environment: None) -> None:
    """Set up an OTel TracerProvider for the test session.

    We set the trace + log exporters to ``none`` so init doesn't spew JSON
    to stderr during every test run; the observability tests install an
    :class:`InMemorySpanExporter` on top to capture spans for assertions.
    Without a real provider in place, the gRPC auto-instrumentation creates
    no-op spans and the manual spans in the SUT have nowhere to land.
    """

    os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
    os.environ.setdefault("OTEL_LOGS_EXPORTER", "none")
    os.environ.setdefault("OTEL_SERVICE_NAME", "library-api-test")

    from library.observability.setup import init_telemetry

    init_telemetry()


@pytest_asyncio.fixture(scope="session")
async def grpc_server(
    _migrated_schema: None, _telemetry_for_tests: None
) -> AsyncIterator[tuple[grpc.aio.Server, int]]:
    """Start an in-process asyncio gRPC server bound to a random localhost port.

    Registers the same interceptor stack as production and all three
    business servicers — Book, Member, Loan — so trace and request context
    propagate through tests identically to a live deployment.
    """

    from grpc import aio

    from library.db.engine import AsyncSessionLocal
    from library.generated.library.v1 import (
        book_pb2_grpc,
        loan_pb2_grpc,
        member_pb2_grpc,
    )
    from library.observability.interceptors import RequestContextInterceptor
    from library.observability.setup import grpc_otel_server_interceptor
    from library.servicer import BookServicer, LoanServicer, MemberServicer

    server = aio.server(
        interceptors=[
            grpc_otel_server_interceptor(),
            RequestContextInterceptor(),
        ]
    )
    book_pb2_grpc.add_BookServiceServicer_to_server(
        BookServicer(AsyncSessionLocal), server
    )
    member_pb2_grpc.add_MemberServiceServicer_to_server(
        MemberServicer(AsyncSessionLocal), server
    )
    loan_pb2_grpc.add_LoanServiceServicer_to_server(
        LoanServicer(AsyncSessionLocal), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield server, port
    finally:
        await server.stop(grace=1.0)


@pytest_asyncio.fixture(scope="session")
async def library_channel(
    grpc_server: tuple[grpc.aio.Server, int],
) -> AsyncIterator[grpc.aio.Channel]:
    """One channel shared by all three stubs — gRPC multiplexes services
    over a single HTTP/2 connection so there's no benefit to splitting it."""

    _, port = grpc_server
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield channel
    finally:
        await channel.close()


@pytest_asyncio.fixture(scope="session")
async def book_stub(library_channel: grpc.aio.Channel):
    """Stub for ``library.v1.BookService`` — book CRUD."""

    from library.generated.library.v1 import book_pb2_grpc

    return book_pb2_grpc.BookServiceStub(library_channel)


@pytest_asyncio.fixture(scope="session")
async def member_stub(library_channel: grpc.aio.Channel):
    """Stub for ``library.v1.MemberService`` — member CRUD."""

    from library.generated.library.v1 import member_pb2_grpc

    return member_pb2_grpc.MemberServiceStub(library_channel)


@pytest_asyncio.fixture(scope="session")
async def loan_stub(library_channel: grpc.aio.Channel):
    """Stub for ``library.v1.LoanService`` — borrow / return / list loans."""

    from library.generated.library.v1 import loan_pb2_grpc

    return loan_pb2_grpc.LoanServiceStub(library_channel)


# ---- per-test cleanup ----


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(_migrated_schema: None) -> AsyncIterator[None]:
    """Reset all tables before each test; runs in the session event loop."""

    from library.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # CASCADE handles FK ordering; RESTART IDENTITY resets BIGSERIAL so
        # tests can rely on stable id ordering when comparing inserted rows.
        await session.execute(
            text(
                "TRUNCATE TABLE loans, book_copies, members, books "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield
