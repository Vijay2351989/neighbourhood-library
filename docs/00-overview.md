# Overview & Architecture

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](README.md)

A take-home build for a small library to manage members, books, and lending operations. This document covers *what* we are building and *why*; the design docs cover the structural answers, and the phase docs cover the implementation order.

---

## 1. Problem

A small neighborhood library needs a service to manage its members, its book catalog, and the day-to-day act of lending books out and getting them back. Today nothing exists — staff need a working web application backed by a real service.

## 2. Solution at a glance

A four-tier system:

1. A **Next.js** staff-facing web UI.
2. An **Envoy** proxy that translates browser-friendly gRPC-Web into native gRPC.
3. A **Python gRPC** service implementing the four core operations (book CRUD, member CRUD, borrow, return) plus list/query endpoints.
4. A **PostgreSQL** database with a normalized schema that distinguishes the abstract `Book` (a title) from a concrete `BookCopy` (a physical item on the shelf).

The whole thing comes up with a single `docker compose up`.

## 3. Rubric criteria this design targets

| Rubric item | How we hit it |
|---|---|
| Schema design — normalization, relationships | Four-table normalized schema. `Book`/`BookCopy` split lets us model real-world inventory and keep loan rows pointing at a physical copy. Foreign keys enforced; indexes on lookup columns. See [design/01-database.md](design/01-database.md). |
| Service interface — intuitive, well-structured RPC | One `LibraryService` proto with verb-noun method names following gRPC conventions (`CreateBook`, `BorrowBook`, `ListLoans`). Distinct request/response messages per RPC. Standard gRPC status codes for failure modes. See [design/02-api-contract.md](design/02-api-contract.md). |
| Code quality — organization, readability | Layered Python backend (proto → services → repositories → db). Small focused modules. Type hints everywhere. SQLAlchemy 2.0 typed mappings. See [design/03-backend.md](design/03-backend.md). |
| Documentation — ease of setup, clear test instructions | Single-command bring-up. README walks through prereqs, compose, .proto regeneration, env vars, sample client, and how to run tests. See [reference/readme-outline.md](reference/readme-outline.md). |

## 4. Explicit non-goals

- **Authentication / authorization.** No login. The app assumes trusted staff use.
- **Fine payments, waivers, partial payments, refunds.** Fines themselves are computed and displayed for overdue loans (see [design/01-database.md §3.5](design/01-database.md#fine-policy-computed-not-stored)), but there is no payment ledger and no concept of a fine being "paid." A real library would need a payments table; out of scope here.
- **Per-copy management UI.** Staff manage books at the title level with a "number of copies" input. The backend manages individual copy rows.
- **Multi-tenancy / multi-branch.** One library, one database.
- **Member-facing UI.** Staff-facing only.
- **Real-time notifications, email reminders, etc.**
- **Production hardening.** No TLS termination, no secrets manager, no rate limiting. This is a take-home demo.

## 5. Estimated complexity

**Medium.** No exotic infrastructure, but the gRPC-Web toolchain plus the borrow/return concurrency story plus a non-trivial Next.js UI plus migrations and seed data plus Docker Compose orchestration adds up. The phased plan is sized accordingly — total ~50 hours of focused work.

---

## 6. Architecture

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

The infrastructure (Envoy, Compose, healthchecks, seed profile) is fully specified in [design/05-infrastructure.md](design/05-infrastructure.md).

---

## 7. Where to go next

- **For schema details:** [design/01-database.md](design/01-database.md)
- **For the wire contract:** [design/02-api-contract.md](design/02-api-contract.md)
- **For implementation order:** [phases/phase-1-scaffolding.md](phases/phase-1-scaffolding.md)
- **For decisions and risks:** [reference/decisions.md](reference/decisions.md)
