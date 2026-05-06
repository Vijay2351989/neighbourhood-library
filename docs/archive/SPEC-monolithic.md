# Neighborhood Library — Technical Specification

A take-home build for a small library to manage members, books, and lending operations. This document is the single source of truth for what we are building, why, and the order in which we will build it. Read it top-to-bottom before any code is written.

---

## 1. Overview & Goals

### Problem
A small neighborhood library needs a service to manage its members, its book catalog, and the day-to-day act of lending books out and getting them back. Today nothing exists — staff need a working web application backed by a real service.

### Solution at a glance
A four-tier system:
1. A **Next.js** staff-facing web UI.
2. An **Envoy** proxy that translates browser-friendly gRPC-Web into native gRPC.
3. A **Python gRPC** service implementing the four core operations (book CRUD, member CRUD, borrow, return) plus list/query endpoints.
4. A **PostgreSQL** database with a normalized schema that distinguishes the abstract `Book` (a title) from a concrete `BookCopy` (a physical item on the shelf).

The whole thing comes up with a single `docker compose up`.

### Rubric criteria this design targets
| Rubric item | How we hit it |
|---|---|
| Schema design — normalization, relationships | Four-table normalized schema. `Book`/`BookCopy` split lets us model real-world inventory and keep loan rows pointing at a physical copy. Foreign keys enforced; indexes on lookup columns. |
| Service interface — intuitive, well-structured RPC | One `LibraryService` proto with verb-noun method names following gRPC conventions (`CreateBook`, `BorrowBook`, `ListLoans`). Distinct request/response messages per RPC. Standard gRPC status codes for failure modes. |
| Code quality — organization, readability | Layered Python backend (proto → services → repositories → db). Small focused modules. Type hints everywhere. SQLAlchemy 2.0 typed mappings. |
| Documentation — ease of setup, clear test instructions | Single-command bring-up. README walks through prereqs, compose, .proto regeneration, env vars, sample client, and how to run tests. |

### Explicit non-goals
- **Authentication / authorization.** No login. The app assumes trusted staff use.
- **Fine payments, waivers, partial payments, refunds.** Fines themselves are computed and displayed for overdue loans (see §3.5), but there is no payment ledger and no concept of a fine being "paid." A real library would need a payments table; out of scope here.
- **Per-copy management UI.** Staff manage books at the title level with a "number of copies" input. The backend manages individual copy rows.
- **Multi-tenancy / multi-branch.** One library, one database.
- **Member-facing UI.** Staff-facing only.
- **Real-time notifications, email reminders, etc.**
- **Production hardening.** No TLS termination, no secrets manager, no rate limiting. This is a take-home demo.

### Estimated complexity
**Medium.** No exotic infrastructure, but the gRPC-Web toolchain plus the borrow/return concurrency story plus a non-trivial Next.js UI plus migrations and seed data plus Docker Compose orchestration adds up. The phased plan below is sized accordingly.

---

## 2. Architecture Diagram

```
+-----------------------+        +-----------------+        +-------------------------+        +----------------+
|                       |        |                 |        |                         |        |                |
|  Browser              |        |  Envoy Proxy    |        |  Python gRPC Server     |        |  PostgreSQL    |
|  (Next.js dev server  | -----> |  :8080          | -----> |  :50051                 | -----> |  :5432         |
|   or static export)   |        |                 |        |  (LibraryService impl)  |        |                |
|                       |  HTTP  |                 |  HTTP/2|                         |   TCP  |                |
|  gRPC-Web client      |  +     |  grpc_web filter|  native|  SQLAlchemy 2.0 async   |        |                |
|  (generated TS stubs) |  CORS  |  + CORS         |  gRPC  |  Alembic migrations     |        |                |
|                       |        |                 |        |                         |        |                |
+-----------------------+        +-----------------+        +-------------------------+        +----------------+
                                                                       ^
                                                                       |  (initial bring-up)
                                                                       |
                                                              +--------+--------+
                                                              | Alembic upgrade |
                                                              | + seed script   |
                                                              +-----------------+
```

**Protocol on each hop:**
- Browser → Envoy: **gRPC-Web** over HTTP/1.1 or HTTP/2 (browsers can't speak native gRPC's HTTP/2 framing requirements directly).
- Envoy → Python server: **native gRPC** over HTTP/2.
- Python server → Postgres: standard Postgres wire protocol over TCP, via `asyncpg` driver.

Next.js sits in the browser tier. In dev it runs as `next dev` on its own port and the browser fetches gRPC-Web from Envoy. In a production-style build it could be statically exported and served behind Envoy too, but for the take-home we keep the Next.js dev server distinct.

---

## 3. Database Schema

PostgreSQL 16. All timestamps use `TIMESTAMPTZ` (timezone-aware) — the application stores UTC, the UI renders in the staff member's local timezone.

### 3.1 DDL

```sql
-- Books: the abstract title. One row per ISBN/title-edition.
CREATE TABLE books (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT        NOT NULL,
    author          TEXT        NOT NULL,
    isbn            TEXT        NULL,        -- nullable; some old/local books have no ISBN
    published_year  INTEGER     NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Case-insensitive prefix search on title/author. Trigram extension is overkill
-- for a take-home, so we use plain B-tree on lower(...) for now.
CREATE INDEX books_title_lower_idx  ON books (lower(title));
CREATE INDEX books_author_lower_idx ON books (lower(author));

-- Members: library patrons.
CREATE TABLE members (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    email       TEXT        NOT NULL,
    phone       TEXT        NULL,
    address     TEXT        NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Email is the natural staff-facing identifier; enforce uniqueness.
CREATE UNIQUE INDEX members_email_unique_idx ON members (lower(email));

-- BookCopies: physical instances of a book.
CREATE TYPE copy_status AS ENUM ('AVAILABLE', 'BORROWED', 'LOST');

CREATE TABLE book_copies (
    id          BIGSERIAL PRIMARY KEY,
    book_id     BIGINT      NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    status      copy_status NOT NULL DEFAULT 'AVAILABLE',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX book_copies_book_id_status_idx ON book_copies (book_id, status);

-- Loans: a member borrowing a copy. returned_at NULL means active.
CREATE TABLE loans (
    id          BIGSERIAL PRIMARY KEY,
    copy_id     BIGINT      NOT NULL REFERENCES book_copies(id) ON DELETE RESTRICT,
    member_id   BIGINT      NOT NULL REFERENCES members(id)     ON DELETE RESTRICT,
    borrowed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at      TIMESTAMPTZ NOT NULL,
    returned_at TIMESTAMPTZ NULL
);

-- A given copy can have at most one active (un-returned) loan.
-- This is the structural guarantee against double-borrow.
CREATE UNIQUE INDEX loans_one_active_per_copy_idx
    ON loans (copy_id)
    WHERE returned_at IS NULL;

-- Common query indexes.
CREATE INDEX loans_member_active_idx ON loans (member_id) WHERE returned_at IS NULL;
CREATE INDEX loans_member_id_idx     ON loans (member_id);
CREATE INDEX loans_copy_id_idx       ON loans (copy_id);
```

