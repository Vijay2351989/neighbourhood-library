"""Async SQLAlchemy engine + session factory for the library service.

Phase 2 deliverable: provide a single, lazily constructed
:class:`~sqlalchemy.ext.asyncio.AsyncEngine` and a matching
:class:`~sqlalchemy.ext.asyncio.async_sessionmaker` that the rest of the
application (repositories in Phase 4) will use.

Design notes
------------
* The engine is **not** opened eagerly at import time. ``get_engine()`` builds
  it on first call and caches it, mirroring the lazy ``get_settings()``
  pattern in :mod:`library.config`. This keeps imports cheap (e.g. for tests
  that override ``DATABASE_URL`` before the first read) and lets the asyncio
  event loop be in charge of when sockets get opened.
* No migration code lives here. Migrations are an Alembic concern, run by
  ``entrypoint.sh`` before the server starts. Importing this module must not
  cause any ``CREATE TABLE`` to happen.
* The session factory is exposed both as a module-level
  ``AsyncSessionLocal`` (cheap to import; bound to the lazy engine via the
  ``async_sessionmaker`` constructor's deferred ``bind`` resolution) and via
  ``get_session()`` — an async generator suitable for dependency-injection
  style use in service handlers: ``async for session in get_session(): ...``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library.config import get_settings

logger = logging.getLogger("library.db.engine")


_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide :class:`AsyncEngine`, building it on first call.

    The engine uses the SQLAlchemy default ``QueuePool`` async equivalent
    (``AsyncAdaptedQueuePool``); pool sizing knobs are left at their defaults
    for now and can be tuned in a later phase if load testing reveals a need.

    Raises:
        sqlalchemy.exc.ArgumentError: if ``settings.database_url`` is not a
            valid SQLAlchemy URL.
    """

    global _engine
    if _engine is None:
        url = get_settings().database_url
        logger.info("library.db: creating async engine for %s", _redact_password(url))
        # ``future=True`` is the 2.0-style API; explicit for clarity even though
        # it is the default in SQLAlchemy 2.x.
        _engine = create_async_engine(url, future=True, pool_pre_ping=True)
    return _engine


# A module-level sessionmaker bound to a lambda that resolves the engine at
# session-creation time. ``async_sessionmaker`` accepts an ``AsyncEngine``
# directly, but we want lazy engine construction — so the first session built
# here triggers ``get_engine()``.
#
# Note: ``async_sessionmaker`` does not natively support a "bind callable",
# so we wrap the construction in a thin factory function below
# (``AsyncSessionLocal``) that materializes the sessionmaker on first use.
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the cached :class:`async_sessionmaker`, building it on first call."""

    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


class _LazyAsyncSessionLocal:
    """Callable proxy that defers sessionmaker construction until first call.

    Allows ``AsyncSessionLocal()`` to act exactly like an
    ``async_sessionmaker`` instance from the caller's point of view, while
    still permitting tests to swap in a different engine before any session
    is ever opened. Supports both direct call (``AsyncSessionLocal()``) and
    ``async with AsyncSessionLocal.begin() as session:`` style usage.
    """

    def __call__(self) -> AsyncSession:
        return _get_sessionmaker()()

    def begin(self):  # type: ignore[no-untyped-def]
        """Proxy to the underlying sessionmaker's :meth:`begin` for ``async with`` use."""

        return _get_sessionmaker().begin()


AsyncSessionLocal = _LazyAsyncSessionLocal()
"""Process-wide async session factory.

Usage::

    async with AsyncSessionLocal() as session:
        ...

or, for an auto-committing transactional context::

    async with AsyncSessionLocal.begin() as session:
        ...

In service-layer code prefer :func:`get_session`, which already wraps the
commit/rollback/close lifecycle correctly.
"""


async def get_session() -> AsyncIterator[AsyncSession]:
    """Async generator that yields a single managed :class:`AsyncSession`.

    Lifecycle:

    1. Open a new session from the shared sessionmaker.
    2. Yield it to the caller for use inside the request / unit of work.
    3. On clean exit, ``commit()`` any pending transaction.
    4. On exception, ``rollback()`` and re-raise so the caller sees the error.
    5. Always ``close()`` the session, returning its connection to the pool.

    This shape is friendly to dependency-injection patterns we'll use in the
    Phase 4 servicer layer; it's also usable directly via ``async for`` in
    one-off scripts::

        async for session in get_session():
            ...
    """

    session = _get_sessionmaker()()
    try:
        yield session
    except Exception:
        # Roll back uncommitted work on any error, then re-raise so the caller
        # observes the original exception rather than a swallowed one.
        await session.rollback()
        raise
    else:
        # No exception escaped the caller — commit any pending changes.
        # If the caller already committed/rolled back themselves, this is a
        # no-op on a clean session.
        if session.in_transaction():
            await session.commit()
    finally:
        await session.close()


def _redact_password(url: str) -> str:
    """Best-effort password redaction for log output.

    SQLAlchemy URLs look like ``scheme://user:password@host:port/db``. We mask
    the password segment if present, leaving everything else intact for
    debugging. Falls back to the original URL on any parse surprise — better
    to log a verbose URL than to crash a startup log line.
    """

    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        creds, host = rest.split("@", 1)
        if ":" not in creds:
            return url
        user, _password = creds.split(":", 1)
        return f"{scheme}://{user}:***@{host}"
    except Exception:  # pragma: no cover - logging defensive
        return url


__all__ = [
    "AsyncSessionLocal",
    "get_engine",
    "get_session",
]
