"""Alembic environment for the Neighborhood Library backend.

Async-aware: the application's database URL uses the ``postgresql+asyncpg``
driver, so we drive migrations through an :class:`AsyncEngine` and use
``connection.run_sync(context.run_migrations)`` to bridge alembic's
synchronous migration API into the asyncio world.

This is the canonical recipe documented at
https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic
— we follow it deliberately rather than swapping to a sync driver, so that
runtime and migrations share one URL/driver and one configuration source.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import the application's models so ``Base.metadata`` is populated with every
# table before alembic compares against the live database. The import order
# here (settings then models) keeps the import side-effects predictable.
from library.config import get_settings
from library.db.models import Base

# Alembic Config object exposes values from alembic.ini.
config = context.config

# Configure stdlib logging from the [loggers] / [handlers] sections of
# alembic.ini if they're present — keeps alembic's own log lines formatted
# consistently with the rest of the app.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the application's DATABASE_URL into the alembic config so that
# ``async_engine_from_config`` picks it up. We deliberately do NOT bake the
# URL into alembic.ini so there is exactly one source of truth (the env var
# read by the Pydantic settings class).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Target metadata for autogenerate support in future phases. Not used by the
# initial hand-authored migration but harmless to wire up now.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Offline mode emits SQL to stdout instead of executing it against a live
    connection. Useful for ``alembic upgrade head --sql`` style review. We
    keep the URL plumbing identical to online mode so what runs and what's
    emitted match.
    """

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """Synchronous migration runner; invoked from ``run_async_migrations`` via ``run_sync``."""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode against an :class:`AsyncEngine`.

    Builds the async engine from the section of alembic.ini that the
    ``[alembic]`` block points at (the default ``[alembic]`` section here),
    opens a connection, and bridges into alembic's synchronous migration
    runner via ``connection.run_sync``.
    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    # Dispose of the engine explicitly so the asyncpg connection pool is
    # closed and the asyncio loop has nothing pending when alembic exits.
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations — schedules the async runner on a fresh loop."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