### 3.2 Why each table looks this way

**`books`** — the abstract title. Two physical copies of *Dune* are one row here. `isbn` is nullable because real-world cataloging is messy: pre-ISBN books, locally produced zines, etc. We do not enforce uniqueness on ISBN: legitimate edge cases (different editions sharing an ISBN, data-entry typos that we correct later) make a unique constraint more painful than helpful for a small library.

**`members`** — straightforward. `email` is unique because staff use it to disambiguate humans with the same name. We compare lowercased to avoid `Bob@x.com` vs `bob@x.com` showing as different members.

**`book_copies`** — every physical book on the shelf is a row. Status tells us at a glance whether the copy is on the shelf, out on loan, or has been lost. The frontend hides this table entirely: when staff create a book they enter "title, author, ISBN, number of copies" and the server inserts the book row plus N copy rows in one transaction. When staff edit the count up or down, the server adds new copies (status `AVAILABLE`) or removes free copies (refusing to remove if the count would drop below the number currently `BORROWED`).

**`loans`** — one row per borrow event. `returned_at IS NULL` is the canonical "this loan is active" predicate. `due_at` is set at borrow time (default policy: 14 days from borrow). Overdue is a computed predicate (`returned_at IS NULL AND due_at < NOW()`), not a stored column.

### 3.3 Concurrency strategy: the partial unique index

We use **the partial unique index** (`loans_one_active_per_copy_idx`) as the primary defense against double-borrow, with `SELECT ... FOR UPDATE` as a secondary correctness aid inside the borrow transaction.

**Why the partial unique index is the cleaner option:**
- It is a database-level invariant. Even a buggy service can't violate it.
- It is declarative — the schema itself documents the rule.
- A unique-violation error is a clean, distinct signal we can map to `ALREADY_EXISTS` / `FAILED_PRECONDITION`.

**The borrow transaction flow:**
```
BEGIN;
  -- 1. Pick an available copy and lock it for the duration of this txn.
  SELECT id FROM book_copies
  WHERE book_id = $1 AND status = 'AVAILABLE'
  ORDER BY id
  LIMIT 1
  FOR UPDATE SKIP LOCKED;
  -- 2. If no row, abort with FAILED_PRECONDITION (no copies available).
  -- 3. Insert into loans (copy_id, member_id, borrowed_at, due_at).
  --    If two transactions race past step 1 somehow, the partial unique index rejects the second.
  -- 4. UPDATE book_copies SET status = 'BORROWED' WHERE id = <picked id>.
COMMIT;
```

`FOR UPDATE SKIP LOCKED` lets concurrent borrows pick *different* copies of the same book without blocking each other — important for popular titles with multiple copies.

### 3.4 Computing `available_copies`

Two viable approaches:

1. **Aggregate query** (recommended): `SELECT COUNT(*) FILTER (WHERE status = 'AVAILABLE') FROM book_copies WHERE book_id = $1`, joined into the book list query.
2. **Materialized view / denormalized counter** on `books` updated via triggers.

We pick **approach 1**. The book list is small (a neighborhood library has hundreds of titles, not millions) and the aggregate is fast with the `book_copies (book_id, status)` index. Denormalization adds invariant-maintenance complexity we don't need at this scale. The `ListBooks` RPC's SQL will look like:

```sql
SELECT b.*,
       COUNT(c.id)                                            AS total_copies,
       COUNT(c.id) FILTER (WHERE c.status = 'AVAILABLE')      AS available_copies
FROM books b
LEFT JOIN book_copies c ON c.book_id = b.id
WHERE (... search predicates ...)
GROUP BY b.id
ORDER BY b.title
LIMIT $page_size OFFSET $offset;
```

### 3.5 Fine policy (computed, not stored)

Fines accrue on overdue loans **after a 14-day grace period** past `due_at`. The schema does not change — fines are computed at query time, the same way `overdue` is.

**Formula** (pure function, easy to unit-test):

```python
def compute_fine_cents(
    due_at: datetime,
    returned_at: datetime | None,
    now: datetime,
    grace_days: int = 14,
    per_day_cents: int = 25,
    cap_cents: int = 2000,
) -> int:
    reference = returned_at if returned_at is not None else now
    days_past_grace = (reference - due_at).days - grace_days
    if days_past_grace <= 0:
        return 0
    return min(cap_cents, days_past_grace * per_day_cents)
```

**Defaults** (env-configurable on the `api` service):

| Env var | Default | Meaning |
|---|---|---|
| `FINE_GRACE_DAYS`     | `14`   | Days past `due_at` before fines start accruing |
| `FINE_PER_DAY_CENTS`  | `25`   | $0.25 per overdue day after grace |
| `FINE_CAP_CENTS`      | `2000` | Maximum fine per loan: $20.00 |

**Behavior summary:**

| Loan state | Fine |
|---|---|
| Active or returned, still within `grace_days` of `due_at` | 0 |
| Active, overdue past grace | accrues per day, capped at `FINE_CAP_CENTS` |
| Returned after grace expired | snapshot as of `returned_at`; remains visible on the loan record forever |
| Returned before grace expired | 0 |

**Aggregating across loans (`Member.outstanding_fines_cents`):**
Sum of `compute_fine_cents` over all of the member's loans. Active overdue loans contribute their currently-accruing value; returned-late loans contribute their snapshot. There is no "paid" state — once a fine exists it remains visible on the loan record. (Real-world: a payment ledger would clear these; out of scope here, see §1 non-goals.)

**Why computed and not stored:**
Storing fines would require a periodic job to update them as days tick over, plus a "today" timezone reference, plus invalidation when a loan is returned. Computing at query time avoids all three classes of bug. The per-row arithmetic cost is negligible at neighborhood-library scale.

**Concurrency note:** because fines are computed, no additional locking is needed for fine display. The borrow/return transactions don't touch any fine state.

---

## 4. Protobuf Service Definition

`proto/library/v1/library.proto`. We version with `v1` in the package path so future breaking changes are clearly delineated.

