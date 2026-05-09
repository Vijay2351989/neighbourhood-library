# Configuration Reference

Comprehensive registry of every environment variable that affects the stack. Use this as a lookup table when you need to know *what a variable does*, *where it's read*, *what the default is*, and *what changes when you set it*.

> **Looking for setup instructions?** See [`setup.md`](setup.md).
> **Looking for dev-loop overrides** ("how do I make fines accrue immediately"?)? See [`development.md` §5](development.md#5-development-time-configuration).
> **Looking for the canonical template you can copy?** See [`.env.example`](../.env.example) at the repo root.
>
> This document is the **reference** — exhaustive and dry by design.

---

## Table of contents

1. [How env vars get into the running services](#1-how-env-vars-get-into-the-running-services)
2. [Postgres](#2-postgres)
3. [Backend — gRPC server](#3-backend--grpc-server)
4. [Backend — loan & fine policy](#4-backend--loan--fine-policy)
5. [Backend — DEMO_MODE](#5-backend--demo_mode)
6. [Backend — DB resilience](#6-backend--db-resilience)
7. [Backend — observability (OpenTelemetry)](#7-backend--observability-opentelemetry)
8. [Frontend](#8-frontend)
9. [Compose-level (not consumed by the app code)](#9-compose-level-not-consumed-by-the-app-code)
10. [Where each variable is defined](#10-where-each-variable-is-defined)

---

## 1. How env vars get into the running services

Three layers, applied in order of precedence (later wins):

```
┌────────────────────────────────────┐
│ 1. Defaults baked into source       │
│    (Pydantic Settings field default │
│     or Compose `${VAR:-default}`)   │
└──────────────┬─────────────────────┘
               │
               ▼
┌────────────────────────────────────┐
│ 2. .env file at repo root           │
│    (Compose loads automatically;    │
│     gitignored — copy from          │
│     .env.example to start)          │
└──────────────┬─────────────────────┘
               │
               ▼
┌────────────────────────────────────┐
│ 3. Shell env at the time of         │
│    `docker compose up` (or         │
│    `uv run python -m library.main`)│
└────────────────────────────────────┘
```

### Override examples

```sh
# Edit .env, then bring up
$EDITOR .env
docker compose up -d

# Or override per-command
DEMO_MODE=true docker compose up -d

# For Path B (no Docker)
source .env
cd backend && uv run python -m library.main

# Or per-command (Path B)
DEFAULT_LOAN_DAYS=0 OTEL_TRACES_EXPORTER=none uv run python -m library.main
```

For frontend env (`NEXT_PUBLIC_*`):

```sh
NEXT_PUBLIC_API_BASE_URL=http://localhost:8081 npm run dev
```

`NEXT_PUBLIC_*` is a Next.js convention — only variables prefixed `NEXT_PUBLIC_` are exposed to the browser. Anything else stays server-side.

---

## 2. Postgres

Consumed by the `postgres` service in docker-compose.yml.

| Variable | Default | What it does |
|---|---|---|
| `POSTGRES_USER` | `postgres` | Postgres superuser created on container init |
| `POSTGRES_PASSWORD` | `postgres` | Password for `POSTGRES_USER` |
| `POSTGRES_DB` | `library` | Database created on container init. Must match the `library` segment of `DATABASE_URL`. |

**Note:** these are init-time values. Changing them after the volume is populated does not migrate existing data — you'd need to `docker compose down -v` first.

---

## 3. Backend — gRPC server

Consumed by the Python `library.config.Settings` Pydantic model and read at every entrypoint.

| Variable | Default | Type | What it does |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@postgres:5432/library` | str | SQLAlchemy connection URL. **Critical:** the `+asyncpg` dialect suffix is required — the app uses asyncpg, not psycopg2. The `postgres` hostname is the Compose service name; for Path B (local) replace with `localhost`. |
| `GRPC_PORT` | `50051` | int (1..65535) | Port the gRPC server binds to inside the container. Compose maps `50051:50051` so the host can reach it. Changing this also requires updating `docker-compose.yml` port mapping and Envoy's upstream address. |

### Important defaults that aren't env vars

| Setting | Value | Where |
|---|---|---|
| Bind address | `0.0.0.0` | `library/main.py` — hardcoded so the gRPC server is reachable across the Docker network |
| Graceful shutdown grace | `5.0` seconds | `library/main.py` — time given to in-flight RPCs to finish on SIGTERM |
| Health service | always registered | `library/main.py` — `grpc.health.v1.Health` (used by `grpc_health_probe`) |
| Reflection | always registered | `library/main.py` — `grpc.reflection.v1alpha.ServerReflection` (used by `grpcurl` and `grpcui`) |

---

## 4. Backend — loan & fine policy

Govern the lending state machine and fine computation. All consumed by `library.services.loan_service` and `library.services.fines`.

| Variable | Default | Range | What it does |
|---|---|---|---|
| `DEFAULT_LOAN_DAYS` | `14` | ≥0 | Days added to `borrowed_at` to compute `due_at` when the borrow request doesn't override it. Setting `0` makes every loan due immediately. |
| `FINE_GRACE_DAYS` | `14` | ≥0 | Days past `due_at` before fines start accruing. After this grace period, fines compute at `FINE_PER_DAY_CENTS` per day. |
| `FINE_PER_DAY_CENTS` | `25` ($0.25) | ≥0 | Fine amount per day overdue past grace period. |
| `FINE_CAP_CENTS` | `2000` ($20.00) | ≥0 | Maximum fine per loan. Caps the linear accrual at this ceiling. |

### Pure-function semantics

```python
def compute_fine_cents(due_at, returned_at, now, grace_days, per_day_cents, cap_cents):
    reference = returned_at if returned_at is not None else now
    days_past_grace = (reference - due_at).days - grace_days
    if days_past_grace <= 0:
        return 0
    return min(cap_cents, days_past_grace * per_day_cents)
```

Computed at query time. **No fines column in the schema** — the schema doesn't change when policy changes. See [`design/01-database.md` §5](design/01-database.md#5-fine-policy-computed-not-stored).

### Combined effect during demo

`DEFAULT_LOAN_DAYS=0 FINE_GRACE_DAYS=0 FINE_PER_DAY_CENTS=100`: every new loan is overdue and accruing $1.00/day from the moment it's created.

---

## 5. Backend — DEMO_MODE

Single boolean that toggles the seed-on-startup behavior. Consumed by `backend/entrypoint.sh`.

| Variable | Default | Values | What it does |
|---|---|---|---|
| `DEMO_MODE` | `false` | `true` or `false` | When `true`, the api container's entrypoint runs `scripts/reset_and_seed.py` between `alembic upgrade head` and starting the gRPC server. The script TRUNCATEs all four tables and reseeds 21 books, 10 members, 11 loans (including 2 with computed fines and 1 active overdue). |

### Critical operational note

When `DEMO_MODE=true`, **the database is wiped on every container restart**. Anything a librarian entered through the UI is destroyed. Only enable in environments where data durability doesn't matter (demos, screenshots, Playwright runs against populated state).

`docker compose restart api` with `DEMO_MODE=true` re-runs the seed; the seed is idempotent (TRUNCATE-then-insert, never UPSERT) so re-runs produce identical state.

---

## 6. Backend — DB resilience

Tunes the database connection pool and Postgres-side timeouts. All consumed by `library.db.engine`.

| Variable | Default | Unit | What it does |
|---|---|---|---|
| `DB_STATEMENT_TIMEOUT_MS` | `5000` | ms | Postgres `statement_timeout` GUC. Server-side cap on how long a single statement can run before Postgres aborts it with SQLSTATE `57014`. |
| `DB_LOCK_TIMEOUT_MS` | `3000` | ms | Postgres `lock_timeout` GUC. Bounds non-deadlock lock waits. **MUST be lower than `DB_STATEMENT_TIMEOUT_MS`** — see invariant below. |
| `DB_IDLE_TX_TIMEOUT_MS` | `15000` | ms | Postgres `idle_in_transaction_session_timeout` GUC. Kills a session that has held a transaction open without activity. Prevents zombie locks. |
| `DB_COMMAND_TIMEOUT_S` | `5` | s | asyncpg driver-side wall-clock timeout per command. Should be ≥ `DB_STATEMENT_TIMEOUT_MS / 1000` so the driver doesn't pre-empt the server's cleaner timeout. |
| `DB_POOL_SIZE` | `10` | int | SQLAlchemy `pool_size`. Steady-state pool capacity. |
| `DB_MAX_OVERFLOW` | `10` | int | SQLAlchemy `max_overflow`. Extra connections allowed beyond `pool_size` under burst load. Total max = `pool_size + max_overflow`. |
| `DB_POOL_TIMEOUT_S` | `5` | s | SQLAlchemy `pool_timeout`. How long a coroutine waits for a free connection before raising `TimeoutError`. |
| `DB_POOL_RECYCLE_S` | `1800` | s | SQLAlchemy `pool_recycle`. Connections older than this are closed and reopened on next use. Avoids stale connections after Postgres restarts or proxy timeouts. |

### Critical invariant: `lock_timeout < statement_timeout`

If `DB_LOCK_TIMEOUT_MS >= DB_STATEMENT_TIMEOUT_MS`, lock contention surfaces as the ambiguous `STATEMENT_TIMEOUT` error class (which **`RETRY_WRITE_TX` does NOT retry**, because connection-level statement timeout could be mid-commit). Setting `lock_timeout` lower makes contention surface as the cleaner `LOCK_TIMEOUT` error class (which **IS retried**, because lock waits are pre-commit by definition).

`library.db.engine.get_engine()` logs a warning at startup if you violate this.

---

## 7. Backend — observability (OpenTelemetry)

Configure tracing, logging, and metrics export. Read by `library.observability.setup` at server startup.

| Variable | Default | Common values | What it does |
|---|---|---|---|
| `OTEL_SERVICE_NAME` | `library-api` | any string | Identifies this process in trace views. Set differently per deployment (staging-api, prod-api, etc.). |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=library,deployment.environment=local` | comma-separated `key=value` | Additional metadata attached to every span/log. Useful for routing in observability backends. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` | Wire protocol when shipping to an OTLP collector. Stick with `grpc` unless your collector requires HTTP. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (empty) | e.g. `http://signoz-otel-collector:4317` | Where to send OTLP traces/logs. Empty means no remote shipping. The observability profile sets this to the in-network SigNoz collector. |
| `OTEL_TRACES_EXPORTER` | `console` | `console`, `otlp`, `none` | `console` prints span summaries to stdout (handy in dev). `otlp` ships to `OTEL_EXPORTER_OTLP_ENDPOINT`. `none` disables tracing entirely. |
| `OTEL_LOGS_EXPORTER` | `console` | `console`, `otlp`, `none` | Same semantics for structured logs. |
| `OTEL_METRICS_EXPORTER` | `none` | `none`, `otlp` | Metrics are off by default (we don't currently emit custom counters). Set to `otlp` if you wire metrics in later. |
| `OTEL_PROPAGATORS` | `tracecontext,baggage` | comma-separated list | Trace context propagation formats. The W3C trace-context default is what gRPC and HTTP clients widely speak. |

### How the observability profile activates remote shipping

The repo ships an `.env.observability` file with the OTLP overrides. Activate the SigNoz overlay with:

```sh
docker compose --env-file .env.observability --profile observability up -d
```

That file flips `OTEL_TRACES_EXPORTER=otlp` and points `OTEL_EXPORTER_OTLP_ENDPOINT` at the in-network `signoz-otel-collector:4317`. See `deploy.md` for the SigNoz topology.

---

## 8. Frontend

Consumed by Next.js at build time (production build) or dev-server start time (`next dev`).

| Variable | Default | What it does |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8080` | URL the browser uses to reach Envoy. Embedded into the JS bundle (because of `NEXT_PUBLIC_` prefix). For test stack: `http://localhost:8081`. For staging: a remote URL. |

### Why `NEXT_PUBLIC_*`

Next.js strictly separates server-only env from browser-exposed env. **Only variables prefixed `NEXT_PUBLIC_`** are baked into the client JavaScript bundle. Anything else stays server-side.

This means: if you change `NEXT_PUBLIC_API_BASE_URL`, you need to **restart `next dev`** (or rebuild for production) for the new value to take effect. It's not a runtime override.

---

## 9. Compose-level (not consumed by the app code)

These are read by Docker Compose itself, not by the application.

| Variable | Default | What it does |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | (directory name) | Override with `docker compose -p NAME ...` to namespace containers, networks, and volumes. Used by `test.sh` to run a parallel `library-test` stack. |
| `COMPOSE_FILE` | `docker-compose.yml` | Override or augment via `-f file1.yml -f file2.yml`. Used by `test.sh` to layer `docker-compose.test.yml` over the base. |
| Healthcheck params | (per-service) | `interval`, `timeout`, `retries`, `start_period` are baked into `docker-compose.yml`. Not env-driven. |

---

## 10. Where each variable is defined

| Variable | Defined in source | Defined in compose | Default in code |
|---|---|---|---|
| `POSTGRES_*` | n/a | `docker-compose.yml` postgres env block | (image defaults) |
| `DATABASE_URL` | `library/config.py:Settings.database_url` | api env block | (none — required) |
| `GRPC_PORT` | `library/config.py:Settings.grpc_port` | api env block | `50051` |
| `DEFAULT_LOAN_DAYS` | `library/config.py:Settings.default_loan_days` | api env block | `14` |
| `FINE_GRACE_DAYS` | `library/config.py:Settings.fine_grace_days` | api env block | `14` |
| `FINE_PER_DAY_CENTS` | `library/config.py:Settings.fine_per_day_cents` | api env block | `25` |
| `FINE_CAP_CENTS` | `library/config.py:Settings.fine_cap_cents` | api env block | `2000` |
| `DEMO_MODE` | (read directly in `entrypoint.sh`, not via Settings) | api env block | `false` (shell default in entrypoint) |
| `DB_*_TIMEOUT_MS` / `DB_POOL_*` | `library/config.py:Settings.db_*` | api env block | (per field) |
| `OTEL_*` | (read by OTel SDK auto-config + `library/observability/setup.py`) | api env block | varies |
| `NEXT_PUBLIC_API_BASE_URL` | (read by Next.js) | web env block | `http://localhost:8080` |

To read the actual default for any backend variable, check `backend/src/library/config.py`. The Pydantic Settings model is the single source of truth.

---

## What's intentionally NOT configurable via env

These are hardcoded by design — changing them requires a code edit:

- **Bind address (`0.0.0.0`)** — the gRPC server always binds all interfaces. Configurable would invite mistakes.
- **Graceful shutdown duration (`5s`)** — short enough for Compose timeouts, long enough for in-flight RPCs.
- **Health/Reflection service registration** — always on. Disabling them would break Compose healthchecks and `grpcurl`.
- **Schema migrations** — always run on api startup via `alembic upgrade head`. No skip flag.
- **Partial unique index on active loans** — schema-level invariant, not a runtime knob.
- **The three retry policies (RETRY_READ, RETRY_WRITE_TX, RETRY_NEVER)** — values baked in `library/resilience/policies.py`. Editing requires understanding the read/write-tx asymmetry (see [`development.md` §2.4](development.md#24-resilience-layer--retry--timeout-nuances)).

---

## Cross-references

- [`.env.example`](../.env.example) — copy-and-edit template with every var
- [`setup.md` § Configuration overrides](setup.md#configuration-overrides) — operational patterns for setting these
- [`development.md` §5](development.md#5-development-time-configuration) — dev-loop perspective on which vars matter when
- [`deploy.md`](deploy.md) — how Compose wires these into the running services
- [`design/05-infrastructure.md`](design/05-infrastructure.md) — the overall service topology
