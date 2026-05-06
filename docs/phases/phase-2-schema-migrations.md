# Phase 2 — Schema & Migrations

**Status:** Approved, not yet started
**Last Updated:** 2026-05-05
**Effort:** S (~2 hrs)
**Prerequisites:** [Phase 1](phase-1-scaffolding.md)
**Blocks:** [Phase 4](phase-4-backend-crud.md), [Phase 5](phase-5-borrow-return-fines.md)

---

## Goal

The database schema from [design/01-database.md](../design/01-database.md) exists and is reproducible from a single command (`alembic upgrade head`).

---

## Related design docs

- [design/01-database.md](../design/01-database.md) — full DDL, indexes, partial unique index strategy

---

## Scope

### In
- Alembic init under `backend/alembic/`.
- Single migration `0001_initial.py` creating all four tables, the `copy_status` enum, all indexes, and the partial unique index.
- SQLAlchemy 2.0 typed models in `db/models.py` matching the schema exactly.
- Container entrypoint runs `alembic upgrade head` before launching the server.

### Out
- Any service implementation that uses the schema (Phases 4 and 5).
- Any data — pure schema.

---

## Deliverables

- `backend/alembic/versions/0001_initial.py`.
- `backend/src/library/db/models.py`.
- `backend/src/library/db/engine.py` (async engine, session factory).
- Updated `api` Dockerfile entrypoint script that runs migrations before launching.

---

## Acceptance criteria

- After `docker compose up`, running `psql -h localhost -U postgres library -c '\dt'` shows `books`, `members`, `book_copies`, `loans`, `alembic_version`.
- `\d loans` shows the partial unique index on `(copy_id) WHERE returned_at IS NULL`.
- `\d book_copies` shows the `copy_status` enum and the `(book_id, status)` composite index.
- Stopping compose, deleting the `pgdata` volume, restarting → schema is recreated identically.
- Running `alembic upgrade head` a second time is a no-op (no errors, no duplicate creates).
- `from library.db.models import Book, Member, BookCopy, Loan` works inside the container.

---

## Notes & risks

- **Enum migration.** Postgres enums require an explicit `CREATE TYPE` step in the migration. Use `op.execute()` for the enum and reference it via `postgresql.ENUM(name=..., create_type=False)` in the column definition.
- **Partial unique index.** Alembic's `op.create_index(..., postgresql_where=...)` is the right tool here. Verify it's emitted correctly.
- **`updated_at`.** We could automate it via a trigger or update it in the application code. **Decision: application-side updates** — simpler, no migration complexity.