```protobuf
syntax = "proto3";

package library.v1;

import "google/protobuf/timestamp.proto";
import "google/protobuf/wrappers.proto";

// =====================================================================
// Resource messages
// =====================================================================

message Book {
  int64 id = 1;
  string title = 2;
  string author = 3;
  google.protobuf.StringValue isbn = 4;            // nullable
  google.protobuf.Int32Value published_year = 5;   // nullable
  int32 total_copies = 6;                           // computed
  int32 available_copies = 7;                       // computed
  google.protobuf.Timestamp created_at = 8;
  google.protobuf.Timestamp updated_at = 9;
}

message Member {
  int64 id = 1;
  string name = 2;
  string email = 3;
  google.protobuf.StringValue phone = 4;
  google.protobuf.StringValue address = 5;
  google.protobuf.Timestamp created_at = 6;
  google.protobuf.Timestamp updated_at = 7;
  int64 outstanding_fines_cents = 8;            // computed: sum of compute_fine_cents over all member's loans (see §3.5)
}

message Loan {
  int64 id = 1;
  int64 member_id = 2;
  int64 book_id = 3;
  int64 copy_id = 4;
  string book_title = 5;       // denormalized into responses for UI convenience
  string book_author = 6;
  string member_name = 7;
  google.protobuf.Timestamp borrowed_at = 8;
  google.protobuf.Timestamp due_at = 9;
  google.protobuf.Timestamp returned_at = 10;  // unset = active
  bool overdue = 11;                            // computed: returned_at unset AND due_at < now
  int64 fine_cents = 12;                        // computed per §3.5; 0 when within grace or never overdue
}

// =====================================================================
// Book RPCs
// =====================================================================

message CreateBookRequest {
  string title = 1;
  string author = 2;
  google.protobuf.StringValue isbn = 3;
  google.protobuf.Int32Value published_year = 4;
  int32 number_of_copies = 5;   // must be >= 1
}
message CreateBookResponse { Book book = 1; }

message UpdateBookRequest {
  int64 id = 1;
  string title = 2;
  string author = 3;
  google.protobuf.StringValue isbn = 4;
  google.protobuf.Int32Value published_year = 5;
  google.protobuf.Int32Value number_of_copies = 6;  // optional; if set, server reconciles copy rows
}
message UpdateBookResponse { Book book = 1; }

message GetBookRequest  { int64 id = 1; }
message GetBookResponse { Book book = 1; }

message ListBooksRequest {
  google.protobuf.StringValue search = 1;   // matches title or author, case-insensitive
  int32 page_size = 2;                       // default 25, max 100
  int32 offset = 3;
}
message ListBooksResponse {
  repeated Book books = 1;
  int32 total_count = 2;
}

// =====================================================================
// Member RPCs
// =====================================================================

message CreateMemberRequest {
  string name = 1;
  string email = 2;
  google.protobuf.StringValue phone = 3;
  google.protobuf.StringValue address = 4;
}
message CreateMemberResponse { Member member = 1; }

message UpdateMemberRequest {
  int64 id = 1;
  string name = 2;
  string email = 3;
  google.protobuf.StringValue phone = 4;
  google.protobuf.StringValue address = 5;
}
message UpdateMemberResponse { Member member = 1; }

message GetMemberRequest  { int64 id = 1; }
message GetMemberResponse { Member member = 1; }

message ListMembersRequest {
  google.protobuf.StringValue search = 1;
  int32 page_size = 2;
  int32 offset = 3;
}
message ListMembersResponse {
  repeated Member members = 1;
  int32 total_count = 2;
}

// =====================================================================
// Loan (borrow / return / query) RPCs
// =====================================================================

message BorrowBookRequest {
  int64 book_id = 1;
  int64 member_id = 2;
  google.protobuf.Timestamp due_at = 3;   // optional; server defaults to now+14d
}
message BorrowBookResponse { Loan loan = 1; }

message ReturnBookRequest  { int64 loan_id = 1; }
message ReturnBookResponse { Loan loan = 1; }

enum LoanFilter {
  LOAN_FILTER_UNSPECIFIED = 0;  // both active and returned
  LOAN_FILTER_ACTIVE = 1;       // returned_at IS NULL
  LOAN_FILTER_RETURNED = 2;
  LOAN_FILTER_OVERDUE = 3;      // active AND due_at < now
  LOAN_FILTER_HAS_FINE = 4;     // fine_cents > 0 (active accruing, or returned-late snapshot)
}

message ListLoansRequest {
  google.protobuf.Int64Value member_id = 1;   // optional filter
  google.protobuf.Int64Value book_id = 2;     // optional filter
  LoanFilter filter = 3;
  int32 page_size = 4;
  int32 offset = 5;
}
message ListLoansResponse {
  repeated Loan loans = 1;
  int32 total_count = 2;
}

message GetMemberLoansRequest {
  int64 member_id = 1;
  LoanFilter filter = 2;
}
message GetMemberLoansResponse {
  repeated Loan loans = 1;
}

// =====================================================================
// Service
// =====================================================================

service LibraryService {
  rpc CreateBook   (CreateBookRequest)   returns (CreateBookResponse);
  rpc UpdateBook   (UpdateBookRequest)   returns (UpdateBookResponse);
  rpc GetBook      (GetBookRequest)      returns (GetBookResponse);
  rpc ListBooks    (ListBooksRequest)    returns (ListBooksResponse);

  rpc CreateMember (CreateMemberRequest) returns (CreateMemberResponse);
  rpc UpdateMember (UpdateMemberRequest) returns (UpdateMemberResponse);
  rpc GetMember    (GetMemberRequest)    returns (GetMemberResponse);
  rpc ListMembers  (ListMembersRequest)  returns (ListMembersResponse);

  rpc BorrowBook     (BorrowBookRequest)     returns (BorrowBookResponse);
  rpc ReturnBook     (ReturnBookRequest)     returns (ReturnBookResponse);
  rpc ListLoans      (ListLoansRequest)      returns (ListLoansResponse);
  rpc GetMemberLoans (GetMemberLoansRequest) returns (GetMemberLoansResponse);
}
```

### 4.1 Error semantics

| Failure | gRPC status | Example |
|---|---|---|
| Required field missing or invalid | `INVALID_ARGUMENT` | empty title, negative `number_of_copies`, `page_size > 100` |
| Resource not found | `NOT_FOUND` | `GetBook(id=999)` when no such book |
| Borrow attempted but no AVAILABLE copy | `FAILED_PRECONDITION` | every copy of the book is `BORROWED` |
| Return attempted on already-returned loan | `FAILED_PRECONDITION` | `returned_at IS NOT NULL` |
| Duplicate email on member create | `ALREADY_EXISTS` | enforced via `members_email_unique_idx` |
| Reducing `number_of_copies` below currently-borrowed | `FAILED_PRECONDITION` | with a clear message |
| Catastrophic / unexpected | `INTERNAL` | logged with stack trace |

