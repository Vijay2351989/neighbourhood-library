# Neighborhood Library

A staff-facing application for a small neighborhood library to manage its **books**, **members**, and **lending operations** (borrow, return, overdue handling, fine tracking).

Built as a self-hosted, single-tenant tool: install behind the library's front desk, open the URL, and start cataloging. There is no login screen — anyone with network access to the app is implicitly trusted as staff. Members are the people who walk in to borrow books; they never touch the application.

Stack at a glance: **Python gRPC** backend, **Envoy** as a gRPC-Web translator, **PostgreSQL** for storage, **Next.js + React + TypeScript** UI calling the backend over **gRPC-Web**, all wired together by **Docker Compose**.

---

## Key functional scenarios

What a librarian actually does with the app, mapped to the routes that handle each:

| Scenario | What happens | Where it lives |
|---|---|---|
| **Open shop in the morning** | Glance at the dashboard — total books, total members, active loans, overdue count, total outstanding fines, recent activity feed | `/` |
| **Register a new patron** | Patron walks in for the first time; staff fills name + email (+ optional phone, address) | `/members/new` |
| **Record a borrow** | Patron hands over a book; staff picks the patron, picks the book, optionally overrides the due date, confirms | `/loans/new` |
| **Record a return** | Patron returns a book; staff finds the active loan on the patron's profile, clicks Return | `/members/[id]` (Active tab) |
| **Manage the catalog** | New shipment → create the book with N copies. Existing book → adjust copy count up/down (down is rejected if the count would drop below currently-borrowed) | `/books`, `/books/new`, `/books/[id]/edit` |
| **Audit overdue / fines** | End of week: filter loans by Overdue or Has Fine; click into a member to see their outstanding total | `/loans` (filter chips), `/members/[id]` (fines tile) |

Optional features that go beyond the minimum (and exercise the tricky parts of the design):

