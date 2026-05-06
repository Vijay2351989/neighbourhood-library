# Backend Design

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)
**Implemented in:** [Phase 1](../phases/phase-1-scaffolding.md), [Phase 4](../phases/phase-4-backend-crud.md), [Phase 5](../phases/phase-5-borrow-return-fines.md)

Python project structure, module responsibilities, and codegen policy for the gRPC server.

---

## 1. Directory layout

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
│       └── 0001_initial.py     # the schema in design/01-database.md
├── proto/                      # symlink or build-time copy of repo-root proto/
│   └── library/v1/library.proto
├── src/
│   └── library/
│       ├── __init__.py
│       ├── main.py             # entrypoint: build server, attach servicer, serve_forever
│       ├── config.py           # env-driven settings
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
│       │   ├── loan_service.py
│       │   └── fines.py        # pure-function fine arithmetic (see design/01-database.md §5)
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
    │   └── test_loan_logic.py  # state transitions + fine formula in isolation
    └── integration/
        ├── test_books.py       # CRUD via real grpc client against in-process server
        ├── test_members.py
        ├── test_borrow_return.py
        └── test_concurrency.py # parallel borrow attempts on a single-copy book
```

---

## 2. Module responsibilities (one line each)

- **`config.py`** — read `DATABASE_URL`, `GRPC_PORT`, `DEFAULT_LOAN_DAYS`, `FINE_GRACE_DAYS`, `FINE_PER_DAY_CENTS`, `FINE_CAP_CENTS` from env via Pydantic settings.
- **`db/models.py`** — SQLAlchemy 2.0 `Mapped[...]` typed model classes mirroring [design/01-database.md](01-database.md).
- **`repositories/*`** — every line of SQL lives here. No protobuf imports allowed.
- **`services/*`** — orchestrate repositories, do protobuf↔domain conversion, raise typed domain errors.
- **`services/fines.py`** — pure-function `compute_fine_cents(due_at, returned_at, now, grace_days, per_day_cents, cap_cents) -> int`. No I/O, no proto imports — purely arithmetic. Used by `loan_service` when building Loan/Member responses.
- **`servicer.py`** — implements the generated `LibraryServiceServicer`, catches domain errors, maps them to gRPC status, returns response messages. No business logic.
- **`errors.py`** — `class NotFound`, `class AlreadyExists`, `class FailedPrecondition`, `class InvalidArgument` plus a decorator that the servicer uses to translate them.
- **`scripts/sample_client.py`** — a standalone Python file using the generated client stubs to do: create member, create book, borrow, list loans, return, list loans again. Demonstrates the API for reviewers.

---

## 3. Layering rules

```
servicer.py        ← gRPC servicer; only proto in/out, only catches domain errors
    │
    ▼
services/*.py      ← orchestration; converts proto ↔ domain; raises domain errors
    │
    ▼
repositories/*.py  ← SQL only; returns ORM models or plain rows
    │
    ▼
db/models.py       ← SQLAlchemy mappings
```

**Forbidden cross-cuts:**
- Repositories never import proto.
- Services never write raw SQL — they call repositories.
- The servicer never executes business logic — it only marshals.

---

## 4. Generated protobuf code: not committed

- `backend/src/library/generated/` is in `.gitignore`.
- `scripts/gen_proto.sh` runs at container build time (and locally via `uv run gen-proto`).
- The same `.proto` is consumed by the frontend codegen, so it lives at the **repo root** as `proto/library/v1/library.proto`. Both backend and frontend reference it from there.

---

## Cross-references

- Schema that the ORM mirrors: [design/01-database.md](01-database.md)
- Wire contract that the servicer implements: [design/02-api-contract.md](02-api-contract.md)
- Codegen mechanics: [phases/phase-3-proto-codegen.md](../phases/phase-3-proto-codegen.md)
- Concurrency strategy used by `repositories/loans.py`: [design/01-database.md §3](01-database.md#3-concurrency-strategy-the-partial-unique-index)
- Fine formula referenced by `services/fines.py`: [design/01-database.md §5](01-database.md#5-fine-policy-computed-not-stored)