The Python server uses `grpc.aio.AioRpcError`-compatible exceptions raised from the service layer; the outer servicer wrapper translates any uncaught exception into `INTERNAL` and logs it.

---

## 5. Backend Code Layout

```
backend/
├── pyproject.toml              # uv-managed; deps: grpcio, grpcio-tools, sqlalchemy[asyncio],
│                               #   asyncpg, alembic, pydantic, python-dotenv, pytest,
│                               #   pytest-asyncio, testcontainers[postgresql]
├── uv.lock
├── README.md                   # backend-specific notes (root README is the user-facing one)
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_initial.py     # the schema in section 3
├── proto/
│   └── library/v1/library.proto
├── src/
│   └── library/
│       ├── __init__.py
│       ├── main.py             # entrypoint: build server, attach servicer, serve_forever
│       ├── config.py           # env-driven settings (DB URL, port, default loan length)
│       ├── db/
│       │   ├── __init__.py
│       │   ├── engine.py       # async engine + session factory
│       │   └── models.py       # SQLAlchemy 2.0 typed mappings: Book, Member, BookCopy, Loan
│       ├── repositories/
│       │   ├── books.py        # SQL-touching code; returns ORM models or rows
│       │   ├── members.py
│       │   └── loans.py        # borrow/return transactional logic lives here
│       ├── services/
│       │   ├── book_service.py # protobuf <-> domain translation; calls repositories
│       │   ├── member_service.py
│       │   └── loan_service.py
│       ├── servicer.py         # the LibraryServiceServicer class — thin glue, error mapping
│       ├── errors.py           # domain exceptions + grpc status mapping
│       └── generated/          # protoc output — gitignored, regenerated on build
│           └── library/v1/
│               ├── library_pb2.py
│               └── library_pb2_grpc.py
├── scripts/
│   ├── gen_proto.sh            # runs python -m grpc_tools.protoc against proto/
│   ├── seed.py                 # populates sample books, members, loans via the gRPC API
│   └── sample_client.py        # rubric-tip "sample client": full borrow→return demo
└── tests/
    ├── conftest.py             # testcontainer Postgres fixture; alembic upgrade per session
    ├── unit/
    │   └── test_loan_logic.py  # state transitions in isolation
    └── integration/
        ├── test_books.py       # CRUD via real grpc client against in-process server
        ├── test_members.py
        ├── test_borrow_return.py
        └── test_concurrency.py # parallel borrow attempts on a single-copy book
```

### Module responsibilities (one line each)
- **`config.py`** — read `DATABASE_URL`, `GRPC_PORT`, `DEFAULT_LOAN_DAYS`, `FINE_GRACE_DAYS`, `FINE_PER_DAY_CENTS`, `FINE_CAP_CENTS` from env via Pydantic settings.
- **`db/models.py`** — SQLAlchemy 2.0 `Mapped[...]` typed model classes mirroring section 3.
- **`repositories/*`** — every line of SQL lives here. No protobuf imports allowed.
- **`services/*`** — orchestrate repositories, do protobuf↔domain conversion, raise typed domain errors.
- **`servicer.py`** — implements the generated `LibraryServiceServicer`, catches domain errors, maps them to gRPC status, returns response messages. No business logic.
- **`errors.py`** — `class NotFound`, `class AlreadyExists`, `class FailedPrecondition`, `class InvalidArgument` plus a decorator that the servicer uses to translate them.
- **`scripts/sample_client.py`** — a standalone Python file using the generated client stubs to do: create member, create book, borrow, list loans, return, list loans again. Demonstrates the API for reviewers.

### Generated protobuf code: not committed
- `backend/src/library/generated/` is in `.gitignore`.
- `scripts/gen_proto.sh` runs at container build time (and locally via `uv run gen-proto`).
- The same `.proto` is used by the frontend codegen, so it lives at the **repo root** as `proto/`, not inside `backend/`. Both backend and frontend reference it via relative path. (Updating the layout above to reflect this: `backend/proto/` is a symlink or the copy is done at build time.) **Decision: the `.proto` file lives at repo-root `proto/library/v1/library.proto`** and both backend and frontend consume it from there.

---

## 6. Frontend Code Layout

```
frontend/
├── package.json                # next, react, typescript, tailwind, @tanstack/react-query,
│                               # @bufbuild/protobuf, @connectrpc/connect, @connectrpc/connect-web
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── buf.gen.yaml                # codegen config for ts protobuf stubs
├── README.md
├── src/
│   ├── app/                    # Next.js App Router
│   │   ├── layout.tsx          # global shell: top nav, QueryClientProvider
│   │   ├── page.tsx            # dashboard: counts (total books, members, active loans, overdue)
│   │   ├── books/
│   │   │   ├── page.tsx        # ListBooks with search box + pagination + "New book" button
│   │   │   ├── new/page.tsx    # create form
│   │   │   └── [id]/
│   │   │       ├── page.tsx    # book detail (copies count, status)
│   │   │       └── edit/page.tsx
│   │   ├── members/
│   │   │   ├── page.tsx        # ListMembers
│   │   │   ├── new/page.tsx
│   │   │   └── [id]/
│   │   │       ├── page.tsx    # member detail + loan history (active + returned)
│   │   │       └── edit/page.tsx
│   │   └── loans/
│   │       ├── page.tsx        # all loans, filter by Active/Returned/Overdue
│   │       └── new/page.tsx    # the borrow flow: pick member → pick book → confirm
│   ├── components/
│   │   ├── ui/                 # buttons, inputs, table, pagination, toast
│   │   ├── BookForm.tsx
│   │   ├── MemberForm.tsx
│   │   ├── BorrowDialog.tsx
│   │   └── ReturnButton.tsx
│   ├── lib/
│   │   ├── client.ts           # createPromiseClient(LibraryService, createGrpcWebTransport({baseUrl: ENVOY_URL}))
│   │   ├── queryKeys.ts        # central TanStack Query key factory
│   │   └── format.ts           # date/timestamp formatters
│   └── generated/              # Connect-generated TS — gitignored, regenerated on build
│       └── library/v1/
│           ├── library_pb.ts
│           └── library_connect.ts
└── public/
```

