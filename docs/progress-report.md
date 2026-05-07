# Implementation Progress Report

**Project:** Neighborhood Library
**Last Updated:** 2026-05-07
**Overall Status:** Phase 5.5 Complete; Phase 5.5b spec drafted — paused at user gate before implementing the SigNoz overlay
**Active Phase:** none (awaiting user approval to start Phase 5.5b or proceed to Phase 6)

---

## Phase Status Summary

### Phase 1: Repo & Infra Scaffolding
**Status:** <promise>DONE</promise>
**Completed:** 2026-05-05
**Agents:** elite-engineer, ui-ux-fullstack-savant, cto-code-reviewer
**Spec:** [phases/phase-1-scaffolding.md](phases/phase-1-scaffolding.md)
**Effort:** M (~4 hrs)

#### Implementation summary
- Repo initialized; comprehensive `.gitignore`; root `README.md` skeleton.
- Backend: `pyproject.toml` (uv-managed, Python 3.12, grpcio + grpcio-health-checking + pydantic-settings), `src/library/main.py` (async gRPC server with proactive health-service registration and graceful SIGTERM/SIGINT shutdown), `src/library/config.py` (full Pydantic Settings with all six env knobs including the fine-policy ones), multi-stage Dockerfile (uv, multi-arch grpc_health_probe v0.4.28, non-root user), entrypoint with `TODO(phase-2)` for Alembic.
- Infrastructure: `deploy/envoy/envoy.yaml` byte-identical to spec, `docker-compose.yml` with all four services + gated `seed` profile, persistent `pgdata` volume, `service_healthy` dependencies.
- Frontend: Next 16.2.4 + React 19 + Tailwind v4 + TypeScript + App Router via `create-next-app@latest`. Custom layout/page replacing CNA boilerplate. Frontend `Dockerfile` binds `0.0.0.0:3000`. `.dockerignore` in place.
- Proto skeleton: `proto/library/v1/.gitkeep` placeholder.

#### Improvements on spec (proactively applied by agents)
- **gRPC health service registered in Phase 1** rather than waiting for Phase 4. The `api` healthcheck now passes from first boot rather than flapping for several phases.

#### CTO review
"PASS WITH DISTINCTION." All 14 acceptance criteria pass. No MUST-FIX issues. Detailed review covered acceptance criteria, compose correctness, Dockerfile correctness, code quality (graceful shutdown, Pydantic validators, multi-stage Dockerfile), and spec drift (Next 16, Tailwind v4).

**CTO Certification:** "I solemnly swear this phase is complete, meets all acceptance criteria, and is production-ready."

#### Spec-drift items (deferred to a docs-writer pass before Phase 6)
The frontend scaffold landed on Next 16 / Tailwind v4 because `create-next-app@latest` advanced past Next 15 between spec authoring and Phase 1 implementation. The following lines in `docs/design/04-frontend.md` need updating:
- **Line 17** (`tailwind.config.ts` in tree) — Tailwind v4 uses CSS `@theme`, not a config file. Remove the line or replace with a v4 note.
- **Lines 13-15** (deps summary) — Add `@tailwindcss/postcss`; bump to React 19 / Next 16.
- **Top of doc** — Add a "Note: scaffolded on Next 16, App Router params/searchParams are now async" callout for Phase 6 implementation guidance.

#### Other follow-ups for later phases
- `api` healthcheck `start_period` may need extending in Phase 2 once `alembic upgrade head` is added to the entrypoint and migration time is empirically known.
- `config.py` could expose a `_reset_settings_for_tests()` helper for Phase 4 test fixtures.
- `pyproject.toml` upper bound `<3.13` on Python is tight; consider relaxing to `<4`.
- `LICENSE` file is missing — flag for Phase 7 polish.

---

### Phase 2: Schema & Migrations
**Status:** <promise>DONE</promise>
**Completed:** 2026-05-05
**Agents:** elite-engineer, cto-code-reviewer
**Spec:** [phases/phase-2-schema-migrations.md](phases/phase-2-schema-migrations.md)
**Effort:** S (~2 hrs)

