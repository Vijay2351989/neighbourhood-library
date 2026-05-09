# Neighborhood Library

A take-home build of a small library management service. Staff can manage books and members, lend copies out, take them back, and see who owes what in fines. The whole stack — Postgres, a Python gRPC service, an Envoy gRPC-Web proxy, and a Next.js staff UI — comes up with a single `docker compose up`.

> **Looking for the design rationale?** Start at [`docs/README.md`](docs/README.md). The phase-by-phase implementation plan lives under [`docs/phases/`](docs/phases/) and the per-area design docs live under [`docs/design/`](docs/design/).

---

## Architecture overview

```
+----------------------+       +-----------------+       +-------------------------+       +----------------+
|                      |       |                 |       |                         |       |                |
|  Next.js staff UI    | ----> |  Envoy proxy    | ----> |  Python gRPC service    | ----> |  PostgreSQL    |
|  (browser, :3000)    | gRPC- |  (:8080,        | HTTP/2|  (:50051,               | TCP   |  (:5432)       |
|                      |  Web  |   admin :9901)  | native|   LibraryService impl)  |       |                |
|  gRPC-Web client     |       |  grpc_web + CORS|  gRPC |  SQLAlchemy 2.0 async   |       |                |
|  (Connect-Web stubs) |       |   filters       |       |  Alembic migrations     |       |                |
+----------------------+       +-----------------+       +-------------------------+       +----------------+
```

The browser cannot speak native gRPC's HTTP/2 framing directly, so the UI talks **gRPC-Web** to Envoy, which translates to native gRPC on the way to the Python server. The same `LibraryService` proto contract drives both sides — the backend regenerates Python stubs and the frontend regenerates TypeScript stubs from the single `proto/library/v1/library.proto`.

The Python service is layered: a thin proto-aware servicer (`library.servicer`) calls into proto-free domain services (`library.services.*`) which call into proto-free repositories (`library.repositories.*`) which own the SQL. The split keeps tests against real Postgres (via testcontainers) clean of mock plumbing. Concurrency safety on the borrow path is structural — a partial unique index on `loans(copy_id) WHERE returned_at IS NULL` makes double-borrow a database-level invariant. Fine policy is computed at query time, not stored, so no cron job ever lies about today's fine total.

---

## Prerequisites

For the basic `docker compose up` flow you need only:

- **Docker Desktop** (or any Docker engine new enough for Compose v2 named build contexts — Docker 23+).

Optional extras for local development outside the containers:

