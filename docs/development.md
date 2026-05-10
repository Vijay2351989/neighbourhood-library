# Development Guide

A working developer's guide to the codebase. Read this when you're about to make a change and want to know *where* code lives, *how* the layers fit, and *what conventions* to follow.

> **Already read:** [`architecture.md`](architecture.md) (high-level components) and [`setup.md`](setup.md) (got the app running). This doc picks up from there.

---

## Table of contents

1. [Codebase orientation](#1-codebase-orientation)
2. [Backend — deep dive](#2-backend--deep-dive)
   - [2.1 Layering rules](#21-layering-rules)
   - [2.2 Database access — engine, session, models](#22-database-access--engine-session-models)
   - [2.3 Migrations workflow](#23-migrations-workflow)
   - [2.4 Resilience layer — retry + timeout nuances](#24-resilience-layer--retry--timeout-nuances)
   - [2.5 Adding a new RPC end-to-end](#25-adding-a-new-rpc-end-to-end)
3. [Frontend — deep dive](#3-frontend--deep-dive)
   - [3.1 App Router structure](#31-app-router-structure)
   - [3.2 Calling an RPC from a page](#32-calling-an-rpc-from-a-page)
   - [3.3 Error UX pattern](#33-error-ux-pattern)
   - [3.4 Adding a new page](#34-adding-a-new-page)
4. [gRPC code generation](#4-grpc-code-generation)
   - [4.1 The two codegens and what each produces](#41-the-two-codegens-and-what-each-produces)
   - [4.2 When and how to regenerate](#42-when-and-how-to-regenerate)
   - [4.3 Notable codegen details](#43-notable-codegen-details)
5. [Development-time configuration](#5-development-time-configuration)
6. [Testing while developing](#6-testing-while-developing)
7. [Common dev recipes](#7-common-dev-recipes)
8. [Style and conventions](#8-style-and-conventions)

---

## 1. Codebase orientation

Top-level structure with one-line descriptions:

```
neighborhood-library/
├── proto/library/v1/                 ← single source of truth for the API
│   ├── book.proto                    ←   BookService (book CRUD)
│   ├── member.proto                  ←   MemberService (member CRUD)
│   └── loan.proto                    ←   LoanService (borrow / return / list)
├── backend/                          ← Python gRPC service
├── frontend/                         ← Next.js + React UI
├── deploy/envoy/envoy.yaml           ← Envoy config (gRPC-Web bridge)
├── docker-compose.yml                ← four-service stack
├── docker-compose.test.yml           ← override for the isolated test stack
├── test.sh                           ← parameterized test runner (./test.sh --help)
└── docs/                             ← all documentation
```

**Layering rule of thumb (backend):** `servicer` knows protobuf, `services` knows protobuf+domain+errors, `repositories` knows SQL, `db/models` knows ORM. Each layer never reaches into the layer two below it.

**Layering rule of thumb (frontend):** Pages call the matching client (`bookClient` / `memberClient` / `loanClient`) from `@/lib/client`, wrapped in TanStack Query (`useQuery` / `useMutation`). Components are decorative; data lives in pages and hooks.

For a more granular file-tree view see [`design/03-backend.md`](design/03-backend.md) §1 and [`design/04-frontend.md`](design/04-frontend.md) §1.

---

## 2. Backend — deep dive

### 2.1 Layering rules

Four physical layers, ordered from "outermost" (proto-aware) to "innermost" (SQL-aware):

```
┌─────────────────────────────────────────────────────────────┐
│  servicer.py                                                 │
│  - Implements 3 generated base classes:                      │
│      BookServiceServicer / MemberServiceServicer /           │
│      LoanServiceServicer  (one Python class each)            │
│  - Translates: proto request → service call → proto response │
│  - Catches DomainError → maps to grpc.StatusCode             │
│  - NO business logic here                                    │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  services/*.py  (book_service, member_service, loan_service)│
│  - Validates input                                           │
│  - Orchestrates one or more repository calls                 │
│  - Constructs proto responses                                │
│  - Computes derived fields (overdue, fine_cents)             │
│  - Raises typed DomainError subclasses                       │
│  - May read settings (env vars) for policy values            │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  repositories/*.py  (books, members, loans)                  │
│  - The ONLY layer that writes SQL (or SQLAlchemy queries)    │
│  - Returns ORM model instances or plain rows                 │
│  - Owns transaction shape (FOR UPDATE, ON CONFLICT, etc.)    │
│  - NO protobuf imports allowed                               │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  db/models.py + db/engine.py                                 │
│  - SQLAlchemy 2.0 typed Mapped[...] models                   │
│  - Engine + sessionmaker + lifecycle helpers                 │
│  - get_session() async generator                             │
└─────────────────────────────────────────────────────────────┘
```

**Cross-cutting:** `errors.py` (typed DomainError hierarchy), `config.py` (Pydantic Settings), `resilience/` (retry decorator + classifier), `observability/` (OTel + interceptors). These are imported across layers but don't introduce new layers.

### 2.2 Database access — engine, session, models

#### The engine (`backend/src/library/db/engine.py`)

One process-wide `AsyncEngine`, lazily constructed on first call to `get_engine()`. Don't construct it directly in your code — always go through `get_engine()` or the `AsyncSessionLocal` factory:

```python
from library.db.engine import AsyncSessionLocal

async with AsyncSessionLocal() as session:
    result = await session.execute(select(Book).where(Book.id == 1))
    book = result.scalar_one_or_none()
```

**Why lazy?** Tests can override `DATABASE_URL` and reset the cached engine before any query fires. Avoids "connection opened during import" foot-guns.

#### Sessions and `get_session()`

Use the `get_session()` async generator inside services for the standard request-scope lifecycle (commit on clean exit, rollback on exception, always close):

```python
from library.db.engine import get_session

async def list_books_service(...) -> ListBooksResponse:
    async for session in get_session():
        # session.execute, session.add, etc.
        ...
        return response   # commit happens automatically on clean exit
    # if any exception raised inside, rollback ran and the exception propagates
```

For multi-step "all or nothing" work, use the `.begin()` form to make the transaction explicit:

```python
async with AsyncSessionLocal.begin() as session:
    book = Book(title="Dune", author="Herbert")
    session.add(book)
    await session.flush()                       # gets book.id without commit
    for _ in range(num_copies):
        session.add(BookCopy(book_id=book.id))
    # `.begin()` commits at the end of the with block
```

#### Two non-default flags worth knowing

The sessionmaker is configured with `expire_on_commit=False` and `autoflush=False`. Both override SQLAlchemy defaults. Together they mean: **SQL only fires when you explicitly `await` something on the session.**

- **`expire_on_commit=False`** — after `commit()`, attribute access on already-loaded objects DOES NOT trigger a fresh SELECT. Without this flag, reading `book.title` after commit would re-fetch the row, which can fail with `MissingGreenlet` in async code.
- **`autoflush=False`** — pending writes do not auto-flush before each query. You explicitly `await session.flush()` when you need to read your own writes. Makes SQL execution order match Python line order.

In practice this means: you write code that looks sequential and SQL fires sequentially. No surprise queries inside an attribute access.

#### Models (`backend/src/library/db/models.py`)

SQLAlchemy 2.0 typed `Mapped[...]` declarative. Every column has a Python type and a SQL type:

```python
class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    isbn: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    copies: Mapped[list["BookCopy"]] = relationship(back_populates="book")
```

When adding a new field:
1. Add it to the model with the right `Mapped[T]` type
2. Add it in the migration that introduces the column (see §2.3)
3. Update the proto if it's user-visible — and regenerate stubs (§4)

#### The `CopyStatus` enum

Special case: a Postgres `ENUM` type. The pattern is:
- Python `enum.Enum` subclass with `(str, enum.Enum)` so equality with plain strings works
- Mapped via `sa.Enum(CopyStatus, name="copy_status", create_type=False)` — `create_type=False` is critical because the migration owns the type's lifecycle. If both the migration and SQLAlchemy try to emit `CREATE TYPE copy_status`, you get a duplicate-create error.

For full schema rationale, see [`design/01-database.md`](design/01-database.md).

### 2.3 Migrations workflow

We hand-author migrations rather than autogenerate. Reasons: autogenerate misses partial unique indexes, enum value changes, server defaults, and check constraints — exactly the things our schema relies on.

#### Adding a new migration

```sh
cd backend

# Create a new revision file (downstream of head)
uv run alembic revision -m "add_phone_index_on_members"
# Creates: alembic/versions/000X_add_phone_index_on_members.py
```

Open the new file. The skeleton:

```python
revision: str = "0002"
down_revision: str | None = "0001"

def upgrade() -> None:
    op.create_index("members_phone_idx", "members", ["phone"])

def downgrade() -> None:
    op.drop_index("members_phone_idx", table_name="members")
```

Then apply:

```sh
uv run alembic upgrade head     # apply all pending up to current
uv run alembic downgrade -1     # back out the most recent
uv run alembic current          # show current revision
uv run alembic history          # show all
```

**`downgrade()` must reverse `upgrade()` in FK-safe order.** If a migration adds a FK column, `downgrade` removes it. If it adds an index, `downgrade` drops it. If it's truly irreversible (e.g., dropping a column with data), make `downgrade` raise `NotImplementedError("irreversible — see migration X")`.

#### Pulling pending migrations during dev

If a teammate added a migration:

```sh
cd backend
uv run alembic upgrade head
```

Or just rebuild and restart the api container — `entrypoint.sh` runs `alembic upgrade head` before starting the gRPC server.

### 2.4 Resilience layer — retry + timeout nuances

This is the most subtle backend piece. Three modules in `backend/src/library/resilience/`:

| File | Role |
|---|---|
| `policies.py` | The three sanctioned retry policies as immutable dataclasses |
| `classify.py` | Maps a raw exception → `ErrorClass` enum (deadlock / serialization / lock_timeout / connection_dropped / pool_timeout / statement_timeout / integrity / domain / bug) |
| `backoff.py` | Pure-function `compute_backoff(attempt, policy, rng)` |
| `deadline.py` | `contextvars`-based per-request deadline, populated by the gRPC interceptor |
| `decorator.py` | The `@with_retry(policy)` decorator that wraps service methods |

#### The three policies

```python
from library.resilience import RETRY_READ, RETRY_WRITE_TX, RETRY_NEVER, with_retry

class BookService:
    @with_retry(RETRY_READ)        # 3 attempts, 50ms base, 1s cap, ±25% jitter
    async def list_books(self, ...): ...

    @with_retry(RETRY_WRITE_TX)    # 2 attempts, narrower retryable set
    async def create_book(self, ...): ...

    # No decorator → not retryable. Equivalent to @with_retry(RETRY_NEVER).
```

| Policy | Attempts | Retries on | When to use |
|---|---|---|---|
| `RETRY_READ` | 3 | DEADLOCK, SERIALIZATION, LOCK_TIMEOUT, CONNECTION_DROPPED, POOL_TIMEOUT, STATEMENT_TIMEOUT | Pure read RPCs (`Get*`, `List*`) |
| `RETRY_WRITE_TX` | 2 | DEADLOCK, SERIALIZATION, LOCK_TIMEOUT, POOL_TIMEOUT | RPCs that write inside a single transaction (`Create*`, `Update*`, `BorrowBook`, `ReturnBook`) |
| `RETRY_NEVER` | 1 | (nothing) | Operations whose retry semantics you haven't analyzed yet — explicit opt-out |

#### Why `RETRY_WRITE_TX` is narrower than `RETRY_READ`

The crucial nuance: write-transaction retry policy **does NOT include `CONNECTION_DROPPED` or `STATEMENT_TIMEOUT`**.

Reason: those failure modes are **ambiguous mid-commit**. A connection dropped between `COMMIT` being sent and the server's ack arriving — did the commit succeed? You don't know. Retrying could double-apply (e.g., create the same book twice).

For reads it's safe (`SELECT` is idempotent). For writes it's not. The retryable set for `RETRY_WRITE_TX` is exactly "the transaction is provably aborted before any commit could happen" — deadlock, serialization conflict, lock timeout, pool timeout. Those guarantee no double-apply.

#### How classification works

`classify(exc)` dispatches on:
1. SQLAlchemy exception type (`OperationalError`, `IntegrityError`, `DBAPIError`)
2. The original asyncpg exception on `.orig` (typed `DeadlockDetectedError`, `SerializationError`, etc.)
3. Postgres `sqlstate` codes (`40P01`, `40001`, `55P03`, `57014`) when the typed exception isn't available

It deliberately **does not match exception message strings** — those drift across Postgres versions and would silently break under upgrade. Always check the typed exception or the SQLSTATE.

#### Deadline awareness

The gRPC server interceptor (`observability/interceptors.py`) reads the client's deadline at request entry and stamps it into a contextvar:

```python
from library.resilience.deadline import set_deadline_from_grpc_context, time_remaining

# At the start of every RPC (interceptor):
token = set_deadline_from_grpc_context(grpc_context)
try:
    response = await actual_handler(...)
finally:
    DEADLINE_VAR.reset(token)
```

The retry decorator reads `time_remaining()` before each backoff sleep. If the deadline can't accommodate the proposed delay, it skips the retry and re-raises so the gRPC mapper produces `DEADLINE_EXCEEDED`. Avoids "retried 3 times, all timed out anyway, sent 4 doomed requests."

#### The four interlocking timeouts

Configured via env vars (see §5), enforced in `db/engine.py`:

```
asyncpg command_timeout       (Python-side, per statement)
Postgres statement_timeout    (server-side, per statement — actually stops the work)
Postgres lock_timeout         (server-side, lock waits only)
Postgres idle_in_tx_timeout   (server-side, kills forgotten open transactions)
```

**Critical invariant: `lock_timeout < statement_timeout`.** If lock_timeout is higher (or unset), a contended lock surfaces as a generic `STATEMENT_TIMEOUT` (which `RETRY_WRITE_TX` does NOT retry, because it's mid-commit ambiguous). With `lock_timeout` lower, contention surfaces as the cleaner `LOCK_TIMEOUT` (which `RETRY_WRITE_TX` DOES retry, because it's clearly pre-commit).

`engine.py` warns at startup if this invariant is violated.

#### Adding retry to a new method

```python
@with_retry(RETRY_WRITE_TX)
async def my_new_write_rpc(self, request: MyRequest) -> MyResponse:
    async for session in get_session():
        # do the write work
        ...
```

Three rules:
1. **Decorate at the service-method layer**, not the repository or servicer
2. The wrapped method **must own its session lifecycle** — each retry re-opens a fresh `AsyncSessionLocal.begin()`. Retrying inside an already-open transaction is invalid (Postgres aborts the tx on deadlock and refuses further work)
3. Pick `RETRY_READ` for pure reads, `RETRY_WRITE_TX` for writes, `RETRY_NEVER` (or no decorator) if you haven't analyzed it yet

For full design rationale, see [`design/01-database.md` §3](design/01-database.md#3-concurrency-strategy-the-partial-unique-index) and the resilience module's docstrings.

### 2.5 Adding a new RPC end-to-end

Putting it all together — a reference recipe for adding `MarkBookLost(book_id, copy_id)` to the API:

```sh
# 1. Edit the proto for the right service
$EDITOR proto/library/v1/book.proto
# Add: rpc MarkBookLost(MarkBookLostRequest) returns (MarkBookLostResponse);
# inside `service BookService { ... }`. Define the two message types.
# (Use member.proto / loan.proto for those subdomains instead.)

# 2. Regenerate stubs (both sides)
cd backend  && uv run bash scripts/gen_proto.sh && cd ..
cd frontend && npm run gen:proto                && cd ..

# 3. Add service-layer logic
$EDITOR backend/src/library/services/book_service.py
# Add `async def mark_book_lost(self, request) -> MarkBookLostResponse:`
# Decorate with @with_retry(RETRY_WRITE_TX). Imports use `book_pb2`.

# 4. Add the SQL in the repository
$EDITOR backend/src/library/repositories/books.py
# Add `async def update_copy_status(session, copy_id, status) -> BookCopy:`

# 5. Wire the servicer (BookServicer for a book RPC)
$EDITOR backend/src/library/servicer.py
# Add `async def MarkBookLost(self, request, context):` to `class BookServicer`
# It should: call self._book_service.mark_book_lost(request)
# The error decorator handles DomainError → grpc.StatusCode mapping
# (Member RPCs go in MemberServicer; loan RPCs in LoanServicer.)

# 6. Add an integration test
$EDITOR backend/tests/integration/test_books.py
# Use the `book_stub` fixture; import `book_pb2`.
# Test happy path, NOT_FOUND on bad copy_id, FAILED_PRECONDITION on already-lost.

# 7. Run tests
./test.sh integration

# 8. (When ready) wire the frontend to call it
$EDITOR frontend/src/components/BookDetail.tsx
# Add a button that calls bookClient.markBookLost({...}) via useMutation.
# (Member RPCs → memberClient; loan RPCs → loanClient.)
```

This pattern — proto → regen → repository → service → servicer → test → frontend — is the canonical change shape for any new RPC.

---

## 3. Frontend — deep dive

### 3.1 App Router structure

Next.js 16 App Router. Each route is a directory with `page.tsx` (server-rendered shell) plus optional client-component siblings:

```
src/app/
├── layout.tsx                  # global shell (TopNav + Providers)
├── page.tsx                    # / dashboard (server)
├── books/
│   ├── page.tsx                # /books — Suspense + BooksList client
│   ├── BooksList.tsx           # client component (uses useSearchParams)
│   ├── new/page.tsx            # /books/new (client)
│   └── [id]/
│       ├── page.tsx            # await params → BookDetail (server shell)
│       ├── BookDetail.tsx      # client (renders + interactivity)
│       └── edit/{page,BookEdit}.tsx
├── members/                    # mirrors books/
└── loans/
    ├── page.tsx                # Suspense + LoansList
    ├── LoansList.tsx
    └── new/{page,NewLoanForm}.tsx
```

#### The Next 16 idioms in play

- **`params` and `searchParams` are async Promises.** Server components must `await params`:
  ```tsx
  export default async function Page({ params }: { params: Promise<{ id: string }> }) {
    const { id } = await params;
    return <BookDetail bookId={id} />;
  }
  ```
- **Client components that use `useSearchParams()` need a Suspense boundary.** Pattern:
  ```tsx
  // page.tsx
  export default function Page() {
    return (
      <Suspense fallback={<Skeleton />}>
        <BooksList />
      </Suspense>
    );
  }

  // BooksList.tsx
  "use client";
  export function BooksList() {
    const searchParams = useSearchParams();
    // ...
  }
  ```

### 3.2 Calling an RPC from a page

Three pieces:

```tsx
"use client";
import { client } from "@/lib/client";                 // 1. the typed Connect client
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bookKeys } from "@/lib/queryKeys";            // 2. typed query keys

export function MyComponent() {
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: (v) =>
      client.createBook({                              // 3. typed RPC call
        title: v.title,
        author: v.author,
        numberOfCopies: v.numberOfCopies,
      }),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: bookKeys.lists() });
      // resp is typed as CreateBookResponse — TypeScript knows the shape
    },
    onError: (err) => {
      // err is a ConnectError. See §3.3 for handling.
    },
  });

  // ...
}
```

The **`client`** is a process-wide singleton in `src/lib/client.ts`. It wraps a `createGrpcWebTransport` pointing at `NEXT_PUBLIC_API_BASE_URL` (defaults to `http://localhost:8080`).

The **`bookKeys` / `memberKeys` / `loanKeys`** factories in `src/lib/queryKeys.ts` are the canonical way to construct query keys. Always use them — never hand-build keys, otherwise invalidation won't catch your queries.

### 3.3 Error UX pattern

Three different error treatments, mapped to gRPC status codes:

| gRPC status | UI treatment | Where |
|---|---|---|
| `INVALID_ARGUMENT` | Inline field error (try to parse the field name from the message) | Form components — set `fieldError` state |
| `FAILED_PRECONDITION` | Inline error near the relevant control (no toast) | Borrow flow / Return flow / copy-count adjust |
| `NOT_FOUND` (on a detail page) | Empty state component, not a toast | Detail pages |
| Anything else | Toast with friendly message | Global toast provider |

The mapping logic lives in `src/lib/errors.ts`:

```ts
import { Code } from "@connectrpc/connect";
import { toFriendlyError, toastMessage } from "@/lib/errors";

onError: (err) => {
  const f = toFriendlyError(err);
  if (f.code === Code.InvalidArgument && f.field) {
    setFieldError({ field: f.field, message: f.message });
  } else {
    toast.error(toastMessage(err));
  }
}
```

The pattern keeps inline errors next to the broken control (lower friction) and pushes generic transport / unknown errors into toasts (higher visibility).

### 3.4 Adding a new page

Reference recipe — adding `/loans/[id]` (loan detail page):

```sh
# 1. Create the route directory
mkdir -p frontend/src/app/loans/[id]

# 2. Server-component shell — awaits params
$EDITOR frontend/src/app/loans/[id]/page.tsx
```

```tsx
import { LoanDetail } from "./LoanDetail";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <LoanDetail loanId={BigInt(id)} />;
}
```

```sh
# 3. Client component — does the actual rendering + data fetching
$EDITOR frontend/src/app/loans/[id]/LoanDetail.tsx
```

```tsx
"use client";
import { useQuery } from "@tanstack/react-query";
import { client } from "@/lib/client";
import { loanKeys } from "@/lib/queryKeys";
// ... rest of imports

export function LoanDetail({ loanId }: { loanId: bigint }) {
  const { data, isPending, error } = useQuery({
    queryKey: loanKeys.detail(loanId),
    queryFn: () => client.getLoan({ id: loanId }),  // assuming we add this RPC
  });

  if (isPending) return <Skeleton />;
  if (error) return <EmptyState message="Loan not found" />;

  // render the loan
}
```

```sh
# 4. Add a queryKey factory entry if missing
$EDITOR frontend/src/lib/queryKeys.ts

# 5. Run TypeScript check + Playwright (if it touches the e2e flow)
./test.sh ts
./test.sh e2e --headed
```

---

## 4. gRPC code generation

### 4.1 The two codegens and what each produces

The three `.proto` files under `proto/library/v1/` (`book.proto`, `member.proto`, `loan.proto`) are the **single source of truth**. Both backend and frontend generate from all three in one pass but produce different artifacts:

| Side | Tool | Plugins | Output (per service) | Run via |
|---|---|---|---|---|
| Backend | `python -m grpc_tools.protoc` | (built-in to grpcio-tools) | `<svc>_pb2.py` (messages), `<svc>_pb2_grpc.py` (stub + servicer base), `<svc>_pb2.pyi` (type stubs) — `<svc>` ∈ {`book`, `member`, `loan`} | `bash backend/scripts/gen_proto.sh` |
| Frontend | `buf generate` | `protoc-gen-es` (messages), `protoc-gen-connect-es` (service descriptor) | `<svc>_pb.ts` (TypeScript classes), `<svc>_connect.ts` (Connect descriptor) | `npm run gen:proto` |

Both output trees are gitignored. Both are regenerated on Docker builds. For local dev you must regenerate manually after editing any of the three protos.

### 4.2 When and how to regenerate

**When:**
- You edited any of `proto/library/v1/{book,member,loan}.proto`
- You're on a fresh clone (the `generated/` dirs are empty)
- Your IDE is reporting "module not found" for the generated stubs

**Backend:**

```sh
cd backend
uv run bash scripts/gen_proto.sh
# → wrote stubs to .../backend/src/library/generated/library/v1/
```

The `uv run` prefix is important — without it, the script's `python3` invocation finds the system Python which doesn't have `grpcio-tools` installed. Inside `uv run`, the project's venv is on PATH.

**Frontend:**

```sh
cd frontend
npm run gen:proto
# (silent on success)
```

**Both at once:**

```sh
( cd backend && uv run bash scripts/gen_proto.sh ) && \
( cd frontend && npm run gen:proto )
```

After regenerating, both sides may have new types. Re-run TypeScript / Python type checks:

```sh
./test.sh ts                              # frontend type-check
( cd backend && uv run python -m mypy src 2>/dev/null || uv run pytest tests/unit )
```

### 4.3 Notable codegen details

#### The backend import-rewrite trick

`grpc_tools.protoc` emits `from library.v1 import <svc>_pb2` inside each `<svc>_pb2_grpc.py`. But our generated tree lives one level deeper at `library.generated.library.v1`. Without correction, the import would fail at runtime.

`scripts/gen_proto.sh` loops over the three services and runs a `perl -pi -e` rewrite on each `_grpc.py`:

```bash
for svc in book member loan; do
    perl -pi -e "s|^from library\\.v1 import ${svc}_pb2|from library.generated.library.v1 import ${svc}_pb2|" \
        "$OUT_DIR/library/v1/${svc}_pb2_grpc.py"
done
```

Classic protoc gotcha — well-known issue, simple fix. The script also `rm`s any stale `*_pb2*` files first, so renaming or removing a service won't leave a shadow module behind.

#### The frontend `import_extension=none` flag

In `frontend/buf.gen.yaml`:

```yaml
plugins:
  - local: protoc-gen-es
    out: src/generated
    opt:
      - target=ts
      - import_extension=none      # ← fixes Turbopack resolver issue
```

Default is `protoc-gen-es` emits `from "./book_pb.js"` (etc) imports with `.js` suffix, which Turbopack rejects. `import_extension=none` produces extension-less imports that resolve cleanly.

#### Why two plugins, not one

`protoc-gen-es` produces message classes (framework-agnostic). `protoc-gen-connect-es` produces the service descriptor that `createPromiseClient` consumes (Connect-specific). Splitting them means you could swap to a different RPC framework (or add a second one) without regenerating message classes.

For full discussion see [`design/02-api-contract.md`](design/02-api-contract.md) and [`reference/decisions.md`](reference/decisions.md) row 5.

---

## 5. Development-time configuration

These env vars affect **how the app behaves while you're developing**. Not deployment-specific knobs (those are in [`architecture.md` §9](architecture.md#9-operations)).

| Variable | Default | Why you'd change it during dev |
|---|---|---|
| **`DEMO_MODE`** | `false` | Set `true` to wipe and reseed on every container start — useful when you want a populated UI for visual / Playwright testing |
| **`DEFAULT_LOAN_DAYS`** | `14` | Set to `0` or `1` to make loans overdue immediately — useful for testing fines without waiting two weeks |
| **`FINE_GRACE_DAYS`** | `14` | Same — set low to surface fines quickly during fine-feature dev |
| **`FINE_PER_DAY_CENTS`** | `25` | Bump to `100` (= $1.00/day) to make currency formatting differences obvious |
| **`FINE_CAP_CENTS`** | `2000` | Lower to test cap behavior at the boundary |
| **`DB_STATEMENT_TIMEOUT_MS`** | `5000` | Lower (e.g. `500`) to surface STATEMENT_TIMEOUT errors fast, useful for retry-policy testing |
| **`DB_LOCK_TIMEOUT_MS`** | `3000` | Same — but keep < statement_timeout |
| **`DB_POOL_SIZE`** | `10` | Lower to `1` to test pool exhaustion |
| **`DB_POOL_TIMEOUT_S`** | `5` | Lower to surface POOL_TIMEOUT faster |
| **`OTEL_TRACES_EXPORTER`** | `console` | Already `console` by default — traces print to stdout. Set to `none` to silence them. Set to `otlp` (with `OTEL_EXPORTER_OTLP_ENDPOINT`) to ship to SigNoz. |
| **`OTEL_LOGS_EXPORTER`** | `console` | Same |
| **`NEXT_PUBLIC_API_BASE_URL`** | `http://localhost:8080` | Override to point the frontend at a different backend (test stack on `:8081`, staging, etc.) |

### Setting them during local development

The repo ships [`.env.example`](../.env.example) at the project root with every var documented and grouped by purpose. Copy it once and edit what you need:

```sh
cp .env.example .env             # gitignored
$EDITOR .env                     # tweak whatever
```

After that, your overrides apply everywhere:

```sh
# Path A (Docker) — Compose auto-loads .env
docker compose up -d

# Path B / hybrid (local backend) — source it before running
cd backend
source ../.env
uv run python -m library.main

# Or set ad-hoc per-command, no .env needed
DEMO_MODE=true FINE_PER_DAY_CENTS=100 docker compose up -d api
DEFAULT_LOAN_DAYS=1 OTEL_TRACES_EXPORTER=none uv run python -m library.main

# Frontend — also reads from .env, or override per-command
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8081 npm run dev   # point at test stack
```

**Backend-specific overrides:** if you want backend-only env without affecting Compose / frontend, drop a separate `backend/.env` (also gitignored). `library.config.Settings` reads that path via `python-dotenv` when you run the backend locally.

### Quick toggles for common dev scenarios

```sh
# Surface fines instantly — every loan is overdue and accruing the moment it's created
DEFAULT_LOAN_DAYS=0 FINE_GRACE_DAYS=0 ...

# Silence the OTel console exporter (less noise in api logs)
OTEL_TRACES_EXPORTER=none OTEL_LOGS_EXPORTER=none ...

# Reproduce pool exhaustion locally
DB_POOL_SIZE=1 DB_POOL_TIMEOUT_S=1 ...

# Reproduce statement timeouts fast
DB_STATEMENT_TIMEOUT_MS=100 DB_LOCK_TIMEOUT_MS=50 ...

# Bring up populated for visual testing
DEMO_MODE=true ...
```

For the operational view of these same vars (the deployment perspective), see [`setup.md` § Configuration overrides](setup.md#configuration-overrides) and [`architecture.md` §9](architecture.md#9-operations).

---

## 6. Testing while developing

The whole testing story is in [`test.md`](test.md). Quick reference for the dev-loop:

```sh
./test.sh unit              # ~3-5s, no Docker — fastest feedback
./test.sh integration       # ~10-30s, testcontainers — when you change services or DB
./test.sh ts                # ~10s, frontend type-check
./test.sh e2e --headed      # watch the UI flow, ~90s
./test.sh stack             # bring up an isolated test stack and leave it running
                            #   then `npm run dev` against it for frontend iteration
./test.sh teardown          # clean up after `stack`
./test.sh                   # full pipeline before pushing
```

**Recommended dev loop** while iterating:
1. Make a change
2. Run the most-targeted test (`unit` or `integration`)
3. When green, run `./test.sh` (full pipeline) before pushing

For deep coverage of debugging Playwright failures, viewing test artifacts, common dev workflows, and per-layer test design — see [`test.md`](test.md).

---

## 7. Common dev recipes

### "I want to add a new field to a model"

1. Edit `backend/src/library/db/models.py` — add the `Mapped[T]` field
2. Create a new Alembic migration: `uv run alembic revision -m "add_X_to_Y"`
3. Author `upgrade()` and `downgrade()` in the migration file
4. If the field is user-visible, add it to the proto and regenerate stubs
5. Update repositories, services, frontend pages as needed
6. Run `./test.sh integration` to verify the schema change + service contract together

### "I want to debug a failing integration test"

```sh
cd backend
uv run pytest tests/integration/test_books.py::test_create_book_happy_path -v -s --pdb
# -s: don't capture stdout
# --pdb: drop into pdb on failure
```

For a step-by-step debugger, add `import pdb; pdb.set_trace()` in the test or service code.

### "I want to inspect what SQL is being emitted"

In `backend/src/library/db/engine.py`, add `echo=True` temporarily to `create_async_engine(...)`:

```python
_engine = create_async_engine(url, future=True, pool_pre_ping=True, echo=True)
```

Now every SQL statement and result prints to stdout. Don't commit this — it's noisy.

### "I want to test the borrow flow in isolation"

```sh
# Bring up just the test stack (no tests, no teardown)
./test.sh stack

# Use grpcurl to drive the API directly
grpcurl -plaintext -d '{"book_id": 1, "member_id": 1}' \
  localhost:50052 library.v1.LoanService/BorrowBook

# When done
./test.sh teardown
```

### "I changed the proto, now nothing works"

1. Did you regenerate stubs? `( cd backend && uv run bash scripts/gen_proto.sh ) && ( cd frontend && npm run gen:proto )`
2. Did you rebuild the Docker images? Backend image needs the new stubs baked in: `docker compose build api`
3. Did the message field numbers change? Existing clients with old stubs will break. Use new field numbers, never reuse.

### "I want to surface fines during a 5-minute UI demo"

```sh
DEFAULT_LOAN_DAYS=0 FINE_GRACE_DAYS=0 docker compose down -v && \
DEFAULT_LOAN_DAYS=0 FINE_GRACE_DAYS=0 DEMO_MODE=true docker compose up -d
```

Now every loan is immediately overdue and accruing fines. The dashboard / loans page / member detail will show the fine UI immediately.

### "I want to point my frontend at a different backend"

```sh
cd frontend
NEXT_PUBLIC_API_BASE_URL=https://staging.example.com npm run dev
```

The Connect transport reads this at runtime in dev mode (it's baked at build time for production builds — but `next dev` re-reads on each server boot).

---

## 8. Style and conventions

### Backend (Python)

- **Type hints everywhere.** `mypy --strict` should pass on new code (we don't enforce it in CI, but the codebase is type-clean).
- **No hard SQL strings outside repositories.** Services and servicers go through the SQLAlchemy expression API or call repository methods.
- **No protobuf imports outside services and servicers.** Repositories work with ORM models or plain rows; `db/models.py` is pure SQLAlchemy.
- **Errors raise typed `DomainError` subclasses** (`NotFound`, `AlreadyExists`, `FailedPrecondition`, `InvalidArgument`). Servicer catches them and maps to grpc.StatusCode.
- **Tests use real Postgres** (testcontainers) and an in-process gRPC server. No mocks of SQLAlchemy or the gRPC stack.
- **Async all the way down.** `async def` on services, repositories, and the engine. Sync code only inside pure-function modules (resilience/backoff.py, services/fines.py).

### Frontend (TypeScript)

- **`"use client";` only when actually needed** — interactive components, hooks, event handlers. Default to server components.
- **TanStack Query for server state, React state for UI state.** Don't push form input state into Query.
- **`bigint` for int64 fields** (book_id, member_id, etc.). Convert at boundaries (URL params come in as strings; use `BigInt(id)` to convert).
- **No raw CSS files** — Tailwind v4 utilities + `@theme` tokens in `globals.css`.
- **Locators in tests use roles, not CSS** — `getByRole`, `getByLabel`, `getByText`. Survives UI tweaks better.

### Docs

- **Keep design docs as the source of truth for "why."** Working docs (this file, test.md, setup.md) link into design docs rather than duplicating their content.
- **Update `progress-report.md` when adding a phase.** It's the running log of what's been built and what's deferred.
- **Add to `reference/decisions.md` when making a non-obvious choice.** Future readers need the why, not just the what.

---

## What's next

Once you're oriented:

| You want to... | Read |
|---|---|
| Run tests during dev | [`test.md`](test.md) |
| Verify a specific subsystem's design | [`design/01-database.md`](design/01-database.md), [`design/02-api-contract.md`](design/02-api-contract.md), [`design/03-backend.md`](design/03-backend.md), [`design/04-frontend.md`](design/04-frontend.md), [`design/05-infrastructure.md`](design/05-infrastructure.md) |
| Look up a decision or its rationale | [`reference/decisions.md`](reference/decisions.md) |
| See the high-level architecture | [`architecture.md`](architecture.md) |
| See how the project was built phase-by-phase | [`phases/`](phases/) |