#### Implementation summary
- **SQLAlchemy 2.0 typed ORM:** `db/models.py` with `DeclarativeBase` + `Mapped[...]` / `mapped_column()` for all four entities (Book, Member, BookCopy, Loan). `CopyStatus(str, enum.Enum)` mapped via `sa.Enum(..., create_type=False)` since the migration owns the enum lifecycle.
- **Alembic migration:** `alembic/versions/0001_initial.py` hand-authored to match `docs/design/01-database.md` §1 byte-for-byte. Includes `op.execute("CREATE TYPE copy_status …")`, functional `lower(...)` indexes, composite `(book_id, status)` index, and the **partial unique index** `loans_one_active_per_copy_idx ON loans(copy_id) WHERE returned_at IS NULL` — the structural double-borrow guarantee.
- **Async-aware Alembic env:** `alembic/env.py` follows the canonical SQLAlchemy/Alembic asyncio cookbook (`async_engine_from_config` + `connection.run_sync(_do_run_migrations)`), with `pool.NullPool`, both online + offline modes, and URL pulled from `library.config.get_settings()` rather than `alembic.ini`.
- **Engine + sessions:** `db/engine.py` lazy `get_engine()`, `AsyncSessionLocal` proxy supporting deferred URL resolution, `get_session()` async generator with commit-on-success / rollback-on-exception / always-close lifecycle. Password-redacted startup log line.
- **Container wiring:** `entrypoint.sh` now runs `alembic upgrade head` before launching the gRPC server. `Dockerfile` copies `alembic.ini` and `alembic/` in both builder and runtime stages.

#### Verification (all 11 checks passed during implementation)
1. `docker compose up` — api healthy, entrypoint logs migration then server start
2. `\dt` — all 5 tables visible (alembic_version, book_copies, books, loans, members)
3. `\d loans` — partial unique index `loans_one_active_per_copy_idx UNIQUE, btree (copy_id) WHERE returned_at IS NULL` present
4. `\d book_copies` — copy_status enum + composite index correctly emitted
5. `\d books` / `\d members` — all functional `lower(...)` indexes present
6. `\dT+ copy_status` — enum values `AVAILABLE, BORROWED, LOST` in order
7. Idempotent re-run: `alembic upgrade head` is a no-op
8. Round-trip: `alembic downgrade base` then `upgrade head` reinstates schema cleanly
9. ORM imports: `from library.db import Base, AsyncSessionLocal, get_engine, get_session` works
10. Live async session: `await session.execute(text('SELECT 1'))` returns `1`
11. **Functional invariant proof:** double-borrow against the same copy rejected with unique violation; case-insensitive email duplicate rejected. The partial unique index is structurally enforcing the right rule.

#### CTO review
"PASS WITH DISTINCTION." No MUST-FIX issues. Highlights: schema fidelity is byte-aligned with the design doc, enum lifecycle correctly partitioned (migration owns it via `op.execute` + `create_type=False` everywhere else), async migrations done by the cookbook, lazy engine + lazy sessionmaker for testability.

**CTO Certification:** "I solemnly swear this phase is complete, meets all acceptance criteria, and is production-ready."

#### Notes for Phase 4
- Phase 4 testing checklist should add: integration test exercising `get_session()`'s rollback path (raise inside the `async for` block, assert no partial commit).
- Optional process improvement: consider a CI check running `alembic upgrade head && alembic downgrade base && alembic upgrade head` on every PR to guard against future migrations breaking downgrade.

### Phase 3: Protobuf Contract & Codegen
**Status:** <promise>DONE</promise>
**Completed:** 2026-05-06
**Spec:** [phases/phase-3-proto-codegen.md](phases/phase-3-proto-codegen.md)
**Effort:** M (~2 hrs — slightly under spec since the codegen path was straightforward once the import-rewrite quirk was diagnosed)