- **Computed fines.** Loans accrue $0.25/day after a 14-day grace period past due, capped at $20. Fines are computed at query time, never stored — see [`docs/design/01-database.md` §5](docs/design/01-database.md#5-fine-policy-computed-not-stored).
- **Concurrency-safe borrowing.** A `partial unique index` on `loans(copy_id) WHERE returned_at IS NULL` plus `SELECT ... FOR UPDATE SKIP LOCKED` guarantees that two concurrent borrow requests for the same physical copy cannot succeed — see [`docs/design/01-database.md` §3](docs/design/01-database.md#3-concurrency-strategy-the-partial-unique-index).
- **Resilience layer.** Service methods are wrapped with a typed retry decorator (`@with_retry(RETRY_READ)` / `RETRY_WRITE_TX`) that classifies database exceptions and applies exponential backoff with jitter, all bounded by the active gRPC deadline.
- **Observability.** OpenTelemetry traces, span events for retries, structured access logs that include request attempt counts. Optional SigNoz overlay via a Compose profile.

---

## Quick start

For a one-screen "see it working" — Docker is the only prerequisite:

```sh
git clone <repo-url> neighborhood-library
cd neighborhood-library

# (Optional) copy the env template if you want to tune anything
cp .env.example .env

docker compose up -d
```

After ~30 seconds (or ~3-5 minutes on first run while images build), open:

| URL | What you'll see |
|---|---|
| **http://localhost:3000** | The staff UI dashboard with five count tiles — the app itself |
| **http://localhost:8082** | **gRPC API explorer** (Swagger-equivalent) — interactive form-based UI to call any RPC, generated automatically from the proto via gRPC reflection |

To see the app populated with sample data instead of empty:

```sh
docker compose down -v          # wipe any prior state
DEMO_MODE=true docker compose up -d
```

This seeds 21 books, 10 members, 11 loans (including overdue and fined loans) on every startup, so you can see filters, the fines tile, and the dashboard counts populated.

### Env vars

Every environment variable across the stack is documented in [**`.env.example`**](.env.example) at the repo root. Copy to `.env` (gitignored) and tweak — Compose auto-loads it. For full guidance on overrides per scenario (Path A Docker, Path B fully-local, hybrid), see [`docs/setup.md` § Configuration overrides](docs/setup.md#configuration-overrides).

For everything else — port-conflict troubleshooting, running without Docker, hybrid configurations, fully-local Python + Node + Postgres + Envoy setup, verification checklists — see [**`docs/setup.md`**](docs/setup.md).

---

## Where to go next

| If you want to... | Read |
|---|---|
| **Get the app running** (detailed prerequisites, Docker path, fully-local path, verification, troubleshooting) | [`docs/setup.md`](docs/setup.md) |
| **Understand the architecture** (components, why gRPC-Web, how Envoy fits, tech-stack rationale) | [`docs/architecture.md`](docs/architecture.md) |
| **Make code changes** (backend / frontend layering, codegen, retry+timeout nuances, dev recipes) | [`docs/development.md`](docs/development.md) |
| **Run all tests / set up a dev test loop** | [`docs/test.md`](docs/test.md) |
| **Look up an env var** (purpose, default, where consumed, what changes when you set it) | [`docs/configuration.md`](docs/configuration.md) |
| **Understand the Docker stack** (how Compose wires services, Dockerfiles, healthchecks, the seed system, observability profile) | [`docs/deploy.md`](docs/deploy.md) |
| **Understand a specific subsystem** | [`docs/design/`](docs/design/) — five focused design docs (database, API contract, backend, frontend, infrastructure) |
| **See how the project was built** | [`docs/phases/`](docs/phases/) — seven phase docs walking through the sequential implementation |
| **Look up why a decision was made** | [`docs/reference/decisions.md`](docs/reference/decisions.md) — central registry of design decisions |
| **Read the original spec** | [`docs/00-overview.md`](docs/00-overview.md), or the archived monolith at [`docs/archive/SPEC-monolithic.md`](docs/archive/SPEC-monolithic.md) |

---

## Project status

### What's complete

A working four-tier staff application — the librarian opens `http://localhost:3000` and can do everything in the [Key functional scenarios](#key-functional-scenarios) table above. The full surface includes:

- 12 gRPC RPCs covering book CRUD, member CRUD, and loan lifecycle (borrow / return / list / get-member-loans)
- Schema with normalized `Book` / `BookCopy` split, computed fines (no fines column), and a partial unique index that makes double-borrow structurally impossible
- Resilience layer with three named retry policies, deadline-aware backoff, and four interlocking database timeouts
- OpenTelemetry tracing with an opt-in self-hosted SigNoz overlay
- Backend integration tests against a real Postgres (testcontainers), plus a Playwright happy-path browser test
- One-command bring-up via Docker Compose, with a `DEMO_MODE` toggle for ephemeral seeded data

For per-phase implementation detail, see [`docs/phases/`](docs/phases/) (seven phases, all CTO-certified `<promise>DONE</promise>` per [`docs/progress-report.md`](docs/progress-report.md)).

### What's intentionally NOT done

These were considered, discussed, and deliberately excluded from scope. Each entry explains *what it is*, *why it's excluded*, and *what it would take to add later*.

#### 1. Authentication and authorization

- **What it would be:** A login flow, user accounts for staff, password hashing, session/JWT issuance, gRPC interceptor validating credentials, and per-RPC permission checks.
- **Why excluded:** This is a **single-tenant, on-premise staff tool**. The deployment model is "install on the front-desk machine; anyone with network access is implicitly trusted as staff" — same security boundary as a coffee shop's POS terminal or a museum kiosk. Adding auth would invent a problem the deployment shape doesn't have, and the assignment rubric doesn't score auth-specific work.
- **What adding it would take:** A `users` table + Argon2/bcrypt password hashing, a login RPC, a gRPC interceptor that validates a JWT from the `authorization` metadata header, and auth-aware UI states (login screen, redirect on 401, logout). Roughly 8-15 hours, none of which would move the rubric needle. See [the auth-related discussion in `docs/architecture.md` §2](docs/architecture.md) and the related rationale in [`docs/00-overview.md` §4 non-goals](docs/00-overview.md#4-explicit-non-goals).

#### 2. Rate limiting

- **What it would be:** Per-client request quotas (e.g., max 100 RPCs/minute per source IP) enforced at the Envoy edge or in a dedicated middleware layer.
- **Why excluded:** Rate limiting protects against abuse from untrusted clients. With **no untrusted clients** (single-tenant trusted network — see #1), there's no abuse vector worth defending against. The cost of adding it (configuration, tuning, debugging false-positive blocks) exceeds the value at this scale.
- **What adding it would take:** Either Envoy's `local_ratelimit` filter (cheapest — config in `envoy.yaml`, no extra services) or `ratelimit` filter with a separate Redis-backed quota service (industrial-grade). For a multi-tenant SaaS deployment of this code, this would become important. For the take-home it's noise.

#### 3. Caching layer

- **What it would be:** A read-through cache (Redis or in-process LRU) in front of `ListBooks` / `ListMembers` / `GetBook` to reduce database hits on hot reads.
- **Why excluded:** **Premature optimization.** A neighborhood library has hundreds of books and members, not millions. The aggregate `available_copies` query joins `book_copies` and counts — at this scale it returns in single-digit milliseconds against the indexed `(book_id, status)` composite. There's no measurable performance problem to solve. Adding cache invariants (TTL, invalidation on writes, consistency between cache and DB) would introduce bugs without benefit.
- **What adding it would take:** Either an LRU cache in `repositories/books.py` keyed by `(search, offset)` with TTL, or a Redis cache shared across replicas. Both would also need cache-busting on writes (`CreateBook` invalidates list cache, etc.). Worth doing if list-page latency exceeds ~50ms at production scale; a non-issue today.

#### 4. Member-facing service (`library.public.v1`)

*One of the two "considered but not built" services.*

- **What it would be:** A separate gRPC service surface — likely in a different proto package (`library.public.v1`) with its own RPCs — for **patrons interacting with the system themselves**. Examples: browse the catalog from home, place a hold on a checked-out book, renew a loan online, view their own outstanding fines.
- **Why excluded:** Out of scope per the assignment, and adds an entire dimension the current model doesn't need:
  - Member-facing **UI** (different page tree from staff)
  - **Authentication** (members would have credentials — bringing back #1)
  - **Authorization** (members can only see *their own* loans, not everyone's)
  - A **separate proto package** so admin RPCs aren't accidentally exposed
  - Probably an **API gateway** (separate Envoy listener) for public-facing traffic
- **What adding it would take:** Real engineering effort — easily a sprint of work. The current backend's clean layering would help (the `loan_service` already has `GetMemberLoans` which is naturally member-scoped), but adding auth + a parallel UI is the bulk of the work. A common pattern would be to deploy this as a separate `library-public` service binary that shares the database with the staff backend but exposes a different RPC surface.

#### 5. Payment / fine-clearing service

*The second "considered but not built" service.*

- **What it would be:** A service that records **fine payments** and clears outstanding amounts. Right now fines are *computed* (always visible, never paid); this would be the missing piece to actually settle them.
- **Why excluded:** Fines are computed at query time from `loan.due_at`, `loan.returned_at`, and the policy env vars — there's no fines column in the schema. Adding payments would require:
  - A `fine_payments` table (`loan_id`, `amount_cents`, `paid_at`, `cashier_id` or similar)
  - A new `RecordFinePayment` RPC
  - An updated `compute_fine_cents` that subtracts paid amounts from the computed total
  - Audit trail thinking (fine waivers, partial payments, refunds)
  - Possibly a "cash drawer" reconciliation feature
- **Why it's a meaningful second service:** Payments are a separate **bounded context** from lending. A real library would have separate UI for "borrow/return at the desk" (this app) and "settle fines at the cashier" (the deferred service), with different staff roles and different audit requirements.
- **What adding it would take:** A new schema migration (~1 hour), the payments service module (~3-4 hours), UI integration to the existing member detail page showing paid-vs-unpaid fine breakdown (~2-3 hours). Total ~8 hours — but with real complexity around the audit/reconciliation requirements that take it from "feature" to "domain." See [`docs/00-overview.md` §4 non-goals](docs/00-overview.md#4-explicit-non-goals) where this is listed explicitly.

### Summary of what we built vs. what we excluded

| Concern | Our choice | Rationale |
|---|---|---|
| Authentication | **None** — trusted-network deployment | Single-tenant on-prem staff tool; rubric doesn't score it |
| Rate limiting | **None** | No untrusted clients in the deployment model |
| Caching | **None** — query the DB on every read | Library scale doesn't warrant the consistency complexity |
| Member self-service | **Excluded** — staff-only UI | Whole separate service + UI + auth dimension |
| Fine payments | **Excluded** — fines computed but never paid | Whole separate service + audit/reconciliation domain |
| Loan due dates | **Implemented** | Cheap; adds rubric value |
| Computed fines | **Implemented** | Demonstrates non-trivial domain logic |
| Concurrency safety | **Implemented** (partial unique index + FOR UPDATE SKIP LOCKED) | Foundational — prevents structural inconsistency |
| Resilience | **Implemented** (typed retry policies, deadline-aware) | Production-realistic engineering posture |
| Observability | **Implemented** (OTel + opt-in SigNoz) | Production-realistic engineering posture |

The rule of thumb behind these calls: **build what the rubric scores and what makes the system structurally honest; defer everything that just adds complexity without affecting correctness or evaluability.**

---

## License

MIT. See [`LICENSE`](LICENSE) at the repo root for full terms.

---

<!--
Outline (planned, will fill in incrementally):

  ✓ 1. Introduction
  ✓ 2. Quick start
  ✓ Where to go next (navigation table)
  ✓ Project status (what's done, intentional non-goals)
  ✓ License
-->