### gRPC-Web client choice
The decision is between `protoc-gen-grpc-web` (Google's older codegen) and `@bufbuild/protobuf` + `@connectrpc/connect-web` (the newer Buf/Connect ecosystem). **We pick Connect.** It's actively maintained, has better TypeScript types, the `buf` CLI is a one-stop codegen tool, and `connect-web` speaks the gRPC-Web protocol that Envoy serves. The older `protoc-gen-grpc-web` works but its tooling has stagnated.

### Data fetching pattern
- Every list page wraps a single `useQuery` keyed by request params (search, page, filter).
- Mutations (`CreateBook`, `BorrowBook`, etc.) use `useMutation` with `onSuccess` invalidating the relevant query keys.
- A central `lib/queryKeys.ts` exports factories like `bookKeys.list({search, offset})` so invalidation is type-safe.
- Loading states render skeleton rows; error states render an inline alert with the gRPC status code mapped to a friendly message.

### Page responsibilities (one line each)
- **`/`** — at-a-glance count tiles (total books, members, active loans, overdue, **total outstanding fines**) plus a "Recent activity" feed of the last 10 loans.
- **`/books`** — paginated, searchable table; row click → detail; "New book" button → form.
- **`/books/[id]`** — title, author, ISBN, year, total/available copies, "Edit" link.
- **`/members`** — paginated, searchable table; row click → member detail.
- **`/members/[id]`** — member info, **outstanding-fines tile** (renders only when `outstanding_fines_cents > 0`), tabbed loan history (Active / Returned / All) with "Return" buttons on active rows. Each loan row in the table shows a fine column when applicable.
- **`/loans`** — global loan list with filter chips (Active / Overdue / **Has Fine** / Returned). Fine column rendered with currency formatting; rows where `fine_cents > 0` are visually highlighted.
- **`/loans/new`** — the borrow flow: search-and-pick a member, search-and-pick a book with available copies, optional due date override, submit.

---

## 7. Envoy Configuration

Envoy translates gRPC-Web (browser) into native gRPC (server). Without it the browser can't call our Python server. Envoy also handles CORS for the dev environment where Next.js runs on a different port from Envoy.

The config registers the `envoy.filters.http.grpc_web` filter and the `envoy.filters.http.cors` filter, then routes all paths to a single upstream cluster pointing at the Python server's gRPC port.

```yaml
# deploy/envoy/envoy.yaml
admin:
  address:
    socket_address: { address: 0.0.0.0, port_value: 9901 }

static_resources:
  listeners:
    - name: listener_0
      address:
        socket_address: { address: 0.0.0.0, port_value: 8080 }
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: ingress_http
                codec_type: AUTO
                route_config:
                  name: local_route
                  virtual_hosts:
                    - name: library_service
                      domains: ["*"]
                      cors:
                        allow_origin_string_match:
                          - prefix: "*"
                        allow_methods: GET, PUT, DELETE, POST, OPTIONS
                        allow_headers: keep-alive,user-agent,cache-control,content-type,content-transfer-encoding,x-accept-content-transfer-encoding,x-accept-response-streaming,x-user-agent,x-grpc-web,grpc-timeout,connect-protocol-version,connect-timeout-ms
                        max_age: "1728000"
                        expose_headers: grpc-status,grpc-message
                      routes:
                        - match: { prefix: "/" }
                          route:
                            cluster: library_grpc
                            timeout: 0s
                http_filters:
                  - name: envoy.filters.http.grpc_web
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.grpc_web.v3.GrpcWeb
                  - name: envoy.filters.http.cors
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.cors.v3.Cors
                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  clusters:
    - name: library_grpc
      type: STRICT_DNS
      connect_timeout: 5s
      http2_protocol_options: {}
      load_assignment:
        cluster_name: library_grpc
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: { address: api, port_value: 50051 }
```

In Compose, `address: api` resolves to the Python server container by service name.

---

## 8. Docker Compose Topology

`docker-compose.yml` at repo root:

| Service | Image / build | Port (host:container) | Depends on | Notes |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | `5432:5432` | — | Healthcheck: `pg_isready`. Volume: `pgdata:/var/lib/postgresql/data`. Env: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB=library`. |
| `api` | `./backend` (Dockerfile) | `50051:50051` | postgres (healthy) | Entrypoint: `alembic upgrade head && python -m library.main`. Env: `DATABASE_URL`, `GRPC_PORT=50051`, `DEFAULT_LOAN_DAYS=14`, `FINE_GRACE_DAYS=14`, `FINE_PER_DAY_CENTS=25`, `FINE_CAP_CENTS=2000`. |
| `envoy` | `envoyproxy/envoy:v1.31-latest` | `8080:8080`, `9901:9901` | api | Mounts `deploy/envoy/envoy.yaml:/etc/envoy/envoy.yaml`. |
| `web` | `./frontend` (Dockerfile) | `3000:3000` | envoy | Env: `NEXT_PUBLIC_API_BASE_URL=http://localhost:8080`. Runs `next dev` in dev profile, `next start` in prod profile. |
| `seed` | `./backend` (same image as `api`) | — | api (healthy) | Optional one-shot service in a `seed` profile: runs `python scripts/seed.py` then exits. Activate with `docker compose --profile seed up`. |

### Migrations on startup
The `api` container's entrypoint script:

```
#!/bin/sh
set -e
alembic upgrade head
exec python -m library.main
```

Migrations are idempotent — re-running on every container start is safe and removes a class of "did you remember to migrate" errors.

### Healthchecks
- `postgres`: `pg_isready -U $POSTGRES_USER`
- `api`: a tiny gRPC health-check probe (we'll use `grpc_health_probe` binary baked into the image, hitting the standard `grpc.health.v1.Health/Check`).
- `envoy`: HTTP GET to `:9901/ready`.

`web` doesn't expose a healthcheck; if Next.js fails to start, that's visible in logs and the user can't load the page.

### Seed data
`scripts/seed.py` uses the gRPC client to (1) create ~20 books with varying copy counts, (2) create ~10 members, (3) borrow a handful of books to give the loans table some content. Running through the public API rather than direct SQL has two benefits: it's a free smoke test of the whole stack, and the seed script doubles as an executable example of how to use the API.

---

## 9. Phased Implementation Plan

Seven sequential phases. Each phase ends with something runnable so we can demo / validate before moving on. **Do not start phase N+1 until phase N's acceptance criteria are met.**

---

### Phase 1 — Repo & Infra Scaffolding

**Goal:** Have a repo skeleton where `docker compose up` brings up *something* (even if it's just Postgres + a hello-world API) and developer tooling is wired up.

**Scope**
- Repo layout: `backend/`, `frontend/`, `proto/`, `deploy/envoy/`, `docs/`, `docker-compose.yml`, `.gitignore`, root `README.md` (skeleton).
- Backend: `pyproject.toml` with `uv`, a stub `library.main` that starts a gRPC server on 50051 with no services registered, Dockerfile.
- Frontend: `npx create-next-app@latest` baseline with Tailwind, TypeScript, App Router. Dockerfile.
- Envoy: `envoy.yaml` from section 7.
- Compose: all four services with healthchecks. Postgres uses the `library` DB.
- `.gitignore` covers `generated/`, `node_modules/`, `__pycache__/`, `.venv/`, etc.

**Deliverables**
- Working `docker-compose.yml`.
- Two Dockerfiles.
- Bare-bones `envoy.yaml`.
- Stub Python `main.py` that logs "listening on :50051".
- Stub Next.js home page that says "Neighborhood Library".
- Root `README.md` with a 3-line "how to run" placeholder.

**Acceptance criteria**
- `docker compose up` exits non-error and stays running.
- `curl http://localhost:3000` returns the Next.js stub page.
- `curl http://localhost:8080` returns an Envoy 404 (proves Envoy is reachable).
- `psql -h localhost -U postgres library -c '\dt'` returns "no relations" without error.

**Dependencies:** none.

**Effort:** **M** (~4 hrs).

---

### Phase 2 — Schema & Migrations

**Goal:** The database schema from section 3 exists and is reproducible from a single command.

**Scope**
- Alembic init under `backend/alembic/`.
- Single migration `0001_initial.py` creating all four tables, the enum, indexes, and the partial unique index.
- SQLAlchemy 2.0 typed models in `db/models.py` matching the schema exactly.
- Container entrypoint runs `alembic upgrade head` before launching the server.

**Deliverables**
- `backend/alembic/versions/0001_initial.py`.
- `backend/src/library/db/models.py`.
- `backend/src/library/db/engine.py` (async engine, session factory).
- Updated `api` Dockerfile entrypoint script.

**Acceptance criteria**
- After `docker compose up`, running `psql -h localhost -U postgres library -c '\dt'` shows `books`, `members`, `book_copies`, `loans`, `alembic_version`.
- `\d loans` shows the partial unique index on `(copy_id) WHERE returned_at IS NULL`.
- Stopping compose, deleting the `pgdata` volume, restarting → schema is recreated identically.
- Running `alembic upgrade head` a second time is a no-op (no errors, no duplicate creates).

**Dependencies:** Phase 1.

**Effort:** **S** (~2 hrs).

---

### Phase 3 — Protobuf Contract & Codegen

**Goal:** The `.proto` is finalized and both backend and frontend can generate working stubs from it.

**Scope**
- `proto/library/v1/library.proto` with the full content from section 4.
- `backend/scripts/gen_proto.sh` invoking `python -m grpc_tools.protoc`.
- `frontend/buf.gen.yaml` configuring `@bufbuild/protoc-gen-es` and `@connectrpc/protoc-gen-connect-es`.
- Both codegen steps wired into the respective Docker builds.
- A trivial smoke check: backend imports `library.v1.library_pb2`; frontend imports the generated TS module without error.

**Deliverables**
- `proto/library/v1/library.proto`.
- `backend/scripts/gen_proto.sh`.
- `frontend/buf.gen.yaml`.
- Updated Dockerfiles to run codegen during build.

**Acceptance criteria**
- `bash backend/scripts/gen_proto.sh` produces non-empty `_pb2.py` and `_pb2_grpc.py` files.
- `cd frontend && npx buf generate` produces non-empty `library_pb.ts` and `library_connect.ts`.
- Backend container build succeeds and the import works inside the container.
- Frontend container build succeeds.

**Dependencies:** Phase 1.

**Effort:** **M** (~3 hrs — buf tooling has gotchas).

---

### Phase 4 — Backend CRUD: Books & Members

**Goal:** The eight book/member RPCs work end-to-end against a real Postgres, reachable via gRPC and via gRPC-Web through Envoy.

**Scope**
- `repositories/books.py`, `repositories/members.py` — async SQLAlchemy code for create/update/get/list with search and pagination.
- `services/book_service.py`, `services/member_service.py` — protobuf↔domain conversion, validation, error raising.
- `errors.py` — `NotFound`, `AlreadyExists`, `InvalidArgument` exceptions.
- `servicer.py` — implements the eight book/member methods on `LibraryServiceServicer`. Decorated to translate domain errors → gRPC status codes.
- Validation: empty title/author rejected, `page_size > 100` clamped, duplicate email → `ALREADY_EXISTS`, etc.
- For `CreateBook` with `number_of_copies = N`, the service creates the book + N `book_copies` rows in one transaction.
- For `UpdateBook` with a new `number_of_copies`, the service reconciles: add new `AVAILABLE` rows or remove existing `AVAILABLE` rows (refusing if the count would drop below currently-`BORROWED`).

**Deliverables**
- All files listed above.
- `tests/integration/test_books.py` — covers create, get, list with search, list with pagination, update (including copy reconciliation), invalid-argument cases, not-found cases.
- `tests/integration/test_members.py` — analogous, plus duplicate-email case.
- Tests use `testcontainers-postgres` and a real grpc client against an in-process server.

**Acceptance criteria**
- `pytest backend/tests/integration/test_books.py backend/tests/integration/test_members.py` is green.
- From the host: `grpcurl -plaintext localhost:50051 library.v1.LibraryService/ListBooks` returns the expected proto JSON.
- From a browser console at `http://localhost:3000`: a fetch through the generated Connect client to `ListBooks` succeeds (proves Envoy + CORS + codegen).

**Dependencies:** Phase 2, Phase 3.

**Effort:** **L** (~10 hrs).

---

### Phase 5 — Borrow & Return with Concurrency

**Goal:** `BorrowBook`, `ReturnBook`, `ListLoans`, `GetMemberLoans` work correctly, including under concurrent borrow attempts on a single-copy book.

**Scope**
- `repositories/loans.py` — borrow transaction (the `FOR UPDATE SKIP LOCKED` flow from section 3.3); return transaction (set `returned_at`, flip copy status to `AVAILABLE`); list/filter queries with the `LoanFilter` enum semantics.
- `services/loan_service.py` — protobuf wiring + error translation. Default `due_at = now + DEFAULT_LOAN_DAYS`. `overdue` and `fine_cents` are computed at response-build time using the formula in §3.5.
- `services/fines.py` — pure-function `compute_fine_cents(due_at, returned_at, now, grace_days, per_day_cents, cap_cents) -> int`. No I/O, no proto imports — purely arithmetic. Used by `loan_service` when building Loan/Member responses.
- `servicer.py` — register the four loan methods.
- Default loan length is read from `DEFAULT_LOAN_DAYS` env var (default 14).
- The `Loan` response message is enriched with `book_title`, `book_author`, `member_name` via SQL joins so the UI doesn't need extra round-trips.

**Deliverables**
- `repositories/loans.py`, `services/loan_service.py`.
- Servicer additions.
- `tests/integration/test_borrow_return.py` — happy path, double-borrow rejection, return flow, return-already-returned rejection, overdue flag computation, **`fine_cents` computation across the grace boundary, capped fine at `FINE_CAP_CENTS`, returned-late snapshot fine, member `outstanding_fines_cents` aggregation across multiple loans**, list with each filter value (including `LOAN_FILTER_HAS_FINE`), member-scoped query.
- `tests/integration/test_concurrency.py` — spawn N=10 concurrent `BorrowBook` tasks against a 1-copy book; assert exactly 1 succeeds and 9 get `FAILED_PRECONDITION`.
- `tests/unit/test_loan_logic.py` — pure-function tests for the overdue predicate, **`compute_fine_cents` across all the table-of-behavior cases (within grace, exactly at grace boundary, mid-fine, at cap, beyond cap, returned within grace, returned past grace)**, and any state-transition helpers.

**Acceptance criteria**
- All loan tests green.
- Concurrency test green: exactly one borrow wins, others fail cleanly with `FAILED_PRECONDITION`, no partial state in DB (verified by checking `loans` row count and `book_copies.status`).
- `grpcurl` smoke: borrow → list active → return → list active again, observed counts make sense.

**Dependencies:** Phase 4.

**Effort:** **L** (~10 hrs).

---

### Phase 6 — Frontend MVP

**Goal:** Staff can perform every operation through the web UI: create/update books and members, borrow, return, list, search, paginate.

**Scope**
- `lib/client.ts` — `createPromiseClient(LibraryService, createGrpcWebTransport({baseUrl: NEXT_PUBLIC_API_BASE_URL}))`.
- `lib/queryKeys.ts` — typed key factory.
- Layout shell with top nav: Dashboard, Books, Members, Loans.
- Books: list (search, paginate, "New book" button), create form, edit form, detail page.
- Members: list (search, paginate), create form, edit form, detail page (with tabbed loan history).
- Loans: global list (filter chips), borrow form (`/loans/new`) with member-picker and book-picker, "Return" button on active loans.
- Dashboard: five tiles (total books, total members, active loans, overdue, total outstanding fines formatted as currency).
- Error UX: any non-`OK` gRPC status renders a toast with the friendly message; `INVALID_ARGUMENT` highlights the offending form field where possible.
- Loading skeletons on all list pages.

**Deliverables**
- All pages and components listed in section 6.
- A consistent UI kit in `components/ui/` (button, input, table, pagination, toast, dialog).

**Acceptance criteria**
- Manual run-through (a "demo script"):
  1. Open `http://localhost:3000`. Dashboard loads.
  2. Books → New → create "Dune" with 2 copies. Appears in the list.
  3. Members → New → create "Alice". Appears in the list.
  4. Loans → New → pick Alice → pick Dune → submit. Loan appears in active list.
  5. Try to borrow Dune again for the same member — it succeeds (2 copies). Try a third time — `FAILED_PRECONDITION` toast.
  6. Members → Alice → tab Active → click Return on first loan. Disappears from active, appears in Returned tab.
  7. Books → Dune detail → available count is 1.
  8. Search "dun" in books list → finds it. Pagination works on a list of 30+ books (use seed data).
  9. Open the member with a fined loan (from seed): outstanding-fines tile renders with the right amount; the loan row shows a fine column; `/loans` with the "Has Fine" filter lists exactly that loan.

**Dependencies:** Phase 4, Phase 5.

**Effort:** **L** (~14 hrs).

---

### Phase 7 — Polish: Seed, Sample Client, README, Optional Test

**Goal:** The deliverable is reviewer-ready: zero-friction setup, sample client demonstrating the API, comprehensive README, and (time permitting) one e2e test.

**Scope**
- `backend/scripts/seed.py` — populates ~20 books, ~10 members, ~5 active loans, ~3 returned loans, ~1 overdue loan still within grace (no fine yet), **~1 overdue loan past grace (currently accruing fine), ~1 returned-late loan (snapshot fine)**. Uses the gRPC API (not direct SQL). To produce historic dates, the seed script may write directly to the DB for `borrowed_at` / `due_at` overrides — document this caveat clearly.
- `backend/scripts/sample_client.py` — standalone script: connects, creates a member + book, borrows, lists, returns, lists again. Heavily commented as it doubles as API documentation.
- `seed` Compose service profile that runs `seed.py` once.
- Root `README.md` filled out per section 12.
- `frontend/README.md` and `backend/README.md` — short, link to root.
- *(Optional, time permitting)*: one Playwright test that drives the demo script in Phase 6's acceptance criteria. Skip if running long — flag in README.

**Deliverables**
- `seed.py`, `sample_client.py`.
- Updated Compose with `seed` profile.
- Final root `README.md`.
- Optional: `frontend/e2e/happy-path.spec.ts`.

**Acceptance criteria**
- A reviewer who has never seen the repo can: clone → `docker compose up` → `docker compose --profile seed up seed` → open `http://localhost:3000` and see populated data — by following only the README.
- `python backend/scripts/sample_client.py` (against a running stack) prints a clean before/after of a full borrow/return cycle.
- Root README has a "How to test" section explaining `pytest`.

**Dependencies:** Phase 6.

**Effort:** **M** (~6 hrs).

---

### Phase summary table

| # | Phase | Effort |
|---|---|---|
| 1 | Repo & Infra Scaffolding | M |
| 2 | Schema & Migrations | S |
| 3 | Protobuf Contract & Codegen | M |
| 4 | Backend CRUD: Books & Members | L |
| 5 | Borrow & Return with Concurrency | L |
| 6 | Frontend MVP | L |
| 7 | Polish: Seed, Sample Client, README | M |

Total estimated effort: roughly 50 hours of focused work — appropriate for a take-home claiming "a few days" of effort.

---

## 10. Testing Strategy

Tests are layered to match where bugs actually appear.

### Unit tests
**Where:** `backend/tests/unit/`.
**What:** Pure-function logic that can be tested without a database — overdue predicate, request validation helpers, protobuf↔domain conversions. Small, fast, no I/O.
**What we deliberately don't unit-test:** repositories or services in isolation. Mocking SQLAlchemy is more bug-prone than running against a real Postgres, and we have testcontainers for that.

### Integration tests (the bulk of the suite)
**Where:** `backend/tests/integration/`.
**Setup:** A `pytest` session-scoped fixture spins up a Postgres testcontainer, runs `alembic upgrade head`, and starts an in-process gRPC server bound to a random port. Each test gets a fresh transaction that rolls back at teardown (or, where transactional rollback is incompatible with the test, a per-test `TRUNCATE` of the four tables).
**What's covered:**
- All CRUD happy paths for books and members.
- Validation: empty fields, oversized page sizes, negative copy counts.
- `NOT_FOUND` for missing IDs.
- `ALREADY_EXISTS` for duplicate member email.
- Borrow happy path; borrow when no copies available → `FAILED_PRECONDITION`.
- Return happy path; return-already-returned → `FAILED_PRECONDITION`.
- Loan listing with each `LoanFilter` value.
- Overdue computation (set `due_at` in the past, assert `overdue=true` in the response).
- Copy reconciliation on `UpdateBook` (count up, count down, count down below borrowed → rejection).
- **Concurrency:** N concurrent borrow tasks against a single-copy book; assert exactly one succeeds, the rest get `FAILED_PRECONDITION`, and final DB state is consistent.

### Frontend tests
Out of scope for the take-home. We focus our test budget on the backend, where correctness matters most. **One optional Playwright test** in Phase 7 walking the happy path (create book, create member, borrow, return) gives us a smoke-level guarantee that the wiring works without committing to a full UI test suite.

### Sample client script
`scripts/sample_client.py` is not a test per se but functions as one: every reviewer run is a free smoke test of the entire stack. It also satisfies the rubric's "sample client script" tip.

### Test execution
All tests run inside the project — no external services needed beyond Docker for testcontainers. CI is out of scope but the structure is CI-ready.

---

## 11. Open Questions / Risks

| # | Topic | Decision / Mitigation |
|---|---|---|
| 1 | **ISBN uniqueness.** Real-world ISBNs aren't perfectly unique (different editions sometimes share, data-entry errors are common). | Make `isbn` nullable, **not unique**. Document this in the README. |
| 2 | **Member email uniqueness.** Staff need a stable identifier to disambiguate humans. | Enforce uniqueness on lower(email). Map duplicate inserts to `ALREADY_EXISTS`. |
| 3 | **Deleting a member with active loans.** Hard delete would orphan loans; cascading delete would erase loan history. | Soft-delete is overkill for a take-home. **Block hard delete** if any loans (active or historical) reference the member; return `FAILED_PRECONDITION` with a clear message. (Alternative: don't expose Delete at all in the UI for v1 — recommended.) |
| 4 | **Reducing copy count below borrowed count.** | Reject with `FAILED_PRECONDITION` and message: "Cannot reduce copies below the number currently borrowed (X)." |
| 5 | **gRPC-Web tooling churn.** `protoc-gen-grpc-web` (Google) vs Connect (Buf). | We pick Connect (`@connectrpc/connect-web` + `@bufbuild/protoc-gen-es`). Risk: Connect's gRPC-Web mode has some edge cases with streaming, but we use no streaming RPCs, so we're fine. Document the choice in the README. |
| 6 | **Time zones.** Browser sees the staff member's local TZ; server stores UTC. | Use `TIMESTAMPTZ` everywhere, `google.protobuf.Timestamp` on the wire. Frontend formatters use `Intl.DateTimeFormat` with the user's locale. |
| 7 | **Default loan length.** Not specified by the assignment. | Default to **14 days**, configurable via `DEFAULT_LOAN_DAYS` env var. `BorrowBookRequest.due_at` allows override. |
| 8 | **Pagination style.** Offset vs cursor. | Offset. Lists are small, requirements are simple. Cursor pagination is over-engineering at this scale. |
| 9 | **What if Envoy can't reach the API on cold start?** | Compose `depends_on` with `condition: service_healthy` on the api service ensures Envoy starts after the api is up. |
| 10 | **Should the proto file live at repo root or inside backend/?** | **Repo root `proto/`.** Both backend and frontend codegen consume it from there; symlinking or copying inside containers is fine. |
| 11 | **Fine policy.** Late returns need a penalty model: grace, accrual rate, cap. | **14-day grace after `due_at`, then $0.25/day, capped at $20.** All three values are env-configurable (`FINE_GRACE_DAYS`, `FINE_PER_DAY_CENTS`, `FINE_CAP_CENTS`). Fines are computed at query time (§3.5), never stored. There is no payment ledger — once a fine exists, it remains visible on the loan record forever. |

---

## 12. README Outline

The root `README.md` is the rubric's documentation deliverable. It must cover:

- **What this is.** One paragraph: a take-home build of a small library management service. gRPC-Web + Python + Postgres + Next.js.
- **Architecture overview.** A trimmed version of the section 2 diagram and a 2-paragraph explanation.
- **Prerequisites.** Docker Desktop, that's it. (Optionally: Python 3.12, Node 20, `uv`, `buf` for local dev outside Docker.)
- **Quick start.**
  1. `git clone ...`
  2. `docker compose up` — wait for "api: listening on :50051".
  3. (Optional) `docker compose --profile seed up seed` to populate sample data.
  4. Open `http://localhost:3000`.
- **What you can do.** A short tour of the UI mapped to the four assignment requirements (book CRUD, member CRUD, borrow, return, list).
- **Database setup.** How to point at an external Postgres if not using Compose; how migrations work; how to reset the DB (`docker compose down -v`).
- **.proto compilation.** How to regenerate stubs after editing `proto/library/v1/library.proto` — one command for each side (`backend/scripts/gen_proto.sh` and `cd frontend && npx buf generate`).
- **Running the server outside Docker.** Optional dev workflow: `cd backend && uv sync && uv run alembic upgrade head && uv run python -m library.main`.
- **Environment variables.** Table: `DATABASE_URL`, `GRPC_PORT`, `DEFAULT_LOAN_DAYS`, `FINE_GRACE_DAYS`, `FINE_PER_DAY_CENTS`, `FINE_CAP_CENTS`, `NEXT_PUBLIC_API_BASE_URL`.
- **Sample client script.** `python backend/scripts/sample_client.py` — what it does and what to expect in the output.
- **How to test.** `cd backend && uv run pytest`. Note that testcontainers needs Docker running. Mention the optional Playwright test if it ships.
- **Troubleshooting.** Three or four common gotchas: port conflicts, Docker memory, regenerating stubs after a `.proto` change, browser CORS errors.
- **Project layout.** A tree of the top two levels of directories with one-line descriptions.
- **Design decisions.** Link to this `docs/SPEC.md` for anyone who wants the full reasoning.

---

*End of specification. Implementation begins at Phase 1 only after this document has been reviewed and approved.*
