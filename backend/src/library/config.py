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


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`, building it lazily on first call.

    Lazy construction lets tests override env vars before the first read.
    """

    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
