"""Environment-driven settings for the library service.

All knobs are read from the process environment via Pydantic Settings, with
sensible defaults that match the values documented in
``docs/design/05-infrastructure.md``. The Phase 1 stub only consumes
``grpc_port``; the rest are declared now so later phases can use them without
re-touching this module.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration.

    Values come from environment variables (and a ``.env`` file if present),
    matching the names declared on the ``api`` service in ``docker-compose.yml``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Connection string is unused in Phase 1 (no DB yet) but declared so that
    # `docker compose up` with the env var set doesn't trip `extra="forbid"`
    # later, and so Phase 2 has a single source of truth.
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@postgres:5432/library",
        description="SQLAlchemy async URL for Postgres.",
    )

    grpc_port: int = Field(
        default=50051,
        ge=1,
        le=65535,
        description="TCP port the gRPC server binds to.",
    )

    default_loan_days: int = Field(
        default=14,
        ge=1,
        description="Loan length in days when a member borrows a book.",
    )

    fine_grace_days: int = Field(
        default=14,
        ge=0,
        description="Days past due before fines start accruing.",
    )

    fine_per_day_cents: int = Field(
        default=25,
        ge=0,
        description="Cents charged per overdue day after the grace period.",
    )

    fine_cap_cents: int = Field(
        default=2000,
        ge=0,
        description="Maximum fine that can accrue on a single loan, in cents.",
    )

    # ---------- Phase 5.6 resilience knobs ----------
    #
    # All sized for single-replica local dev. When N > 1 the pool numbers must
    # be divided by replica count so total = N * (pool_size + max_overflow)
    # stays under Postgres `max_connections`.

    db_statement_timeout_ms: int = Field(
        default=5000,
        ge=0,
        description=(
            "Postgres `statement_timeout`. Bounds total wall-clock per "
            "statement; PG actually stops the work and releases locks when "
            "exceeded. 0 disables — only useful for explicit long-running "
            "admin operations."
        ),
    )

    db_lock_timeout_ms: int = Field(
        default=3000,
        ge=0,
        description=(
            "Postgres `lock_timeout`. Bounds non-deadlock lock waits. Set "
            "lower than statement_timeout so a lock wait surfaces as the "
            "clearer `lock_not_available` rather than `statement_timeout`."
        ),
    )

    db_idle_tx_timeout_ms: int = Field(
        default=15000,
        ge=0,
        description=(
            "Postgres `idle_in_transaction_session_timeout`. Kills a "
            "forgotten BEGIN. Higher than the longest expected handler so "
            "it doesn't fire during normal slow paths."
        ),
    )

    db_pool_size: int = Field(
        default=10,
        ge=1,
        description="SQLAlchemy warm-pool size per worker.",
    )

    db_max_overflow: int = Field(
        default=10,
        ge=0,
        description="SQLAlchemy burst overflow above pool_size.",
    )

    db_pool_timeout_s: float = Field(
        default=5.0,
        ge=0.0,
        description=(
            "Seconds to wait for a free connection before raising "
            "`TimeoutError`. Fast-fail under saturation rather than the "
            "30-second SQLAlchemy default."
        ),
    )

    db_pool_recycle_s: int = Field(
        default=1800,
        ge=0,
        description=(
            "Recycle (close and re-open) connections older than this many "
            "seconds. Defends against firewall idle-kills."
        ),
    )

    db_command_timeout_s: float = Field(
        default=5.0,
        ge=0.0,
        description=(
            "asyncpg driver-side command timeout. Bounds how long Python "
            "waits for a single statement; the server-side "
            "statement_timeout is what actually frees DB resources, so "
            "keep this >= statement_timeout."
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`, building it lazily on first call.

    Lazy construction lets tests override env vars before the first read.
    """

    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