- **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/) for backend hacking
- **Node 20+** for frontend hacking
- [`buf`](https://buf.build) if you want to regenerate proto stubs without going through `npm`

---

## Quick start

```sh
git clone <this-repo> neighborhood-library
cd neighborhood-library
docker compose up
```

That's it for a production-style empty bring-up — wait for the api log line `library api: listening on :50051` and open <http://localhost:3000>. The UI loads against an empty database; you can immediately create a book, create a member, and borrow.

To start with the demo fixture instead — ~20 books, ~10 members, and a mix of active, returned, overdue, and fine-bearing loans:

```sh
DEMO_MODE=true docker compose up
```

`DEMO_MODE=true` makes the api container truncate every table and reseed it on startup. Re-run `DEMO_MODE=true docker compose restart api` any time you want to reset to a clean demo state. Without `DEMO_MODE`, the api never touches existing data.

---

## What you can do

The staff UI at <http://localhost:3000> covers every assignment requirement plus fine visibility:

- **Dashboard** (`/`) — five tiles (total books, members, active loans, overdue loans, outstanding fines) plus a "recent activity" feed.
- **Books** (`/books`) — list with case-insensitive prefix search, paginated. `/books/new` and `/books/[id]/edit` for create/update; the "number of copies" field reconciles physical inventory under the hood. The detail page shows total vs. available copies and recent loans for that title.
- **Members** (`/members`) — list with search; `/members/new` and `/members/[id]/edit`. The detail page shows the member's outstanding fines (computed) and a tabbed history of active vs. returned loans.
- **Loans** (`/loans`) — chip-filtered list (All / Active / Overdue / Has Fine / Returned). `/loans/new` is the borrow flow: pick a book and a member from search-as-you-type pickers, confirm in a dialog, and the loan appears with the server-computed `due_at`. Each row has a Return button that flips `returned_at` and refreshes the list.
- **Fines** are visible everywhere they're relevant — on the dashboard tile, on the member detail page, on every overdue loan in the loans list. Returned-late loans keep the snapshot fine on the row forever (per the [fine policy](docs/design/01-database.md#5-fine-policy-computed-not-stored)).

---

## Database setup

Postgres 16 runs in the `postgres` Compose service. State lives in the named volume `pgdata`, so the database survives `docker compose down` (which only stops containers) but is cleared by `docker compose down -v` (which removes volumes).

The api container runs `alembic upgrade head` on every start (see `backend/entrypoint.sh`), so the schema is always current — no manual migration step required. To inspect the live schema:

```sh
docker compose exec postgres psql -U postgres library -c '\dt'
```

To reset the database to an empty schema:

```sh
docker compose down -v
docker compose up
```

To reset to the demo fixture (truncate-and-reseed without nuking the volume):

```sh
DEMO_MODE=true docker compose restart api
```

To point the api at an external Postgres instead of the bundled one, set `DATABASE_URL` (see [Environment variables](#environment-variables)) and remove the `postgres` service from `docker-compose.yml` (or stop depending on it).

---

## .proto compilation

The single source of truth is `proto/library/v1/library.proto`. Both Dockerfiles regenerate from it during their image builds, so a fresh `docker compose build` is sufficient to pick up changes. To regenerate stubs locally without rebuilding images:

```sh
# Backend (Python stubs into backend/src/library/generated/library/v1/)
cd backend && uv run bash scripts/gen_proto.sh

# Frontend (TypeScript Connect-Web stubs into frontend/src/generated/library/v1/)
cd frontend && npm run gen:proto
```

The backend generator also rewrites a quirk in protoc's emitted import path; see the comment block at the top of `backend/scripts/gen_proto.sh` for the gory detail.

---

## Running outside Docker

Useful when iterating on the Python service with a debugger attached. With the bundled Postgres still running (`docker compose up postgres`), in another terminal:

```sh
cd backend
uv sync                                     # install runtime + dev deps
uv run alembic upgrade head                 # run migrations
uv run python -m library.main               # start the gRPC server
```

The server reads `DATABASE_URL` from your environment; export it if you've changed it from the default. The frontend can run outside Docker too:

```sh
cd frontend
npm install
npm run dev                                 # http://localhost:3000
```

You'll still need Envoy (`docker compose up envoy`) so the browser has a gRPC-Web target to talk to.

---

## Environment variables

All knobs are read from the api container's environment; defaults match the values in `docker-compose.yml`.

| Variable | Default | What it does |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@postgres:5432/library` | SQLAlchemy async URL for Postgres. |
| `GRPC_PORT` | `50051` | TCP port the gRPC server binds to. |
| `DEFAULT_LOAN_DAYS` | `14` | Loan length when the client doesn't pass an explicit `due_at`. |
| `FINE_GRACE_DAYS` | `14` | Days past `due_at` before fines start accruing. |
| `FINE_PER_DAY_CENTS` | `25` | Cents charged per overdue day after the grace period. |
| `FINE_CAP_CENTS` | `2000` | Maximum fine that can accrue on a single loan ($20.00). |
| `DEMO_MODE` | `false` | When `true`, api startup truncates every table and reseeds via `backend/scripts/reset_and_seed.py`. |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8080` | Base URL the browser uses to reach Envoy's gRPC-Web listener. |

Resilience knobs (`DB_*`, `OTEL_*`) are documented in [`docs/phases/phase-5-6-resilience.md`](docs/phases/phase-5-6-resilience.md) and [`docs/design/06-observability.md`](docs/design/06-observability.md). The defaults are sane for local development; you usually don't need to touch them.

---

## Sample client script

`backend/scripts/sample_client.py` is a heavily commented Python walkthrough of the API surface — it doubles as documentation. It connects, creates a member, creates a book with one copy, borrows the book, lists active loans, returns the book, and lists active loans again to prove the borrow disappeared.

After the stack is up:

```sh
# From inside the api container (no host-side Python required):
docker compose exec api python /app/scripts/sample_client.py

# Or from the host (after `cd backend && uv sync`):
uv run python backend/scripts/sample_client.py
```

The script prints a clean six-step summary and exits 0 on success. If the api isn't running it prints a friendly error pointing you at `docker compose ps`.

---

## Testing

The single entry point is **`./test.sh`** — a parameterized runner that can execute the full pre-deploy pipeline or any individual layer in isolation. Quick reference:

```sh
./test.sh                    # full pipeline (unit + integration + ts + sample + e2e)
./test.sh unit               # backend unit tests only (~5s)
./test.sh integration        # backend integration tests only (~30s, testcontainers)
./test.sh ts                 # frontend TypeScript check only
./test.sh sample             # sample client smoke test (brings stack up + tears down)
./test.sh e2e                # Playwright happy-path (headless)
./test.sh e2e --headed       # Playwright with visible browser
./test.sh e2e --debug        # Playwright step-debugger
./test.sh stack              # bring up an isolated test stack and leave it running
./test.sh teardown           # tear down a leftover test stack
./test.sh --help             # full options
```

The `e2e` and `sample` scenarios automatically bring up an isolated test stack (compose project `library-test` on +1 ports — `5433, 50052, 8081, 9902, 3001`) and tear it down on exit, even on failure or Ctrl+C. They don't touch your dev `pgdata` volume.

For full per-layer documentation, common dev workflows, Playwright debugging tips, and troubleshooting, see [**`docs/test.md`**](docs/test.md).

For wire-level manual verification (`grpcurl`, `buf curl`, gRPC-Web byte inspection), see [**`docs/test_api.md`**](docs/test_api.md).

---

## Troubleshooting

**Port conflicts.** This stack binds host ports `5432` (Postgres), `50051` (gRPC), `8080` (Envoy listener), `9901` (Envoy admin), and `3000` (Next.js). When the observability profile is active, also `3301`, `4317`, and `4318`. Find and stop the offending process (`lsof -i :8080`) before bringing the stack up.

**Docker out of memory.** The default 4 GB Docker Desktop allocation is enough for the four core services. Bumping to 6–8 GB is recommended if you also run the SigNoz observability profile, which adds ClickHouse and a query service.

**`UNIMPLEMENTED` from a method that exists.** Almost always means the generated stubs are out of date. Regenerate: `cd backend && uv run bash scripts/gen_proto.sh` and `cd frontend && npm run gen:proto`. Then `docker compose build api web` to bake them into the images.

**Browser CORS error / "no 'Access-Control-Allow-Origin'".** Confirm `envoy` is running (`docker compose ps`) and that `NEXT_PUBLIC_API_BASE_URL` matches the URL the browser is actually loaded from — Envoy's CORS filter allows any origin by default, but the Connect client must be pointed at the proxy, not directly at `:50051`.

**`docker compose up` doesn't seed any data.** Expected — `DEMO_MODE` defaults to `false` for production-style bring-up. Use `DEMO_MODE=true docker compose up` if you want the demo fixture.

---

## Project layout

```
neighborhood-library/
├── backend/                  Python gRPC service
│   ├── src/library/          gRPC servicer, services (domain), repositories (SQL), config, db, observability, resilience
│   ├── alembic/              Schema migrations (single migration; authoritative DDL)
│   ├── scripts/              gen_proto.sh, reset_and_seed.py (DEMO_MODE), sample_client.py
│   ├── tests/                JUnit 5 — unit + integration (testcontainers)
│   ├── Dockerfile            Multi-stage; runs alembic + (optional) seed before serving
│   └── pyproject.toml        uv-managed; Python 3.12+; runtime + dev extras
├── frontend/                 Next.js 16 + Tailwind v4 staff UI
│   ├── src/app/              App Router pages (dashboard, books, members, loans)
│   ├── src/lib/              Connect client singleton, query keys, error mapping, formatting helpers
│   ├── src/generated/        Buf/Connect-Web TS stubs (generated; gitignored)
│   ├── e2e/                  Playwright happy-path test
│   └── Dockerfile            Builds the Next.js dev image
├── proto/library/v1/         Single source of truth: library.proto
├── deploy/
│   ├── envoy/                envoy.yaml — gRPC-Web translation + CORS
│   └── signoz/               Optional observability backend (ClickHouse + collector + UI)
├── docs/
│   ├── 00-overview.md        Problem, solution, architecture, non-goals
│   ├── design/               Per-area design docs (db, api, backend, frontend, infra, observability)
│   ├── phases/               Implementation phase plans (1 through 7)
│   └── reference/            Decisions log, testing notes, README outline (this file's skeleton)
├── docker-compose.yml        Four core services + optional observability profile
├── LICENSE                   MIT
└── README.md                 You are here
```

---

## Design decisions

The full design rationale — schema choices, API shape, concurrency strategy, fine policy, observability layering — is documented in [`docs/README.md`](docs/README.md) and the per-area files under [`docs/design/`](docs/design/). A few decisions worth calling out:

- **`Book` and `BookCopy` are separate tables.** The abstract title is one row; each physical copy is another. Lets us model "two copies of *Dune*, one borrowed and one on the shelf" honestly. ([01-database.md §2](docs/design/01-database.md#2-why-each-table-looks-this-way))
- **gRPC-Web through Envoy, not REST.** The browser-side Connect client and the server-side gRPC code share a single proto contract; no hand-written DTO conversion. Envoy handles the gRPC-Web ↔ native gRPC translation. ([00-overview.md §6](docs/00-overview.md#6-architecture))
- **Fines are computed, not stored.** A pure function in `library.services.fines` and an equivalent SQL expression in `library.repositories.loans` agree on the formula. No cron job, no "today" timezone bug, no invalidation problems. ([01-database.md §5](docs/design/01-database.md#5-fine-policy-computed-not-stored))
- **Concurrency safety is structural.** A partial unique index on `loans(copy_id) WHERE returned_at IS NULL` makes double-borrow impossible at the database layer. The borrow transaction uses `FOR UPDATE SKIP LOCKED` so popular-title parallel borrows don't serialize. ([01-database.md §3](docs/design/01-database.md#3-concurrency-strategy-the-partial-unique-index))
- **Demo data is opt-in via `DEMO_MODE`, not a Compose profile.** One env var, one bring-up command, easy to remember. ([phases/phase-7-polish.md](docs/phases/phase-7-polish.md))
