# Deployment Internals

How Docker Compose wires the stack together: which containers run, how they're built, what they depend on, and how they communicate. This document walks through `docker-compose.yml` end-to-end so you can read the file *and* understand the design choices behind each block.

> **Looking for "how do I run this"?** See [`setup.md`](setup.md).
> **Looking for "what does each env var do"?** See [`configuration.md`](configuration.md).
> **Looking for "how is each component designed"?** See [`design/`](design/).
>
> This document is the **deployment-mechanics reference** — how Compose actually orchestrates everything.

---

## Table of contents

1. [The default stack — five services](#1-the-default-stack--five-services)
2. [Boot order and dependency graph](#2-boot-order-and-dependency-graph)
3. [Service-by-service deep dive](#3-service-by-service-deep-dive)
   - [3.1 `postgres`](#31-postgres)
   - [3.2 `api` — the Python gRPC server](#32-api--the-python-grpc-server)
   - [3.3 `envoy` — the gRPC-Web bridge](#33-envoy--the-grpc-web-bridge)
   - [3.4 `web` — the Next.js frontend](#34-web--the-nextjs-frontend)
   - [3.5 `grpcui` — the API explorer](#35-grpcui--the-api-explorer)
4. [The seed system — DEMO_MODE flow](#4-the-seed-system--demo_mode-flow)
5. [Healthchecks](#5-healthchecks)
6. [Networking — how services find each other](#6-networking--how-services-find-each-other)
7. [Volumes and persistence](#7-volumes-and-persistence)
8. [The observability profile (SigNoz)](#8-the-observability-profile-signoz)
9. [The test stack — `docker-compose.test.yml` override](#9-the-test-stack--docker-composetestyml-override)
10. [Tearing down](#10-tearing-down)

---

## 1. The default stack — five services

A vanilla `docker compose up` brings up:

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│   ┌───────────┐   ┌─────────┐   ┌────────┐   ┌──────┐   ┌────────┐   │
│   │  postgres │──>│   api   │<──│  envoy │<──│ web  │   │ grpcui │   │
│   │   :5432   │   │  :50051 │   │  :8080 │   │ :3000│   │  :8082 │   │
│   │           │   │         │   │  :9901 │   │      │   │        │   │
│   └───────────┘   └────┬────┘   └────────┘   └──────┘   └────┬───┘   │
│                        │                                     │       │
│                        └─────── native gRPC ────────────────┘       │
│                                                                      │
│   Default Docker network (library_default by Compose convention)     │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

| Service | Image / build | Host:Container ports | Purpose |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` (image) | `5432:5432` | Database |
| `api` | `./backend` (build) | `50051:50051` | Python gRPC server |
| `envoy` | `envoyproxy/envoy:v1.31-latest` (image) | `8080:8080`, `9901:9901` | gRPC-Web ↔ native gRPC bridge |
| `web` | `./frontend` (build) | `3000:3000` | Next.js dev server |
| `grpcui` | `fullstorydev/grpcui:latest` (image) | `8082:8080` | Interactive gRPC API explorer |

The observability profile adds six more services. See §8.

---

## 2. Boot order and dependency graph

Compose enforces ordering via `depends_on` with `condition`:

```
                ┌────────────┐
                │  postgres  │
                └──────┬─────┘
                       │  service_healthy
                       ▼
                ┌────────────┐
                │    api     │
                └─┬──────────┘
        service_started│      │ service_healthy
          ┌────────────┘      └──────────────┐
          ▼                                  ▼
    ┌────────────┐                    ┌────────────┐
    │   envoy    │                    │   grpcui   │
    └────────────┘                    └────────────┘
          │ service_started
          ▼
    ┌────────────┐
    │    web     │
    └────────────┘
```

What that means in practice:

| Edge | Type | Reasoning |
|---|---|---|
| `postgres → api` | `service_healthy` | api must wait for Postgres to accept connections; otherwise alembic migration fails on first connect |
| `api → envoy` | `service_started` | Envoy's STRICT_DNS resolves `api` lazily, so it doesn't need api to be *healthy*, just *running* (DNS resolves) |
| `envoy → web` | `service_started` | Same — Next.js doesn't error if Envoy isn't yet serving, and the browser-side fetch is what eventually hits Envoy |
| `api → grpcui` | `service_healthy` | grpcui issues a reflection call at startup; if api isn't ready it fails immediately and exits |

**Why `service_started` and not `service_healthy` everywhere?** Because each healthcheck has its own retry budget. Chaining health gates can multiply timeouts (60s × 3 services = 180s minimum boot). Where a "started" container is sufficient (envoy, web), we use that to keep the cold-start fast.

---

## 3. Service-by-service deep dive

### 3.1 `postgres`

```yaml
postgres:
  image: postgres:16-alpine
  environment:
    POSTGRES_USER: postgres
    POSTGRES_PASSWORD: postgres
    POSTGRES_DB: library
  ports:
    - "5432:5432"
  volumes:
    - pgdata:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
    interval: 5s
    timeout: 5s
    retries: 10
    start_period: 10s
```

**Init behavior:** the official postgres image runs init scripts on first container creation against an empty volume. It creates the `library` database, sets up the `postgres` superuser with the given password, and starts listening on `:5432`. On subsequent restarts (with `pgdata` volume already populated), init is skipped.

**Healthcheck:** `pg_isready` is a Postgres-bundled CLI that issues a tiny connection-acceptance probe. The double-`$$` (`$${POSTGRES_USER}`) is Compose-specific escaping — it tells Compose "don't expand this; let the shell inside the container expand it at runtime."

**Volume:** `pgdata` is a Docker named volume. See §7.

### 3.2 `api` — the Python gRPC server

```yaml
api:
  build:
    context: ./backend
  image: neighborhood-library/api:dev
  environment:
    DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/library
    # ... 20+ other env vars (loan policy, fines, DB resilience, OTel, DEMO_MODE)
  ports:
    - "50051:50051"
  depends_on:
    postgres:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "grpc_health_probe", "-addr=127.0.0.1:50051"]
    # ...
```

#### Build context: `./backend/Dockerfile`

The api image is built locally from `./backend`. The Dockerfile is multi-stage:

```
Stage 1 (builder):
  FROM python:3.12-slim
  install uv, copy pyproject.toml + uv.lock
  uv sync --frozen           # populate /app/.venv
  copy src/, alembic/, scripts/, proto stubs (regenerated)
  download grpc_health_probe binary

Stage 2 (runtime):
  FROM python:3.12-slim
  copy /app/.venv from builder
  copy /app/src and other artifacts
  copy /app/scripts (so /app/scripts/sample_client.py works)
  ENTRYPOINT ["/app/entrypoint.sh"]
```

The two-stage approach keeps the runtime image small (no build tools, no source for uv) while still using `uv` for fast, reproducible deps.

The line `image: neighborhood-library/api:dev` tags the locally-built image. The `seed` and `grpcui` services don't depend on this, but having the explicit tag keeps `docker images` readable.

#### `entrypoint.sh` flow

```sh
#!/bin/sh
set -eu

# 1. Apply schema migrations
alembic upgrade head

# 2. (Optional) seed demo data
if [ "${DEMO_MODE:-false}" = "true" ]; then
    python /app/scripts/reset_and_seed.py
fi

# 3. Start the gRPC server
exec python -m library.main
```

The order matters:

1. **Migrations always run.** `alembic upgrade head` is idempotent. It creates the schema on first run and is a no-op on subsequent runs (Alembic uses the `alembic_version` table to track applied revisions).
2. **Seed runs only if `DEMO_MODE=true`.** `scripts/reset_and_seed.py` TRUNCATEs all four tables and reseeds 21 books, 10 members, 11 loans (including 2 with computed fines and 1 currently overdue). Idempotent — re-runs produce identical state.
3. **`exec python -m library.main`** replaces the shell process with the Python process, so signals (SIGTERM, SIGINT) propagate cleanly to the gRPC server's graceful-shutdown handler.

If alembic fails or seed fails, the container exits non-zero. Compose marks it as unhealthy, the dependent containers (envoy, grpcui) wait, and `docker compose logs api` shows what failed.

#### Why this Dockerfile pattern

- **`uv` for installs** — significantly faster than pip + venv at sync time
- **Multi-stage** — runtime image stays slim (~150MB vs ~400MB without separation)
- **`exec` in entrypoint** — proper signal forwarding to Python
- **`grpc_health_probe` baked in** — Docker's healthcheck has zero external deps

See [`design/03-backend.md`](design/03-backend.md) for the deeper backend layout.

### 3.3 `envoy` — the gRPC-Web bridge

```yaml
envoy:
  image: envoyproxy/envoy:v1.31-latest
  ports:
    - "8080:8080"      # gRPC-Web listener (browser-facing)
    - "9901:9901"      # admin endpoint (/ready, /stats, /clusters)
  volumes:
    - ./deploy/envoy/envoy.yaml:/etc/envoy/envoy.yaml:ro
  depends_on:
    api:
      condition: service_started
  healthcheck:
    test: ["CMD", "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/9901"]
    # ...
```

#### Config mount

Envoy doesn't have a runtime API for config — its YAML is loaded once at startup. We mount our `deploy/envoy/envoy.yaml` read-only into the container's expected path (`/etc/envoy/envoy.yaml`). To change Envoy config, edit the file and restart the container:

```sh
docker compose restart envoy
```

#### What's in `envoy.yaml`

Three things, in this order:

1. **Admin endpoint** — `:9901` for ops endpoints (`/ready`, `/stats`, `/listeners`, `/config_dump`)
2. **Listener** — `:8080` for client-facing traffic, with the gRPC-Web filter chain: `envoy.filters.http.grpc_web` → `envoy.filters.http.cors` → `envoy.filters.http.router`
3. **Cluster** — named `library_grpc`, STRICT_DNS resolution to `api:50051`, HTTP/2 forced

For the wire-protocol mechanics (how gRPC-Web ↔ gRPC translation works byte-by-byte) see [`architecture.md` §3](architecture.md#3-wire-protocol--grpc-grpc-web-and-the-envoy-bridge) and [`design/05-infrastructure.md`](design/05-infrastructure.md).

#### Healthcheck — why `bash /dev/tcp`?

The official `envoyproxy/envoy:v1.31-latest` image ships **without `wget`, `curl`, or `nc`** — it's a minimal Distroless-like image. We can't run `wget /ready`. But it does have `bash`, which has a built-in `/dev/tcp/HOST/PORT` pseudo-device that opens a TCP connection.

`exec 3<>/dev/tcp/127.0.0.1/9901` opens a TCP file descriptor to the admin port. If the port is bound, bash exits 0; if not, it exits non-zero. This is a TCP-liveness check, not a full HTTP `/ready` check, but it's sufficient as a startup gate — Envoy doesn't bind admin until config is parsed and clusters are initialized.

(For full diagnostic context on this design choice, see the historical conversation when the test pipeline broke on the wget-missing healthcheck — captured in the test history of this repo.)

### 3.4 `web` — the Next.js frontend

```yaml
web:
  build:
    context: ./frontend
    additional_contexts:
      proto: ./proto
  environment:
    NEXT_PUBLIC_API_BASE_URL: "http://localhost:8080"
  ports:
    - "3000:3000"
  depends_on:
    envoy:
      condition: service_started
```

#### Build context

Two contexts:
1. `./frontend` — the Next.js source tree (Dockerfile, package.json, src/)
2. `./proto` — the proto file, mounted as the named context `proto`

The named context lets the Dockerfile copy the proto file with `COPY --from=proto library/v1/library.proto /tmp/proto/library/v1/library.proto`. This way the frontend image generates its TypeScript stubs from the canonical `proto/library/v1/library.proto`, not from a duplicate inside `frontend/`.

#### Frontend Dockerfile structure

```
FROM node:20-alpine
COPY package.json package-lock.json ./
RUN npm ci
COPY --from=proto library/v1/library.proto src/proto/...
RUN npm run gen:proto                 # buf generate → src/generated/
COPY src/ ./src/
COPY tsconfig.json next.config.ts ...
ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000
CMD ["npm", "run", "dev"]             # `next dev` for development experience
```

Key points:

- **`next dev`, not `next start`** — for the take-home / dev experience, hot reload is more useful than a production build. To run a real `next build && next start` in production, you'd swap the CMD or layer a `target: production` stage.
- **Stub generation at build time** — `npm run gen:proto` runs during `docker build`. This means the proto stubs are baked into the image. Editing the proto requires `docker compose build web` to regenerate.
- **`NEXT_PUBLIC_API_BASE_URL` is baked in at build time** — Next.js inlines `NEXT_PUBLIC_*` vars into the JS bundle during build. Changing it requires a rebuild for production builds. For `next dev` it's read at server-start time.

#### `NEXT_PUBLIC_*` semantics

Variables prefixed with `NEXT_PUBLIC_` are exposed to the browser. Anything else stays server-side. That's why `NEXT_PUBLIC_API_BASE_URL` is named what it is — the browser needs to know where Envoy is to make `fetch()` calls.

The default `http://localhost:8080` works because the browser running on the host can reach the host port `8080` directly. Inside the docker network, the api would be at `api:50051` — but the browser isn't inside the docker network, so it must use the host port mapping.

### 3.5 `grpcui` — the API explorer

```yaml
grpcui:
  image: fullstorydev/grpcui:latest
  command: ["-port=8080", "-plaintext", "-bind=0.0.0.0", "api:50051"]
  ports:
    - "8082:8080"
  depends_on:
    api:
      condition: service_healthy
```

#### What it does

grpcui is a single-binary Go service that:
1. Connects to a gRPC server (`api:50051`) using **server reflection**
2. Discovers every method, message type, enum, and field
3. Renders an HTML form for each method with input fields auto-generated from the protobuf schema
4. Lets you fill in the form and call the RPC; shows the typed response

It's the "Swagger UI for gRPC" — but built around gRPC reflection rather than OpenAPI specs.

#### Why `service_healthy` (not `service_started`) on `api`

grpcui issues a reflection call at startup. If api isn't accepting connections yet, grpcui crashes and exits. `service_healthy` ensures api's gRPC server is registered and the health check passes before grpcui starts.

#### Why it bypasses Envoy

grpcui talks **native gRPC** (HTTP/2 + trailers) directly to the api container. It does NOT go through Envoy. This is by design:
- Envoy is for browser-side gRPC-Web translation
- grpcui is a native gRPC client, doesn't need translation
- Pointing grpcui at Envoy would just add a layer of indirection

For verifying the **browser → Envoy → backend path**, use the React app at `:3000` or `buf curl --protocol=grpcweb`.

---

## 4. The seed system — DEMO_MODE flow

`DEMO_MODE` toggles whether the api container seeds demo data on startup. The full flow:

```
docker compose up -d
(or: DEMO_MODE=true docker compose up -d)
       │
       ▼
api container starts
       │
       ▼
 entrypoint.sh executes
       │
       ▼
 1. alembic upgrade head    ← always
       │
       ▼
 2. if DEMO_MODE=true:
       python /app/scripts/reset_and_seed.py
       │
       ▼
       ┌─────────────────────────────────────────┐
       │ reset_and_seed.py:                       │
       │   - opens AsyncSession                  │
       │   - TRUNCATE all four tables            │
       │     (RESTART IDENTITY CASCADE)          │
       │   - INSERTs 21 books + book_copies      │
       │   - INSERTs 10 members                  │
       │   - INSERTs 11 loans (5 active, 3       │
       │     returned, 2 fined, 1 overdue)       │
       │   - commits                              │
       │   - exits 0 on success, raises on error │
       └─────────────────────────────────────────┘
       │
       ▼
 3. exec python -m library.main
       (gRPC server starts, listens on :50051)
```

### Idempotency

`reset_and_seed.py` uses `TRUNCATE` then `INSERT` — never `UPSERT`. So:
- Running it multiple times produces **identical** state
- All previous data is destroyed each time
- New IDs are assigned fresh starting at 1 (because of `RESTART IDENTITY`)

This is exactly what you want for "predictable demo data": every Playwright run sees the same seeded loan IDs, every screenshot session starts from the same baseline.

### When NOT to use DEMO_MODE

If you've entered any data through the UI that you care about — DON'T enable DEMO_MODE. The next container restart will wipe it. DEMO_MODE is for ephemeral demo / Playwright contexts only.

### How DEMO_MODE doesn't affect the schema

The `alembic upgrade head` step always runs first. The seed runs **after** the schema is current. So the schema is the same whether DEMO_MODE is on or off — only the data differs.

---

## 5. Healthchecks

| Service | Probe | Why this command |
|---|---|---|
| `postgres` | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` | Built into the image; canonical Postgres readiness check |
| `api` | `grpc_health_probe -addr=127.0.0.1:50051` | Bundled into the api image at build time; speaks the standard gRPC health protocol |
| `envoy` | `bash -c "exec 3<>/dev/tcp/127.0.0.1/9901"` | Envoy image has no `wget`/`curl`/`nc`; bash's `/dev/tcp` is the only portable check |
| `web` | (none) | Next.js startup logs are visible; nothing depends on web's health |
| `grpcui` | (none — but depends on api healthy) | grpcui crashes if reflection fails, so its presence in `docker ps` is the signal |

### Healthcheck timing

Each healthcheck has four parameters:

```yaml
healthcheck:
  test: [...]
  interval: 10s        # how often to run the probe after start_period
  timeout: 5s          # max time per probe before considered failed
  retries: 5           # how many consecutive failures before marking unhealthy
  start_period: 10s    # grace period — failures during this window don't count
```

Tuning rule of thumb: `start_period` should accommodate your slowest service's cold-start. For api, that's `alembic upgrade head` + `library.main` boot (typically 5-15s). For envoy, just config parse (~1s).

### What "healthy" gates

- `depends_on: condition: service_healthy` blocks the dependent service from starting
- `docker compose up --wait` returns once all services with healthchecks are healthy (we don't use this in `test.sh` — we have our own wait loop)
- Healthcheck status is visible in `docker compose ps` and `docker inspect`

---

## 6. Networking — how services find each other

Compose creates a **default user-defined network** named after the project (e.g. `neighborhood-library_default`). All services join it. **Service names act as DNS names** within this network.

So when `envoy.yaml` says `address: api`, Envoy resolves `api` via Docker's embedded DNS server to whatever IP the api container has. No hardcoded IPs, no `/etc/hosts` editing needed.

The same applies to `DATABASE_URL` — `postgres:5432` resolves to the postgres container.

### Inside vs outside the network

```
Inside docker network (container-to-container):
  api    → postgres:5432                ← resolves via Docker DNS
  envoy  → api:50051                    ← same
  grpcui → api:50051                    ← same
  web    → (only accessed from outside; doesn't reach into network)

Outside docker network (host browser → container):
  Browser → http://localhost:3000       ← Docker maps host port → web container
  Browser → http://localhost:8080       ← Docker maps host port → envoy container
  grpcurl → localhost:50051             ← Docker maps host port → api container
```

Host port mappings (`5432:5432`, `8080:8080`, etc.) only matter for traffic from your host into the network. Container-to-container traffic uses the internal port.

### Test stack networking

When `test.sh` brings up the test stack with `-p library-test`, it creates a separate network `library-test_default`. Containers in that network resolve service names against each other but cannot see the dev stack's containers. Two parallel, isolated worlds.

---

## 7. Volumes and persistence

Three named volumes (default stack):

```yaml
volumes:
  pgdata:                        # postgres data directory
  signoz-clickhouse:             # observability profile only
  signoz-data:                   # observability profile only
  signoz-zookeeper:              # observability profile only
```

### `pgdata` — the only volume you'll touch in normal use

```yaml
postgres:
  volumes:
    - pgdata:/var/lib/postgresql/data
```

Persists Postgres data files across container restarts. Survives `docker compose down` (which keeps volumes by default). **Does NOT survive `docker compose down -v`** (`-v` removes volumes — full reset).

### When to `down -v`

- You've corrupted your local DB and want a fresh start
- You're testing the migration from scratch
- You want to switch between DEMO_MODE on/off and start clean
- After running tests that left state behind (rare with our test isolation)

### Data inside containers vs in volumes

Anything written to a path that **isn't a mounted volume** lives only in the container's writable layer and is destroyed when the container is removed. Restart preserves it; `docker compose down` (without `-v`) destroys it.

The api and web containers don't use any volumes — their state is the source code (which they don't write to) and ephemeral runtime data. Only postgres has persistence.

---

## 8. The observability profile (SigNoz)

Behind `profiles: ["observability"]` — not started by default. Activate with:

```sh
docker compose --env-file .env.observability --profile observability up -d
```

Adds six containers:

| Service | Image | Role |
|---|---|---|
| `signoz-zookeeper` | `confluentinc/cp-zookeeper` | Coordination service for ClickHouse |
| `signoz-clickhouse` | `clickhouse/clickhouse-server:25.8-alpine` | Columnar storage for traces / logs / metrics |
| `signoz-otel-collector-migrator` | `signoz/signoz-schema-migrator` | One-shot: creates ClickHouse schemas, then exits |
| `signoz-otel-collector` | `signoz/signoz-otel-collector` | OTLP receiver — accepts OTLP from the api on `:4317` (gRPC) and `:4318` (HTTP), batches, writes to ClickHouse |
| `signoz-query-service` | `signoz/query-service` | Reads from ClickHouse, exposes a query API for the SigNoz UI |
| `signoz-frontend` | `signoz/frontend` | The SigNoz web UI on `:3301` |

### Why `.env.observability`

The api needs to flip its OTel export from `console` to `otlp` and point at the local SigNoz collector. The `.env.observability` file does this:

```sh
OTEL_TRACES_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://signoz-otel-collector:4317
```

Compose's `--env-file` flag layers these over the defaults in `docker-compose.yml`. So `--env-file .env.observability --profile observability up` brings up the SigNoz stack AND reconfigures api to ship traces to it.

Without `--profile observability`, the SigNoz services don't start. Without `--env-file .env.observability`, api keeps using the `console` exporter even if the SigNoz stack is up.

### How traces flow when observability is active

```
api → emits OTel spans
        │
        │ OTLP/gRPC over :4317
        ▼
signoz-otel-collector → batches and forwards
        │
        ▼
signoz-clickhouse (writes traces)
        │
        │ queried by SigNoz UI
        ▼
signoz-query-service ← polled by signoz-frontend → http://localhost:3301
```

User opens `http://localhost:3301`, sees traces in real time.

### Tearing down observability

```sh
docker compose --profile observability down -v
```

The `-v` removes the SigNoz volumes (`signoz-clickhouse`, `signoz-data`, `signoz-zookeeper`) so the next `up` starts fresh. Without `-v` they persist between runs.

---

## 9. The test stack — `docker-compose.test.yml` override

A second compose file, `docker-compose.test.yml`, layered on the base via `-p library-test -f docker-compose.yml -f docker-compose.test.yml`. Used by `test.sh` for isolated end-to-end testing.

### What it overrides

Just two things:

1. **Ports shifted +1** — `5433:5432` for postgres, `50052:50051` for api, `8081:8080` and `9902:9901` for envoy, `3001:3000` for web. So the test stack can run alongside the dev stack without port collisions.
2. **`DEMO_MODE: "false"`** — even if your `.env` has `DEMO_MODE=true`, the test stack always starts with empty data. Tests create their own.

### The `!override` trick

Compose's default merge strategy for list-valued fields (like `ports:`) is **concatenation**, not replacement. Without intervention, the test stack would bind BOTH the dev ports AND the test ports.

```yaml
ports: !override
  - "5433:5432"
```

The `!override` YAML tag tells Compose "replace, don't append." Without it, the test stack would conflict with the dev stack's port bindings. (This was a real bug we hit; see the test stack troubleshooting section in `setup.md`.)

### Project name isolation

`-p library-test` causes Compose to:
- Prefix container names: `library-test-postgres-1` instead of `neighborhood-library-postgres-1`
- Create a separate network: `library-test_default`
- Create separate volumes: `library-test_pgdata`

So the test stack and dev stack are fully independent. Bringing one up has zero effect on the other.

### How `test.sh` uses it

```sh
COMPOSE_TEST=(docker compose -p library-test -f docker-compose.yml -f docker-compose.test.yml)
"${COMPOSE_TEST[@]}" up -d --build       # bring up test stack
"${COMPOSE_TEST[@]}" down -v              # tear it down (cleanup trap)
```

For full coverage of test scenarios, see [`test.md`](test.md).

---

## 10. Tearing down

| Command | What happens |
|---|---|
| `docker compose stop` | Stops containers but keeps them around (and keeps volumes) |
| `docker compose down` | Stops + removes containers + removes the default network. **Volumes preserved.** |
| `docker compose down -v` | Same as above PLUS removes named volumes. **Database wiped.** |
| `docker compose down --rmi all` | Same as `down` PLUS removes images. Use to force a fresh image build. |
| `docker compose --profile observability down -v` | Tears down both the default services AND the observability profile services. Without the `--profile` flag, only default-profile services are torn down. |
| `docker compose -p library-test down -v` | Tears down the test stack (separate project from the dev stack). |

### Recommended cleanup sequences

**Done for the day:**
```sh
docker compose down
```

**Want a fresh empty database next time:**
```sh
docker compose down -v
```

**Switching from DEMO_MODE on → off (or vice versa):**
```sh
docker compose down -v && DEMO_MODE=true docker compose up -d
```

**Test stack wasn't cleaned up by `test.sh` for some reason:**
```sh
./test.sh teardown
# (or directly: docker compose -p library-test down -v)
```

---

## Cross-references

- [`setup.md`](setup.md) — operational instructions for both Docker and non-Docker paths
- [`configuration.md`](configuration.md) — every env var documented
- [`architecture.md`](architecture.md) — the umbrella view of components and tech-stack rationale
- [`design/05-infrastructure.md`](design/05-infrastructure.md) — the original spec for the Compose topology
- [`development.md`](development.md) — developer-perspective on dev-loop env overrides
