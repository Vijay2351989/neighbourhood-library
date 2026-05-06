# Phase 4 — Backend CRUD: Books & Members

**Status:** Approved, not yet started
**Last Updated:** 2026-05-05
**Effort:** L (~10 hrs)
**Prerequisites:** [Phase 2](phase-2-schema-migrations.md), [Phase 3](phase-3-proto-codegen.md)
**Blocks:** [Phase 5](phase-5-borrow-return-fines.md), [Phase 6](phase-6-frontend-mvp.md)

---

## Goal

The eight book/member RPCs work end-to-end against a real Postgres, reachable via gRPC and via gRPC-Web through Envoy.

The eight RPCs:
- `CreateBook`, `UpdateBook`, `GetBook`, `ListBooks`
- `CreateMember`, `UpdateMember`, `GetMember`, `ListMembers`

---

## Related design docs

- [design/01-database.md](../design/01-database.md) — schema, including `available_copies` aggregate query
- [design/02-api-contract.md](../design/02-api-contract.md) — message shapes and error semantics
- [design/03-backend.md](../design/03-backend.md) — module layout and layering rules
- [reference/testing.md](../reference/testing.md) — testcontainers-postgres pattern

---

## Scope

### In
- `repositories/books.py`, `repositories/members.py` — async SQLAlchemy code for create / update / get / list with search and pagination.
- `services/book_service.py`, `services/member_service.py` — protobuf↔domain conversion, validation, error raising.
- `errors.py` — `NotFound`, `AlreadyExists`, `InvalidArgument` exceptions plus the decorator that the servicer applies.
- `servicer.py` — implements the eight book/member methods on `LibraryServiceServicer`.
- Validation: empty title/author rejected, `page_size > 100` clamped, duplicate email → `ALREADY_EXISTS`, etc.
- For `CreateBook` with `number_of_copies = N`, the service creates the book + N `book_copies` rows in one transaction.
- For `UpdateBook` with a new `number_of_copies`, the service reconciles: add new `AVAILABLE` rows or remove existing `AVAILABLE` rows (refusing if the count would drop below currently-`BORROWED`).
- Wire up the gRPC health-check service (`grpc.health.v1.Health`) so the `api` healthcheck from [Phase 1](phase-1-scaffolding.md) starts passing.

### Out
- Loan RPCs (Phase 5).
- Frontend integration (Phase 6).
- Seed data (Phase 7).

---

## Deliverables

- All files listed above.
- `tests/integration/test_books.py` — covers create, get, list with search, list with pagination, update (including copy reconciliation), invalid-argument cases, not-found cases.
- `tests/integration/test_members.py` — analogous, plus duplicate-email case.
- Tests use `testcontainers-postgres` and a real grpc client against an in-process server.

---

## Acceptance criteria

- `pytest backend/tests/integration/test_books.py backend/tests/integration/test_members.py` is green.
- From the host: `grpcurl -plaintext localhost:50051 library.v1.LibraryService/ListBooks` returns the expected proto JSON.
- From a browser console at `http://localhost:3000`: a fetch through the generated Connect client to `ListBooks` succeeds (proves Envoy + CORS + codegen).
- The `api` healthcheck in compose passes (the gRPC health service is now registered).

---

## Notes & risks

- **Email uniqueness via unique partial index.** `INSERT` failures map to `ALREADY_EXISTS`. The repository should catch `IntegrityError` and translate, rather than the service guessing.
- **Copy reconciliation in `UpdateBook`.** The "remove available copies" path must use `LIMIT n` plus a deterministic `ORDER BY id ASC` or `DESC` so behavior is reproducible. Tests pin this.
- **Pagination clamping.** Negative `offset` → 400; `page_size <= 0` → use default; `page_size > 100` → clamp to 100. All return `INVALID_ARGUMENT` (not silent fix) when the input is malformed; clamp only for `0`/missing.
- **Transactions per RPC.** Each RPC runs in its own DB transaction (begin in the service layer, commit on success, rollback on exception). Don't rely on autocommit.