#### Implementation summary
- **Proto contract:** `proto/library/v1/library.proto` authored byte-for-byte from [docs/design/02-api-contract.md §1](design/02-api-contract.md#1-full-proto). Single source of truth at the repo root, consumed by both backend and frontend. Old `.gitkeep` placeholder removed.
- **Backend codegen:** `backend/scripts/gen_proto.sh` runs `python -m grpc_tools.protoc` with `--python_out` / `--grpc_python_out` / `--pyi_out` into `src/library/generated/`, writes `__init__.py` markers at each package level, and rewrites the protoc-emitted import `from library.v1 import library_pb2` → `from library.generated.library.v1 import library_pb2` inside `_pb2_grpc.py` so the file resolves under the deeper namespace where it actually lives.
- **Frontend codegen:** `frontend/buf.gen.yaml` (v2 syntax) drives `protoc-gen-es` and `protoc-gen-connect-es` as local plugins, emitting `target=ts` straight into `src/generated/`. `package.json` gains a `gen:proto` script (`buf generate ../proto`) plus pinned deps:
  - **Runtime (deps):** `@bufbuild/protobuf@1.10.0`, `@connectrpc/connect@1.6.1`, `@connectrpc/connect-web@1.6.1`.
  - **Codegen (devDeps):** `@bufbuild/buf@1.50.0`, `@bufbuild/protoc-gen-es@1.10.0`, `@connectrpc/protoc-gen-connect-es@1.6.1`.
- **Container wiring via named build contexts:** `docker-compose.yml` declares `additional_contexts: { proto: ./proto }` on the `api`, `web`, and `seed` services. Both Dockerfiles `COPY --from=proto . /app/proto` and run their respective codegen step before the rest of the build.
  - Backend: `RUN PATH="/app/.venv/bin:${PATH}" PROTO_DIR=/app/proto bash /app/scripts/gen_proto.sh` (PROTO_DIR override needed because the script's default repo-relative path resolution doesn't match the in-container layout where the proto context lands at `/app/proto`).
  - Frontend: `RUN npx --no-install buf generate /app/proto` (calling `buf` directly rather than the `npm run gen:proto` script for the same reason — the script's `../proto` is host-relative).
- **`.dockerignore` files:** added `backend/.dockerignore` (excludes venv, caches, `src/library/generated/`, env files, OS cruft); extended `frontend/.dockerignore` to exclude `src/generated/`. Local regenerations stay out of the build context so the Dockerfile's codegen step is the only producer.

#### Spec deviations & rationale
- **Build context strategy.** The Phase 3 spec mentioned "Backend Dockerfile copies `proto/` from the build context; frontend Dockerfile likewise. Don't duplicate the `.proto`." We achieved the no-duplication goal via Compose v2 named build contexts (`additional_contexts`) rather than restructuring the per-service `context:` to the repo root. This keeps the per-service contexts tight (smaller transfer to the Docker daemon) while still letting both Dockerfiles see the shared proto. Modern Docker Compose (BuildKit) feature; validated with `docker compose config --quiet`.
- **Import rewrite for `_pb2_grpc.py`.** `grpc_tools.protoc` emits `from library.v1 import library_pb2` as the cross-module import inside `_pb2_grpc.py`, which assumes the proto package sits at the import root. Because we deliberately house generated code one level deeper (`library.generated.library.v1`) per [docs/design/03-backend.md](design/03-backend.md), a single `perl -pi` rewrite at the end of `gen_proto.sh` realigns the import. Industry-standard fix; documented inline in the script.

#### Verification
- `bash backend/scripts/gen_proto.sh` produces `library_pb2.py`, `library_pb2_grpc.py`, `library_pb2.pyi` plus `__init__.py` markers — files non-empty.
- `python -c "from library.generated.library.v1 import library_pb2, library_pb2_grpc; library_pb2.BorrowBookRequest(book_id=42, member_id=7); library_pb2_grpc.LibraryServiceServicer; library_pb2_grpc.add_LibraryServiceServicer_to_server"` — passes.
- Verified the rewrite landed: `head` on `_pb2_grpc.py` shows `from library.generated.library.v1 import library_pb2 as ...`.
- `npx buf generate ../proto` (via `npm run gen:proto`) produces `library_pb.ts` (1421 lines) and `library_connect.ts` (125 lines) — service descriptor exports `LibraryService.typeName === "library.v1.LibraryService"` with all 12 methods (`createBook`, `updateBook`, …, `borrowBook`, `returnBook`, `listLoans`, `getMemberLoans`).
- `npx tsc --noEmit` against a temporary smoke file importing `LibraryService` and constructing `new BorrowBookRequest({ bookId, memberId })` — exit 0. Smoke file removed before commit; `src/lib/client.ts` is built for real in Phase 6.
- `docker compose config --quiet` — passes.
- `docker compose build api` — full image builds; `docker run --rm --entrypoint python neighborhood-library/api:dev -c "from library.generated.library.v1 import library_pb2, library_pb2_grpc; ..."` — imports succeed inside the container.
- `docker compose build web` — full image builds; the generated `library_pb.ts` and `library_connect.ts` are present at `/app/src/generated/library/v1/` inside the image.

#### Notes for downstream phases
- **Phase 6 frontend tsconfig.** The smoke check tripped on `BigInt` literals (`1n`) because the Phase 1 scaffold's `tsconfig.json` targets `ES2017`. `int64` proto fields surface as `bigint` in the generated TS, and `bigint` literal syntax requires `target: "ES2020"` or higher. Phase 6 will need to bump the target (or use `BigInt(...)` constructor calls everywhere). Worth flagging this in the docs-writer pass on `docs/design/04-frontend.md`.
- **Local-host generated stubs.** The host-side `backend/src/library/generated/` and `frontend/src/generated/` directories were populated during verification. Both are gitignored *and* dockerignored, so they neither commit nor pollute the build context — but contributors should treat them as caches and run the codegen step themselves before any local development.
- **Phase 4 servicer scaffolding.** `library_pb2_grpc.LibraryServiceServicer` is the abstract base class to inherit. Methods are async-friendly (the generated stubs work with `grpc.aio`). All 12 RPCs are unary so `grpc.aio.AioRpcError`-compatible exception handling in `servicer.py` will be straightforward.
- **`scripts/sample_client.py` / `scripts/seed.py` (Phase 7).** Both will use `library_pb2_grpc.LibraryServiceStub` against `api:50051`. The stub class is already available in the generated module — verified above.

### Phase 4: Backend CRUD: Books & Members
**Status:** <promise>DONE</promise>
**Completed:** 2026-05-06
**Spec:** [phases/phase-4-backend-crud.md](phases/phase-4-backend-crud.md)
**Effort:** L (~6 hrs — under spec L estimate; the layering established in Phases 1–3 paid off)

#### Implementation summary
- **`errors.py`** — `DomainError` base + `NotFound` / `AlreadyExists` / `InvalidArgument` / `FailedPrecondition` typed exceptions, plus `@map_domain_errors` decorator that translates domain exceptions into the matching `grpc.StatusCode` via `context.abort`. `AioRpcError` from `abort` propagates untouched; everything else surfaces as `INTERNAL` with the traceback logged but not returned to the client.
- **Repositories** (no proto imports, per [docs/design/03-backend.md §3](design/03-backend.md)):
  - `repositories/books.py` — `create`, `get`, `list_books`, `update_book`. The list query is the `LEFT JOIN book_copies + COUNT FILTER (WHERE status='AVAILABLE')` aggregate from [design/01-database.md §4](design/01-database.md#4-computing-available_copies). LIKE-prefix search on `lower(title)` / `lower(author)` with explicit `\\` escaping of `%` / `_` / `\\` so user input can't inject wildcards. Copy reconciliation (`_reconcile_copies`) only touches `AVAILABLE` rows; rejects with `FailedPrecondition` if the request would require removing borrowed/lost copies. Removal ordered by `id ASC` for deterministic test pinning.
  - `repositories/members.py` — analogous CRUD + search. `IntegrityError` from the `members_email_unique_idx` is detected via `orig.constraint_name` (with a substring fallback) and translated to `AlreadyExists`.
  - `updated_at` is application-set with `datetime.now(timezone.utc)` rather than `func.now()`. Setting a SQL expression instead of a Python value forces SQLAlchemy to refresh from the DB on next read, which goes through the asyncpg async bridge — and that triggered a `MissingGreenlet` when the proto-conversion code (sync function) accessed the attribute. Caught and fixed during integration testing; documented inline.
- **Services** (proto ↔ domain + transactions):
  - `services/conversions.py` — `clamp_pagination`, `datetime_to_pb`, `normalize_search`. Pagination rules: `offset < 0` and `page_size < 0` raise `InvalidArgument`; `page_size == 0` defaults to 25; `page_size > 100` clamps to 100.
  - `services/book_service.py` and `services/member_service.py` — one method per RPC, validation, transaction boundary via `async with session_factory.begin() as session`, response message construction. `Member.outstanding_fines_cents` is hardcoded to `0` with a `TODO(phase-5)` — Phase 5 swaps in the real `compute_fine_cents` aggregate.
- **`servicer.py`** — `LibraryServicer(library_pb2_grpc.LibraryServiceServicer)` overriding the eight book/member methods. Each method is a thin `await self._foo_service.x(request)` wrapped by `@map_domain_errors`. The four loan RPCs are deliberately not overridden so the generated base class returns `UNIMPLEMENTED` for them until Phase 5.
- **`main.py`** — registers `LibraryServicer(AsyncSessionLocal)` on the server, sets the per-service health entry `library.v1.LibraryService = SERVING`, adds `LibraryService` to the reflection set, drains both health entries on shutdown.
- **Test infrastructure** (`tests/conftest.py`):
  - Session-scoped Postgres 16 testcontainer (`testcontainers[postgresql]`).
  - Sync `_configure_environment` autouse fixture that sets `DATABASE_URL` and resets `library.config._settings` / `library.db.engine._engine` / `library.db.engine._sessionmaker` to None so the lazy singletons rebuild against the test URL.
  - Sync `_migrated_schema` autouse fixture that runs `alembic upgrade head` programmatically against the testcontainer.
  - Async `grpc_server` session-scoped fixture starting an in-process `grpc.aio` server on a random port with `LibraryServicer` registered.
  - `library_channel` + `library_stub` session-scoped fixtures.
  - Autouse function-scoped `_clean_db` that `TRUNCATE ... RESTART IDENTITY CASCADE` between tests.
  - `pyproject.toml` pinned `asyncio_default_fixture_loop_scope = "session"` AND `asyncio_default_test_loop_scope = "session"` so the session-scoped server / channel / stubs are usable across tests without "Future attached to a different loop" errors.
- **Tests** — 39 integration tests across `test_books.py` (23) and `test_members.py` (16). Coverage: all eight RPC happy paths; validation cases (empty title/author, name/email, invalid number_of_copies); NOT_FOUND for missing IDs; case-insensitive duplicate email rejection on both create and update; copy reconciliation up, down, and the down-below-borrowed rejection (set up by directly mutating `book_copies.status` since Phase 5 owns the borrow flow); pagination with default-on-zero and explicit page sizes; case-insensitive prefix search on title/author and name/email; clearing optional wrapper fields on update.

#### Spec deviations & rationale
- **`UpdateBook` allows `number_of_copies = 0`** while `CreateBook` requires `>= 1`. The phase spec lists `>= 1` for `number_of_copies` on Create only; on Update, an explicit `0` is a valid librarian action ("take this title out of circulation") and the reconciliation safeguard already prevents the dangerous case (borrowed copies still on the books).
- **Pagination edge cases.** The spec says "page_size <= 0 → use default" and "All return INVALID_ARGUMENT (not silent fix) when the input is malformed; clamp only for 0/missing." We took the consistent reading: `page_size == 0` defaults silently (proto3 default = 0 = "client didn't set"), `page_size < 0` is malformed input → `InvalidArgument`, `page_size > 100` clamps silently. `offset < 0` is malformed → `InvalidArgument`.
- **Connection lifecycle.** Engine and sessionmaker stay singletons; tests reset them by direct assignment to `None`. Cleaner than threading "test-only" reset helpers into `library.config` and `library.db.engine`, and the privates are documented with the rationale inside `conftest.py`.

#### Verification
- `pytest backend/tests/integration/` → **39 passed in 5.14s**.
- `docker compose build api` — image builds with the new servicer.
- `docker compose up postgres api` — both services reach `healthy` (the api health entry now reports the per-service `library.v1.LibraryService` as SERVING in addition to the overall server entry).
- Direct gRPC smoke against `localhost:50051` from a Python client using the generated stubs: `ListBooks` empty, `CreateBook` (with isbn+published_year wrappers), `CreateMember`, `ListBooks` populated, `ListMembers` populated, `CreateMember` with case-shifted duplicate email returns `ALREADY_EXISTS` with the expected message.
- `grpcurl` not available on the host, so the spec's `grpcurl ListBooks` smoke is replaced by the Python-stub equivalent above (same wire format, same path through the servicer).
- `docker compose up envoy` did not run during this verification because port 8080 is occupied on the host by another process (unrelated to this project). Envoy + frontend integration is exercised in Phase 6; the standard gRPC path through `:50051` is independent of Envoy.

#### Notes for downstream phases
- **Phase 5 borrow/return flow.** The `loan_service` / `repositories/loans.py` modules will need to update `Member.outstanding_fines_cents` computation in `services/member_service._member_to_proto` (currently `0` with `TODO(phase-5)`). The `compute_fine_cents` pure function lives at `services/fines.py` per the design doc; it will be unit-tested per [reference/testing.md §1](reference/testing.md). The `update_book` repository's `_reconcile_copies` already enforces the "can't drop below borrowed" invariant the borrow flow depends on.
- **Phase 5 servicer methods.** The four loan methods (`BorrowBook`, `ReturnBook`, `ListLoans`, `GetMemberLoans`) currently fall through to the generated base class's `UNIMPLEMENTED` response. Phase 5 just adds them to `servicer.py` with the same `@map_domain_errors` decorator pattern.
- **Phase 5 concurrency tests.** The session-loop fixture already supports parallel `asyncio.gather(...)` invocations against the in-process server. The `tests/integration/test_concurrency.py` file from the design doc plugs straight into the existing `library_stub` fixture.
- **Phase 6 tsconfig bump.** Carries over from Phase 3: the frontend's tsconfig still targets ES2017, which trips on `bigint` literals from `int64` proto fields. Update before the first `lib/client.ts` call.

### Phase 5: Borrow & Return with Concurrency + Fines
**Status:** Not Started — ready (Phase 4 satisfies dependencies)
**Dependencies:** Phase 4 ✅
**Spec:** [phases/phase-5-borrow-return-fines.md](phases/phase-5-borrow-return-fines.md)

### Phase 5: Borrow & Return with Concurrency + Fines
**Status:** <promise>DONE</promise>
**Completed:** 2026-05-07
**Spec:** [phases/phase-5-borrow-return-fines.md](phases/phase-5-borrow-return-fines.md)
**Effort:** L (~5 hrs — well under spec L estimate; the layering and test harness from Phases 3–4 paid off heavily)

#### Implementation summary
- **`services/fines.py`** — pure `compute_fine_cents(due_at, returned_at, now, grace_days, per_day_cents, cap_cents) -> int`. Reference time is `returned_at` when the loan is returned (snapshot) else `now` (still accruing). Days computed via `timedelta.days` (integer floor). 17 unit tests cover every row of the policy table from [design/01-database.md §5](design/01-database.md): within grace, exactly at boundary, mid-fine, at cap, beyond cap, returned within grace, returned past grace (snapshot, snapshot above cap), returned-before-due (zero), parametrized progression sweep.
- **`repositories/loans.py`** — owns the borrow concurrency strategy and the SQL form of the fine formula:
  - `borrow(...)` — checks member + book existence (clean `NotFound`), then `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` an `AVAILABLE` copy, INSERT the loan, flip the copy to `BORROWED`. Catches `IntegrityError` against `loans_one_active_per_copy_idx` and translates to `FailedPrecondition` as a backstop the partial-unique-index enforces structurally.
  - `return_loan(...)` — `SELECT ... FOR UPDATE` the loan row, reject with `FailedPrecondition` if already returned, set `returned_at = now`, flip the copy back to `AVAILABLE`.
  - `_fine_expression(now, fines)` — SQLAlchemy expression mirroring the Python formula: `LEAST(cap, GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (COALESCE(returned_at, :now) - due_at)) / 86400)::int - grace) * per_day)`. Used by the `LOAN_FILTER_HAS_FINE` predicate and by `sum_member_fines`. Python and SQL forms must agree; tests exercise both.
  - `list_loans(...)` — JOIN to `book_copies`, `books`, `members` for the denormalized fields the proto expects; supports member/book filters and the five `LoanFilter` variants. Ordered most-recent-first with `(borrowed_at DESC, id DESC)` for stable pagination.
  - `get_member_loans(...)` — same shape but no pagination per spec (a single member's loan history is bounded).
  - `sum_member_fines(...)` — single-aggregate `SUM(LEAST(...))` over the member's loans (rather than fetching loans and summing in Python).
  - Domain `LoanFilter` IntEnum mirrors the proto enum integers exactly so service code can map `request.filter` → repo enum trivially while keeping the repo proto-free.
- **`services/loan_service.py`** — borrow/return/list/get_member_loans methods. `due_at` defaults to `now + DEFAULT_LOAN_DAYS` when the client doesn't set the wrapper; explicit past-due dates are rejected with `InvalidArgument`. The proto's `Loan.overdue` and `Loan.fine_cents` are computed in Python on every response build via `compute_fine_cents`, so the formula has a single source of truth and is fully unit-testable.
- **`services/member_service.py`** updated — `_member_to_proto` now takes `outstanding_fines_cents` as a keyword argument. `GetMember` calls `loans_repo.sum_member_fines` and passes the real value; `Create` / `Update` / `List` pass 0 (Create has no loans by definition; Update is a write op; ListMembers paying N+1 queries on every page isn't justified for the dashboard's purposes per [design/04-frontend.md](design/04-frontend.md), which only needs system-wide fines, not per-row).
- **`servicer.py`** — registers all four loan RPCs with the same `@map_domain_errors` pattern. Constructor now also takes a `Settings` (defaulting to `get_settings()`) which it threads through to `LoanService` and `MemberService` for the fine config.

#### Verification
- **17 unit tests** — `pytest backend/tests/unit/`. Covers every row of the design-doc fine policy table.
- **63 integration tests** — `pytest backend/tests/integration/`:
  - 39 from Phase 4 (books + members) still green after the `member_service` refactor for `outstanding_fines_cents`.
  - 22 in `test_borrow_return.py`: borrow happy path, explicit + default `due_at`, past-`due_at` rejection, no-copies rejection, NOT_FOUND on book or member, return happy path (with available-copies state-transition assertion), return-already-returned rejection, return-not-found, invalid-arg, overdue flag (1 day overdue, no fine yet — within grace), one-day-past-grace fine, at-cap fine, returned-late snapshot fine + overdue=false invariant, multi-loan `outstanding_fines_cents` aggregate, member-with-no-loans returns 0 fines, all five `LoanFilter` variants surfacing the right loans, `member_id` and `book_id` scoping, `GetMemberLoans` ordering and filter and member-not-found.
  - 2 in `test_concurrency.py`: 10 racers vs 1-copy book → exactly 1 winner, 9 `FAILED_PRECONDITION`, 0 unexpected errors, final DB state has 1 active loan + 1 BORROWED copy + 0 AVAILABLE copies. Same 10 racers vs 2-copy book → exactly 2 winners on distinct copies (proves `SKIP LOCKED` lets concurrent borrows of *different* copies parallelize).
- **End-to-end smoke** against the live `docker compose up postgres api` stack via the generated Python stubs: create book → create member → borrow (denormalized title/member-name populated, `fine_cents=0`, `overdue=false`) → list active (count 1) → second borrow rejected with `FAILED_PRECONDITION` → return (`returned_at` set, fine 0) → list active (count 0). All checks observed.
- Total runtime: 80 tests in **6.96s** (one shared testcontainer + in-process server).

#### Notes for Phase 6 / 7
- **Frontend dashboard tile.** `/` shows total outstanding fines per [design/04-frontend.md §4](design/04-frontend.md). The cheapest implementation is a small server-side helper (a new repo function or a single SQL query summing `_fine_expression` across all loans) that Phase 6 can call once on dashboard load. Out of scope for Phase 5; flag as a small Phase 6 task.
- **Sample client / seed scripts.** `backend/scripts/sample_client.py` (Phase 7) should walk through the full borrow → return → list lifecycle. Phase 5's end-to-end smoke is the seed for that — paste-ready.
- **Time injection.** Production code currently calls `datetime.now(timezone.utc)` at the top of each service method. Tests work around this by backdating `due_at` / `returned_at` directly in the DB. If Phase 7 ever wants reproducible time-dependent demos, refactor `LoanService` to take a `now_fn` callable; small change, mostly mechanical.
- **`return_loan` re-fetch.** After flipping the copy and setting `returned_at`, the repo issues a second SELECT-with-joins to rebuild the `LoanRow` for the response. One extra round trip per return; trade for simpler proto-conversion code that doesn't have to know about joins. Acceptable at scale; could fold into a single CTE if it ever shows up in a profile.

### Phase 5.5: Observability Instrumentation
**Status:** <promise>DONE</promise>
**Completed:** 2026-05-07
**Spec:** [phases/phase-5-5-observability.md](phases/phase-5-5-observability.md)
**Design:** [design/06-observability.md](design/06-observability.md)
**Effort:** M (~4 hrs — under spec; the layering established in Phases 1–5 paid off)

#### Implementation summary
- **`library/observability/` package** (3 modules):
  - `setup.py` — `init_telemetry()` builds `TracerProvider` + `LoggerProvider` from `OTEL_*` env vars, runs `SQLAlchemyInstrumentor` and `AsyncPGInstrumentor` for auto SQL spans, returns shutdown hooks for the drain path.
  - `interceptors.py` — `RequestContextInterceptor` generates `request.id = uuid4()`, stamps it on the active span and a contextvar, emits one INFO access log per RPC. Detects sync vs async handlers (the standard `HealthServicer` ships sync handlers — initial implementation tripped on `await`).
  - `logging_config.py` — `JsonFormatter` reading active OTel context + the request-id contextvar, plus a `redact_email` utility. Replaces the prior `logging.basicConfig` plaintext setup.
- **`main.py`** — calls `init_telemetry()` before `_build_server()`; the gRPC server is constructed with `[grpc_otel_server_interceptor(), RequestContextInterceptor()]` so the OTel root span exists before the request-context interceptor stamps `request.id` onto it. `telemetry.shutdown()` runs in the drain path.
- **`errors.py`** — `map_domain_errors` now sets `Status(StatusCode.ERROR)` and calls `record_exception(...)` on the active span for both `DomainError` and unhandled-`Exception` paths. Trace UIs render errored spans red and the stack trace shows up as a span event.
- **Manual spans + events at the 7 hotspots from the design doc**:
  - `loan_service.borrow_book`: `borrow.validate`, `borrow.transaction`, `borrow.build_response` spans + `loan.created` event with `loan_id`, `copy_id`, `book_id`, `member_id`.
  - `repositories/loans.py:borrow`: `borrow.pick_copy` span around the FOR UPDATE SKIP LOCKED query + `copy_picked` event on success, `loan.contention` event when no copy can be locked.
  - `loan_service.return_book`: `return.transaction`, `return.build_response` spans + `loan.returned` event with `fine_cents`, `was_overdue`, `days_late` (the snapshot moment captured for dashboards).
  - `loan_service.list_loans`: `list_loans` span with filter + page-size attrs, `list.returned` event with counts.
  - `loan_service.get_member_loans`: `member_loans.returned` event on the auto root span.
  - `member_service.get_member`: `fines.aggregate` span around `sum_member_fines` + `fines.computed`, `member.fetched` events.
  - `member_service.create_member` / `update_member`: `member.created` / `member.updated` events.
  - `book_service.create_book` / `update_book`: `book.created` event; `books.reconcile_copies` span with `copies.reconciled` / `copies.reconciliation_rejected` events.
- **`docker-compose.yml`** — full standard OTel env-var set added to the api service with Compose interpolation defaults, so Phase 5.5b can flip values via `.env.observability` without editing the YAML.
- **5 new integration tests** in `tests/integration/test_observability.py`:
  - InMemorySpanExporter fixture wired into the global tracer provider.
  - Borrow happy path → all 4 manual spans appear, `loan.created` event emitted exactly once with the right ID attrs.
  - Borrow no-copies → `loan.contention` event emitted with `library.book_id`.
  - Return happy path → `loan.returned` event with `fine_cents=0`, `was_overdue=False`, `days_late=0`.
  - Every RPC root span carries `request.id` (uuid4 hex, 32 chars).
  - **PII smoke**: borrow flow uses `UNIQUE_MEMBER_NAME`, `unique@example.com`, `UNIQUEBOOKTITLE`, `UNIQUEAUTHOR` and asserts none of those substrings appear in any span attribute or event attribute across the captured trace.
- **Test conftest** updated to register the same interceptor stack as production and `init_telemetry()` with `OTEL_TRACES_EXPORTER=none` / `OTEL_LOGS_EXPORTER=none` so init runs but doesn't dump JSON to stderr; observability tests install the InMemorySpanExporter on top.

#### Verification
- `pytest backend/tests/` → **85 passed in 5.32s** (80 prior + 5 new). Test runtime regressed by ~3% — well within the 10% budget from the spec.
- Container build clean (`docker compose build api`).
- End-to-end smoke against the live container: create book + create member + borrow + return.
  - JSON access log lines emitted per RPC with `trace_id`, `span_id`, `request.id`, `rpc.method`, `rpc.status`, `rpc.duration_ms`, `peer` — all populated correctly.
  - Span tree (visible from console exporter) shows all 4 borrow manual spans, all 2 return manual spans, plus `book.created`, `copy_picked`, `loan.created`, `loan.returned` events with non-PII attrs.
  - Health-check probes don't spam the access log (filtered out per phase notes).

#### Bugs caught & fixed during implementation
- **Sync handler in HealthServicer.** The interceptor unconditionally `await inner(...)` failed with `TypeError: object HealthCheckResponse can't be used in 'await' expression`. Fixed with `inspect.isawaitable(result)` check. Documented inline.
- **`%f` strftime placeholder in JSON timestamp.** `logging.Formatter.formatTime` doesn't support microseconds (uses `time.struct_time`). Fixed by formatting via `datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")`.

#### Notes for Phase 5.5b
- The OTel env vars are already plumbed with Compose `${VAR:-default}` interpolation. The 5.5b overlay only needs to ship the SigNoz services + collector config + the `.env.observability` file that flips `OTEL_TRACES_EXPORTER` / `OTEL_LOGS_EXPORTER` to `otlp` and points `OTEL_EXPORTER_OTLP_ENDPOINT` at the collector. **Zero application code changes** for 5.5b — exactly the layering the design doc promised.

### Phase 5.5b: Observability Backend (SigNoz Local Overlay)

### Phase 5.5b: Observability Backend (SigNoz Local Overlay)
**Status:** Spec drafted, not yet started
**Dependencies:** Phase 5.5
**Spec:** [phases/phase-5-5b-observability-backend.md](phases/phase-5-5b-observability-backend.md)
**Design:** [design/06-observability.md §8.2](design/06-observability.md)
**Effort:** S (~1.5–2 hrs)

#### Why this phase exists
Pairs with Phase 5.5 — once the app is emitting OTel data, this phase plugs in **SigNoz** as a self-hosted backend so traces and logs are viewable in a real UI (`localhost:3301`). Single project, single UI for traces + logs + future metrics; no Loki, no Grafana, no separate Prometheus. SigNoz is gated behind a Compose **profile** (`--profile observability`) so the default `docker compose up` flow stays lean. The application's behavior is identical with or without the profile; only env-var values flip from console exporter to OTLP.

### Phase 6: Frontend MVP
**Status:** Not Started
**Dependencies:** Phase 4, Phase 5 (and the docs/design/04-frontend.md update flagged above)
**Spec:** [phases/phase-6-frontend-mvp.md](phases/phase-6-frontend-mvp.md)

### Phase 7: Polish: Seed, Sample Client, README
**Status:** Not Started
**Dependencies:** Phase 6
**Spec:** [phases/phase-7-polish.md](phases/phase-7-polish.md)

---

## Verification status

- `git status`: 12 backend/infra files + frontend tree staged on `main`. No commit created (user has not requested one).
- `python -c "import ast; ast.parse(...)"` on `main.py`, `config.py`: PASS.
- `tomllib.loads(...)` on `pyproject.toml`: PASS.
- `yaml.safe_load(...)` on `docker-compose.yml`, `envoy.yaml`: PASS.
- Compose structural assertions (services, ports, env vars, healthchecks, depends_on, volumes, seed profile): PASS.
- `npx tsc --noEmit` in `frontend/`: PASS (exit 0).
- `docker compose config --quiet`: **NOT RUN** — Docker is not installed on this host. Recommend the user run this locally as a pre-Phase-2 sanity check.

## Notes

User explicitly scoped this orchestration to **Phase 1 only**. Phase 2 will be started in a separate user request.
