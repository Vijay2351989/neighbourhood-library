# Neighborhood Library — Specification Index

A take-home build for a small library to manage members, books, and lending operations. Stack: Python gRPC server + Envoy gRPC-Web proxy + PostgreSQL + Next.js.

This directory contains the full specification, broken into focused, modular documents. Read in the order suggested below for the smoothest understanding.

---

## Document Map

### Start here
- **[00-overview.md](00-overview.md)** — Problem statement, goals, non-goals, rubric criteria, and the system architecture diagram. **Everyone reads this first.**

### Design documents — the *what* (cross-cutting reference)
These are read-once references that the phase documents link into.

- **[design/01-database.md](design/01-database.md)** — PostgreSQL schema (DDL), table-by-table rationale, concurrency strategy (partial unique index + `FOR UPDATE SKIP LOCKED`), `available_copies` computation, and the fine policy (§3.5).
- **[design/02-api-contract.md](design/02-api-contract.md)** — Full `library.proto` content (messages, RPCs) and the gRPC error-semantics table.
- **[design/03-backend.md](design/03-backend.md)** — Python project layout, module responsibilities, generated-code policy.
- **[design/04-frontend.md](design/04-frontend.md)** — Next.js project layout, gRPC-Web client choice, TanStack Query data-fetching pattern, page-by-page responsibilities.
- **[design/05-infrastructure.md](design/05-infrastructure.md)** — Envoy proxy config and Docker Compose topology (services, healthchecks, env vars, seed profile).

### Phases — the *how* and *when* (implementation roadmap)
Sequential. Each phase ends with something runnable so we can validate before moving on. **Do not start phase N+1 until phase N's acceptance criteria are met.**

| # | Phase | Effort | Document |
|---|---|---|---|
| 1 | Repo & Infra Scaffolding | M (~4h) | [phases/phase-1-scaffolding.md](phases/phase-1-scaffolding.md) |
| 2 | Schema & Migrations | S (~2h) | [phases/phase-2-schema-migrations.md](phases/phase-2-schema-migrations.md) |
| 3 | Protobuf Contract & Codegen | M (~3h) | [phases/phase-3-proto-codegen.md](phases/phase-3-proto-codegen.md) |
| 4 | Backend CRUD: Books & Members | L (~10h) | [phases/phase-4-backend-crud.md](phases/phase-4-backend-crud.md) |
| 5 | Borrow & Return with Concurrency + Fines | L (~12h) | [phases/phase-5-borrow-return-fines.md](phases/phase-5-borrow-return-fines.md) |
| 6 | Frontend MVP | L (~14h) | [phases/phase-6-frontend-mvp.md](phases/phase-6-frontend-mvp.md) |
| 7 | Polish: Seed, Sample Client, README | M (~6h) | [phases/phase-7-polish.md](phases/phase-7-polish.md) |

Total estimated effort: ~50 hours of focused work.

### Reference
- **[reference/testing.md](reference/testing.md)** — Layered testing strategy (unit, integration, sample-client smoke, optional Playwright).
- **[reference/decisions.md](reference/decisions.md)** — Open questions, risks, and the decisions taken on each (ISBN uniqueness, member delete behavior, gRPC-Web tooling, time zones, fine policy, etc.).
- **[reference/readme-outline.md](reference/readme-outline.md)** — The skeleton of the project's user-facing README that ships in Phase 7.

### Archive
- **[archive/SPEC-monolithic.md](archive/SPEC-monolithic.md)** — The original single-document spec. Kept for traceability; the modular documents above are the current source of truth.

---

## Recommended reading order

**For a reviewer who wants the whole picture:**
1. `00-overview.md`
2. `design/01-database.md` through `design/05-infrastructure.md` (in order)
3. `phases/phase-1-...` through `phases/phase-7-...` (in order)
4. `reference/*` as needed

**For a developer about to implement Phase N:**
1. `00-overview.md` (once)
2. `phases/phase-N-*.md`
3. The design docs that phase links to (in its "Related design docs" section)

---

## Document status

| Document | Status | Last updated |
|---|---|---|
| 00-overview.md | Complete | 2026-05-05 |
| design/01-database.md | Complete | 2026-05-05 |
| design/02-api-contract.md | Complete | 2026-05-05 |
| design/03-backend.md | Complete | 2026-05-05 |
| design/04-frontend.md | Complete | 2026-05-05 |
| design/05-infrastructure.md | Complete | 2026-05-05 |
| phases/phase-1-scaffolding.md | Approved, not yet started | 2026-05-05 |
| phases/phase-2-schema-migrations.md | Approved, not yet started | 2026-05-05 |
| phases/phase-3-proto-codegen.md | Approved, not yet started | 2026-05-05 |
| phases/phase-4-backend-crud.md | Approved, not yet started | 2026-05-05 |
| phases/phase-5-borrow-return-fines.md | Approved, not yet started | 2026-05-05 |
| phases/phase-6-frontend-mvp.md | Approved, not yet started | 2026-05-05 |
| phases/phase-7-polish.md | Approved, not yet started | 2026-05-05 |
| reference/testing.md | Complete | 2026-05-05 |
| reference/decisions.md | Complete | 2026-05-05 |
| reference/readme-outline.md | Complete | 2026-05-05 |

---

## How to use this documentation

- **Treat the design docs as the single source of truth** for any structural question (schema, proto, project layout, infrastructure). Phase docs reference them rather than restating them.
- **Phases are sequential.** Skipping ahead is allowed for reading, but implementation gates on the prior phase's acceptance criteria.
- **Updates** to any decision should land in the relevant design doc *and* in `reference/decisions.md`. Phase docs only need updating if scope or acceptance changes.
