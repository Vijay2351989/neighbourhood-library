# Architecture

The umbrella view of the Neighborhood Library system: components, communication, data, and how the pieces fit. This document is a guided tour — it does not replace the deep design docs in [`design/`](design/), it links into them at the right depth.

> **For first-time readers:** start at §1 (Big picture) and read top-to-bottom.
> **For senior reviewers:** the [Tech-stack rationale](#7-tech-stack-rationale) section in particular justifies every framework / library choice with a tradeoff statement.
> **For developers about to make a change:** jump straight to §6 (Code organization) for the file-tree view.

---

## Table of contents

1. [Big picture](#1-big-picture)
2. [Components](#2-components)
3. [Wire protocol — gRPC, gRPC-Web, and the Envoy bridge](#3-wire-protocol--grpc-grpc-web-and-the-envoy-bridge)
4. [Database](#4-database)
5. [API surface](#5-api-surface)
6. [Code organization](#6-code-organization)
7. [Tech-stack rationale](#7-tech-stack-rationale)
8. [Cross-cutting concerns](#8-cross-cutting-concerns)
9. [Operations](#9-operations)
10. [Documentation map](#10-documentation-map)

---

## 1. Big picture

A four-tier staff-facing application:

```
+---------------+       +---------------+       +---------------------+       +-------------+
|               |       |               |       |                     |       |             |
|   Browser     |       |   Envoy       |       |   Python gRPC API   |       |  Postgres   |
|   Next.js UI  | ----> |   :8080       | ----> |   :50051            | ----> |  :5432      |
|   gRPC-Web    |  HTTP |   gRPC-Web    |  HTTP/2|  Book/Member/Loan   |  TCP  |  library DB |
|   client      |       |   ↔ gRPC      |  gRPC  |  servicers (3×4 RPC)|       |             |
|               |       |   filter      |  native|                     |       |             |
+---------------+       +---------------+       +---------------------+       +-------------+
                                                          ^
                                                          |
                                                  Alembic migrations on boot
                                                  Optional DEMO_MODE seeding
```

The whole stack comes up with a single `docker compose up`. Each tier is a separate container managed by Compose, communicating over Docker's user-defined network.

For a deeper architecture diagram + Compose service table, see [`design/05-infrastructure.md`](design/05-infrastructure.md).

---

## 2. Components

### 2.1 Browser — Next.js + Connect-Web

| Aspect | Detail | Read more |
|---|---|---|
| Framework | Next.js 16 (App Router), React 19, TypeScript, Tailwind v4 | [`design/04-frontend.md`](design/04-frontend.md) |
| Server state | TanStack Query with a typed key factory | `frontend/src/lib/queryKeys.ts` |
| Wire client | `@connectrpc/connect-web` speaking gRPC-Web binary | `frontend/src/lib/client.ts` |
| Generated types | TypeScript message classes (`{book,member,loan}_pb.ts`) and service descriptors (`*_connect.ts`) — one pair per service, from `buf generate ../proto` | [Phase 3](phases/phase-3-proto-codegen.md) |

Single-persona UI: one set of pages, no auth, no role-based visibility. The pages mirror the librarian workflows enumerated in the README's "Key functional scenarios."

### 2.2 Envoy — gRPC-Web ↔ gRPC bridge

Browsers cannot speak native gRPC because they cannot read or send HTTP/2 trailers (which is where gRPC encodes its status codes). gRPC-Web is the browser-friendly variant; Envoy translates between them.

| Aspect | Detail | Read more |
|---|---|---|
| Image | `envoyproxy/envoy:v1.31-latest` | `docker-compose.yml` |
| Listener | `:8080` — accepts gRPC-Web, HTTP/1.1 or HTTP/2 | `deploy/envoy/envoy.yaml` |
| Admin | `:9901` — `/ready`, `/stats`, `/clusters`, etc. | `design/05-infrastructure.md` §1 |
| Filter chain | `grpc_web` → `cors` → `router` (terminal) | `design/05-infrastructure.md` §1 |
| Upstream | `library_grpc` cluster, STRICT_DNS to `api:50051`, HTTP/2 forced | same |

**Wire-level walkthrough** (request bytes, filter actions, the `0x80`-flagged trailer frame, how `grpc-status` ends up in a body frame instead of HTTP/2 trailers): see §3 below for the conceptual trace, or `deploy/envoy/envoy.yaml` and the `grpc_web` filter for the actual config.

### 2.3 Python gRPC API

| Aspect | Detail | Read more |
|---|---|---|
| Runtime | Python 3.12, `grpcio` async (`aio.Server`) | [`design/03-backend.md`](design/03-backend.md) |
| Layering | servicer → service → repository → ORM (no leaks; repositories never touch protobuf, services never write SQL) | same §3 |
| Entry point | `backend/src/library/main.py` — registers `BookServicer`, `MemberServicer`, `LoanServicer`, `Health`, and `ServerReflection` on one server | same |
| Migrations | Alembic, run as the first step of `entrypoint.sh` before the gRPC server starts | [Phase 2](phases/phase-2-schema-migrations.md) |
| Generated code | `{book,member,loan}_pb2.py` (messages) + `*_pb2_grpc.py` (stub + servicer base) — one trio per service, gitignored, regenerated by `scripts/gen_proto.sh` | [Phase 3](phases/phase-3-proto-codegen.md) |

### 2.4 PostgreSQL

| Aspect | Detail | Read more |
|---|---|---|
| Version | Postgres 16 (alpine image) | `docker-compose.yml` |
| Driver | `asyncpg` via SQLAlchemy 2.0 async | `backend/src/library/db/engine.py` |
| Schema | Four tables — `books`, `members`, `book_copies`, `loans` — plus the `copy_status` enum | [`design/01-database.md`](design/01-database.md) |
| Concurrency | Partial unique index on `loans(copy_id) WHERE returned_at IS NULL` + `SELECT ... FOR UPDATE SKIP LOCKED` in the borrow transaction | same §3 |

The schema explicitly distinguishes the *abstract* `Book` (one row per title) from the *physical* `BookCopy` (one row per shelf-item). A `Loan` always references a specific `BookCopy`, never the catalog `Book` directly.

---

## 3. Wire protocol — gRPC, gRPC-Web, and the Envoy bridge

This is the part the system spends the most lines on, because it's the most subtle.

### Why gRPC at all?

The three `.proto` files are the single source of truth for the API surface. Both backend and frontend codegen from them: the backend gets typed `BookServiceServicer` / `MemberServiceServicer` / `LoanServiceServicer` base classes to implement, the frontend gets three typed Connect-Web clients. No drift between client and server, no DTOs to maintain, no OpenAPI to regenerate.

### Why Envoy?

Browsers run on top of `fetch()` / `XMLHttpRequest`, neither of which can read HTTP/2 trailers. But native gRPC encodes its status code (`grpc-status: 0`, `grpc-status: 5`, etc.) in HTTP/2 trailers. So a browser literally cannot decode whether a gRPC call succeeded — even if it could send the request.

**gRPC-Web** is a slightly different wire format: same body framing, but trailers are moved into a special `0x80`-flagged frame *appended to the response body* (where the browser CAN read it). Envoy's `grpc_web` filter does this translation in both directions.

The CORS filter on the same chain handles preflight `OPTIONS` requests and exposes the `grpc-status` / `grpc-message` headers so the JavaScript can read them.

In one round-trip: the browser sends a `POST` with `Content-Type: application/grpc-web+proto` and a body of `[5-byte frame prefix][serialized protobuf]`. Envoy rewrites the content-type to `application/grpc+proto`, injects `te: trailers`, and forwards over HTTP/2 to the Python server. The server responds with HTTP/2 HEADERS + DATA + TRAILERS. Envoy's `grpc_web` filter takes the trailer values, encodes them as ASCII, prepends a `0x80`-flagged length-prefixed frame, and appends that frame to the response body — which the browser reads as plain bytes since it can't access HTTP/2 trailers directly.

### Why the `library_grpc` cluster forces HTTP/2

The `http2_protocol_options: {}` line in `envoy.yaml` forces HTTP/2 on the upstream connection from Envoy to the Python server. Native gRPC requires HTTP/2; the listener accepts HTTP/1.1 from browsers (which gRPC-Web is fine with), but the upstream cannot be HTTP/1.1 — that's the asymmetry Envoy is bridging.

### Reflection

The Python server registers `grpc.reflection.v1alpha.ServerReflection` so tools like `grpcurl` can introspect the service surface without a local copy of the `.proto`. Useful for debugging; cheap to ship.

---

## 4. Database

### Schema overview

```
books                  members                book_copies               loans
─────                  ───────                ───────────               ─────
id                     id                     id                        id
title                  name                   book_id ──→ books.id      copy_id ──→ book_copies.id
author                 email (unique          status enum:              member_id ──→ members.id
isbn (nullable)              on lower())        AVAILABLE                borrowed_at
published_year         phone                    BORROWED                 due_at
created_at             address                  LOST                    returned_at (nullable)
updated_at             created_at             created_at
                       updated_at
```

**Three things worth knowing without reading the full design doc:**

1. **`Book` vs `BookCopy` split.** The catalog (`books`) is an abstract title; physical inventory (`book_copies`) is one row per shelf item. Two paperback copies of *Dune* are one `books` row + two `book_copies` rows. Loans always reference a specific copy.

2. **Partial unique index on active loans.** `CREATE UNIQUE INDEX loans_one_active_per_copy_idx ON loans(copy_id) WHERE returned_at IS NULL`. This makes "double-borrow of the same physical copy" *structurally impossible* at the database layer — the second insert fails with a unique-constraint violation, regardless of any race condition in the application code.

3. **Computed fines, not stored.** Fines accrue at $0.25/day after a 14-day grace past `due_at`, capped at $20. Computed at query time via a pure function `compute_fine_cents(due_at, returned_at, now, ...)`. The schema has no fines column — adding one would require a recurring job to update it as the wall clock advances. See [`design/01-database.md` §5](design/01-database.md#5-fine-policy-computed-not-stored).

### Migrations

Alembic, hand-authored migration `alembic/versions/0001_initial.py`. `entrypoint.sh` runs `alembic upgrade head` before the gRPC server starts, so the schema is always current. Idempotent — safe to re-run on every container start.

For full DDL, indexes, FK behavior, and design rationale, see [`design/01-database.md`](design/01-database.md).

---

## 5. API surface

12 RPCs split across three services — one per subdomain — all under package `library.v1`:

| Service | File | Methods | Purpose |
|---|---|---|---|
| **`library.v1.BookService`** | `proto/library/v1/book.proto` | `CreateBook`, `UpdateBook`, `GetBook`, `ListBooks` | Catalog management. `CreateBook` takes `number_of_copies` and creates the matching `book_copies` rows transactionally. |
| **`library.v1.MemberService`** | `proto/library/v1/member.proto` | `CreateMember`, `UpdateMember`, `GetMember`, `ListMembers` | Patron records. Email is unique on `lower(email)` to prevent case-variant duplicates. |
| **`library.v1.LoanService`** | `proto/library/v1/loan.proto` | `BorrowBook`, `ReturnBook`, `ListLoans`, `GetMemberLoans` | Lending. `BorrowBook` picks any AVAILABLE copy via `FOR UPDATE SKIP LOCKED`, creates the loan, flips the copy status. `ReturnBook` does the reverse. |

The three services share a single HTTP/2 connection — gRPC multiplexes on the wire, so the frontend opens one transport and routes per-service. The proto split is purely organizational; loan messages reference `int64 book_id` / `member_id` rather than embedded book/member messages, so no proto-level dependency exists between the three files.

### Error semantics

| Failure | gRPC status |
|---|---|
| Validation (empty title, bad page size) | `INVALID_ARGUMENT` |
| Resource missing | `NOT_FOUND` |
| Borrow with no copies available | `FAILED_PRECONDITION` |
| Return on already-returned loan | `FAILED_PRECONDITION` |
| Duplicate email | `ALREADY_EXISTS` |
| Reduce copies below currently-borrowed | `FAILED_PRECONDITION` |
| Unexpected | `INTERNAL` |

Full proto definitions (every message, every method, every field number) live at `proto/library/v1/{book,member,loan}.proto`. Detailed prose explanation: [`design/02-api-contract.md`](design/02-api-contract.md).

---

## 6. Code organization

```
neighborhood-library/
├── docker-compose.yml              # 4-service stack (postgres / api / envoy / web)
├── docker-compose.test.yml         # override for the isolated test stack (+1 ports)
├── proto/library/v1/                # single source of truth — shared by both codegens
│   ├── book.proto                   # BookService (4 RPCs)
│   ├── member.proto                 # MemberService (4 RPCs)
│   └── loan.proto                   # LoanService (4 RPCs)
├── deploy/envoy/envoy.yaml         # Envoy listener + filter chain + cluster
├── backend/
│   ├── pyproject.toml              # uv-managed Python project
│   ├── Dockerfile                  # multi-stage (builder + runtime), uv-based
│   ├── entrypoint.sh               # alembic upgrade head → optional reset_and_seed → server
│   ├── alembic/                    # Alembic config + versions/0001_initial.py
│   ├── scripts/
│   │   ├── gen_proto.sh            # backend stubs → src/library/generated/
│   │   ├── reset_and_seed.py       # DEMO_MODE=true seed
│   │   └── sample_client.py        # heavily-commented native-gRPC client tutorial
│   ├── src/library/
│   │   ├── main.py                 # gRPC server entry
│   │   ├── config.py               # Pydantic settings (env vars)
│   │   ├── db/                     # engine, sessionmaker, ORM models
│   │   ├── repositories/           # SQL only; no protobuf imports
│   │   ├── services/               # protobuf ↔ domain; orchestrates repositories; raises typed errors
│   │   ├── servicer.py             # gRPC servicer; thin glue + error→status mapping
│   │   ├── errors.py               # NotFound / FailedPrecondition / InvalidArgument / AlreadyExists
│   │   ├── resilience/             # retry decorator + classifier + backoff + deadline
│   │   ├── observability/          # OTel setup + interceptors + structured access log
│   │   └── generated/              # protoc output — gitignored, regenerated on build
│   └── tests/
│       ├── unit/                   # pure-function tests (fine formula, retry classifier, etc.)
│       └── integration/            # in-process gRPC server + testcontainer Postgres
├── frontend/
│   ├── package.json                # next, react, typescript, tailwind v4, connect-web, tanstack-query
│   ├── Dockerfile                  # next dev for the take-home; would be next start in prod
│   ├── buf.gen.yaml                # protoc-gen-es + protoc-gen-connect-es
│   ├── playwright.config.ts        # chromium-only, retries=1, trace-on-first-retry
│   ├── e2e/happy-path.spec.ts      # single happy-path browser test
│   └── src/
│       ├── app/                    # Next.js App Router pages (Dashboard, Books, Members, Loans)
│       ├── components/             # UI kit (Button, Input, Table, Toast, Dialog) + feature components
│       ├── lib/                    # client.ts (Connect transport), queryKeys.ts, format.ts, errors.ts
│       └── generated/              # buf output — gitignored, regenerated on build
├── docs/                           # everything you're reading
├── test.sh                         # parameterized test runner (full / unit / integration / e2e / stack / teardown)
└── README.md                       # the project front door
```

For per-subsystem details, see [`design/03-backend.md`](design/03-backend.md) and [`design/04-frontend.md`](design/04-frontend.md).

---

## 7. Tech-stack rationale

Every framework / library choice with the tradeoff that justified it:

| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| **Backend language** | Python 3.12 | Required by the assignment. `asyncio` + `grpcio.aio` give us non-blocking I/O without threads. |
| **Backend framework** | `grpcio` directly, no Spring/FastAPI | Assignment specifies gRPC. FastAPI is a REST framework — the wrong abstraction. `grpcio` is the canonical Python gRPC implementation. |
| **DB driver** | `asyncpg` via SQLAlchemy 2.0 async | `psycopg2` blocks the event loop; `asyncpg` is async-native and significantly faster on the wire. SQLAlchemy 2.0's async layer wraps `asyncpg` with a typed ORM. |
| **ORM** | SQLAlchemy 2.0 (typed `Mapped[...]` API) | The 1.x style is legacy. 2.0's typed declarative gives mypy real types instead of `Any`. |
| **Migrations** | Alembic | Ships with SQLAlchemy; the canonical Python migration tool. |
| **Settings** | Pydantic 2 (`pydantic-settings`) | Type-safe, validated, env-driven. Standard in the Python async-services world today. |
| **Wire protocol — backend** | Native gRPC (HTTP/2 + trailers) | What `grpcio` produces. Fast, typed, streaming-capable (unused here). |
| **Wire protocol — browser** | gRPC-Web binary | The only browser-compatible option for a `.proto`-defined service. The alternative is REST + a hand-maintained client, with all the type-drift that entails. |
| **gRPC-Web bridge** | Envoy with the official `grpc_web` filter | The reference implementation. nginx and Caddy have plugins but Envoy is the canonical choice and is well-documented. |
| **Frontend framework** | Next.js 16 (App Router) | Assignment preferred React + Next.js. App Router is the supported path forward; legacy `pages/` is being deprecated. |
| **Frontend wire client** | `@connectrpc/connect-web` | Modern, TypeScript-first, actively maintained. The older `protoc-gen-grpc-web` (Google) works but its tooling has stagnated. See [`reference/decisions.md`](reference/decisions.md) row 5. |
| **Frontend codegen** | `@bufbuild/protoc-gen-es` (messages) + `@connectrpc/protoc-gen-connect-es` (service descriptor) | Two-plugin split because messages are framework-agnostic and reusable; the descriptor is Connect-specific. Lets us swap clients without regenerating message classes. |
| **Frontend state** | TanStack Query | Server-state caching + invalidation + retries are non-trivial to roll by hand. Lighter than Redux for this kind of CRUD app. |
| **Styling** | Tailwind v4 | Utility-first; v4's CSS-driven config (no `tailwind.config.ts`) is cleaner than v3. |
| **Database** | PostgreSQL 16 | Assignment specifies Postgres. 16 is the current stable; the partial-unique-index design we depend on is a Postgres-specific feature. |
| **Container orchestration** | Docker Compose | Sufficient for a single-host take-home. Kubernetes would be overkill. |
| **Tests — backend unit** | `pytest` | Standard. |
| **Tests — backend integration** | `pytest` + `testcontainers-postgres` | Spawns a real ephemeral Postgres per session. Avoids the brittleness of mocked SQLAlchemy + lets us test SQL semantics (FK, partial index, transactions) for real. |
| **Tests — frontend e2e** | Playwright (chromium only) | Drives the real UI. Catches wire-format / CORS / Envoy / browser issues that unit tests can't. |

For decisions that are scoped narrower than "what tech stack" (e.g. ISBN nullability, member email case-insensitivity, member-delete behavior, fine policy parameters), see [`reference/decisions.md`](reference/decisions.md).

---

## 8. Cross-cutting concerns

### 8.1 Resilience

The backend has a typed retry decorator at the service-method layer:

```python
@with_retry(RETRY_WRITE_TX)
async def borrow_book(self, request, context):
    ...
```

Three sanctioned policies — `RETRY_READ` (3 attempts, retries on connection drops + statement timeouts), `RETRY_WRITE_TX` (2 attempts, narrower retryable set because connection drops mid-commit are ambiguous), `RETRY_NEVER`. Errors are classified by SQLSTATE / asyncpg exception type — never by message string (which drifts across Postgres versions).

Each retry's backoff is bounded by the active gRPC deadline read from the request context. If the deadline can't accommodate another sleep, the retry is skipped and the original exception surfaces immediately.

Engine-level timeouts are tuned in concert: `lock_timeout < statement_timeout < command_timeout`, so a lock contention surfaces as the cleaner `LOCK_TIMEOUT` error rather than the ambiguous `STATEMENT_TIMEOUT`.

### 8.2 Observability

OpenTelemetry is wired throughout:

- **gRPC server interceptor** captures every RPC as a span, including request method, status, retry attempts, and deadline budget remaining
- **Span events** for retry attempts, retry exhaustion, and deadline-skipped retries
- **Access log** structured with request method, latency, retry count, status code
- Default exporter is `console`; an opt-in **SigNoz** Compose profile (`docker compose --profile observability up`) ships traces to a local SigNoz instance for visual inspection

### 8.3 Concurrency safety

Beyond the partial unique index discussed in §4, the borrow transaction also uses `SELECT ... FOR UPDATE SKIP LOCKED` to pick an available copy. This lets two concurrent borrows of the same multi-copy book (e.g., two librarians borrowing different copies of *Dune* simultaneously) proceed without blocking each other — `SKIP LOCKED` makes each transaction pick a different un-locked copy.

The two mechanisms compose: `FOR UPDATE SKIP LOCKED` is the optimistic path (no contention → fast); the partial unique index is the pessimistic backstop (race condition past the FOR UPDATE → second insert fails the unique check → caller sees `FAILED_PRECONDITION`).

---

## 9. Operations

### Runtime modes

| Mode | Trigger | What happens |
|---|---|---|
| **Production-style** | `docker compose up` | Schema migrated; empty DB; staff start adding real data |
| **Demo** | `DEMO_MODE=true docker compose up` | Schema migrated; tables truncated + seeded with 21 books, 10 members, 11 loans (including all three fine states); reset on every container restart |

Two modes, one Compose file. No separate deployment manifests, no environment-specific YAML to maintain.

### Health checks

| Service | Check | Purpose |
|---|---|---|
| `postgres` | `pg_isready` | Connection accepting clients |
| `api` | `grpc_health_probe -addr=127.0.0.1:50051` | gRPC server registered the standard `Health/Check` |
| `envoy` | `bash -c "exec 3<>/dev/tcp/127.0.0.1/9901"` | Admin port reachable (the official envoy image lacks `wget`/`curl`, so `/dev/tcp` is the only portable check) |
| `web` | (none) | Next.js dev server failures show in logs |

### Environment variables

| Variable | Default | Owner |
|---|---|---|
| `DATABASE_URL` | (set in compose) | Backend |
| `GRPC_PORT` | `50051` | Backend |
| `DEFAULT_LOAN_DAYS` | `14` | Backend (loan due-date calc) |
| `FINE_GRACE_DAYS` | `14` | Backend (fine policy) |
| `FINE_PER_DAY_CENTS` | `25` | Backend (fine policy) |
| `FINE_CAP_CENTS` | `2000` | Backend (fine policy) |
| `DEMO_MODE` | `false` | Backend (entrypoint behavior) |
| `DB_STATEMENT_TIMEOUT_MS`, `DB_LOCK_TIMEOUT_MS`, ... | (sane defaults) | Backend (resilience tuning) |
| `OTEL_TRACES_EXPORTER`, `OTEL_*` | `console` | Backend (observability) |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8080` | Frontend (Connect-Web target) |

For deeper operational guidance, see [`design/05-infrastructure.md`](design/05-infrastructure.md).

---

## 10. Documentation map

The full documentation tree is rooted at [`docs/README.md`](README.md). Suggested entry points by role:

### A new developer joining the project
1. This document (you're reading it) — get the big picture
2. [`design/03-backend.md`](design/03-backend.md) or [`design/04-frontend.md`](design/04-frontend.md) — depending on which side you'll touch
3. [`test.md`](test.md) — how to run tests during development
4. [`phases/`](phases/) — pick whichever subsystem you'll modify and skim its phase doc to understand how it was built
5. [`reference/decisions.md`](reference/decisions.md) — the "why" register

### A senior architect doing design review
1. This document — components and rationale
2. [`design/01-database.md`](design/01-database.md) — the partial unique index argument and the fine policy
3. [`design/02-api-contract.md`](design/02-api-contract.md) — proto contract and error-status mapping
4. [`reference/decisions.md`](reference/decisions.md) — every non-obvious choice with its tradeoff
5. [`phases/phase-5-borrow-return-fines.md`](phases/phase-5-borrow-return-fines.md) — the most subtle phase (concurrency + fines)

### A senior lead / engineering manager
1. This document, especially §7 (rationale) and §8 (cross-cutting concerns)
2. [`progress-report.md`](progress-report.md) — phase-by-phase status, CTO certifications, deferred items
3. [`test.md`](test.md) — testing posture across unit / integration / e2e
4. [`reference/readme-outline.md`](reference/readme-outline.md) — what the README covers (and why)

### A reviewer (take-home context)
1. The root `README.md` — quick start and feature tour
2. This document — to validate architectural decisions
3. [`docs/00-overview.md`](00-overview.md) — original problem statement, rubric mapping, non-goals

---

## What this document is NOT

It does not duplicate the design docs — it summarizes them and links in. If you want the full DDL, full `.proto`, full Compose YAML, or full Envoy config, follow the links into [`design/`](design/). This document is the **map**; the design docs are the **territory**.
