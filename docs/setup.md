# Setup & Running Locally

How to get the Neighborhood Library application up and running on your machine. This is an **operational guide** — read it once to see the app working in a browser.

> Want to make code changes after seeing it run? See the upcoming "Local Development" section in the README — that's where dev-loop documentation lives.

---

## Two paths

| Path | Effort | When to pick this |
|---|---|---|
| **A. Docker** (recommended) | One command, ~3 minutes first run | You want to see the app working with minimum setup. |
| **B. Fully local (no Docker)** | Install 4 tools, configure 5 env vars, ~30 minutes | You can't run Docker, want air-gapped operation, or want to step into the Python / Node processes with a host-side debugger. |

If in doubt, use Path A. It's the supported flow and what the test suite uses.

---

## Path A — Docker (recommended)

### Prerequisites

| Tool | Why | Install |
|---|---|---|
| **Docker Desktop** (or Docker Engine + Compose plugin) | Runs the four-service stack | https://www.docker.com/products/docker-desktop/ |
| `git` | Clone the repository | Should already be installed; otherwise via your OS package manager |

That's it. **No Python, Node, Postgres, or Envoy on the host required for Path A.** Everything runs inside containers.

### Verify Docker is ready

```sh
docker --version
# → Docker version 24.x.x or later

docker compose version
# → Docker Compose version v2.x.x or later

docker info | grep -i "Server Version"
# → Server Version: 24.x.x  (means the daemon is running and reachable)
```

If `docker info` errors with "Cannot connect to the Docker daemon" — start Docker Desktop and wait for the whale icon to settle.

### Free ports

The stack binds these on your host. Make sure none are in use:

| Port | Used by |
|---|---|
| `3000` | Frontend (Next.js dev server) |
| `5432` | Postgres |
| `8080` | Envoy gRPC-Web listener |
| `9901` | Envoy admin endpoint |
| `50051` | Python gRPC API |

Quick check:

```sh
for p in 3000 5432 8080 9901 50051; do
    lsof -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1 && echo "$p IN USE" || echo "$p free"
done
```

If any port is in use, stop whatever owns it (often a previous Postgres install or another project's compose stack).

### Bring it up

```sh
git clone <repo-url> neighborhood-library
cd neighborhood-library

# (Optional) copy the env template for any overrides you want to apply
cp .env.example .env

# Production-style: empty database, no demo data
docker compose up -d
```

First run takes ~3-5 minutes — Docker pulls Postgres + Envoy + grpcui images and builds the api / web images. Subsequent runs are ~30 seconds.

> **Env vars:** every variable is documented in [`.env.example`](../.env.example) with its default and "when you'd change this." `docker compose up` automatically loads `.env` (gitignored) from the repo root if present. If you don't create `.env`, the defaults baked into `docker-compose.yml` apply — the stack works either way. See the [Configuration overrides](#configuration-overrides) section below for the patterns.

### Watch it boot

```sh
docker compose logs -f api
```

You're waiting for these lines (in order):

```
library api: running alembic upgrade head
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial schema...
library api: starting gRPC server
... library.main library api: listening on :50051
```

`Ctrl+C` to detach (containers keep running).

### Confirm everything is healthy

```sh
docker compose ps
```

All four services should show `Up` with `(healthy)` (postgres, api, envoy) or just `Up` (web — it has no healthcheck):

```
NAME                          STATUS                    PORTS
neighborhood-library-api-1    Up 30s (healthy)          0.0.0.0:50051->50051/tcp
neighborhood-library-envoy-1  Up 30s (healthy)          0.0.0.0:8080->8080/tcp, 0.0.0.0:9901->9901/tcp
neighborhood-library-postgres-1  Up 35s (healthy)       0.0.0.0:5432->5432/tcp
neighborhood-library-web-1    Up 30s                    0.0.0.0:3000->3000/tcp
```

### See it running

Open http://localhost:3000 — you'll see the dashboard with five empty tiles (0 books, 0 members, 0 active loans, 0 overdue, $0 fines).

That's it: **the app is up.** You can now click around — `Books → New` to add a title, `Members → New` to register a patron, `Loans → New` to record a borrow.

### Explore the API interactively

For a Swagger-style web UI to call gRPC methods directly, open **http://localhost:8082** — that's `grpcui`, an interactive API explorer that uses server reflection to discover every method, render input forms, and let you invoke RPCs. Useful for poking at the backend without writing code.

> grpcui talks native gRPC to the api container directly (not through Envoy), so it bypasses the gRPC-Web translation. It's for API exploration; for wire-path verification use `grpcurl` against `:8080` (Envoy) instead.

### (Optional) Bring up with demo data

If you'd rather see the app populated, set `DEMO_MODE=true`:

```sh
docker compose down -v        # wipe any existing state
DEMO_MODE=true docker compose up -d
```

This truncates all tables and seeds 21 books, 10 members, 11 loans (including 2 with computed fines and 1 currently overdue) on every startup. Useful for visualizing how the dashboard, filters, and fines tile look populated.

To switch back to empty production-style: `docker compose down -v && docker compose up -d`.

### (Optional) Try the sample client

A heavily-commented Python script that demonstrates the API end-to-end:

```sh
docker compose exec api python /app/scripts/sample_client.py
```

Output walks through: create member → create book → borrow → list active → return → list active. Useful as a self-documenting "what does a real client look like" example.

### Tearing down

```sh
docker compose down            # stop containers (keeps the DB volume)
docker compose down -v         # stop + delete the DB volume (full reset)
```

---

## Configuration overrides

The repo ships an [`.env.example`](../.env.example) at the project root that documents every environment variable across the stack. To apply overrides:

```sh
# Copy the template — `.env` is gitignored
cp .env.example .env

# Edit whatever you want to change
$EDITOR .env

# Bring up the stack — Compose auto-loads .env from the project root
docker compose up -d
```

You can also pass overrides ad-hoc on the command line:

```sh
DEMO_MODE=true docker compose up -d
DEFAULT_LOAN_DAYS=0 FINE_GRACE_DAYS=0 docker compose up -d api    # surface fines instantly
```

For Path B (local dev without Docker), env vars must be set in your shell before running the backend:

```sh
cd backend
source ../.env                               # if you've created one
# or export individual vars:
export DEFAULT_LOAN_DAYS=0
export OTEL_TRACES_EXPORTER=none
uv run python -m library.main
```

The `library.config.Settings` (Pydantic Settings) class is the source of truth for what's settable. It also reads from a `backend/.env` file via `python-dotenv`, which can be useful if you want backend-only overrides separate from Compose:

```sh
cp .env.example backend/.env                 # backend-specific overrides
```

For a developer-focused breakdown of which env vars matter during which kinds of work (testing fines, surfacing timeouts, debugging traces, etc.), see [`development.md` §5](development.md#5-development-time-configuration).

---

## Path B — Fully local (without Docker)

For when you cannot use Docker. More installation, more configuration, more things that can go wrong. Use Path A unless you specifically can't.

### Prerequisites

You'll need **all** of these on your host:

| Tool | Version | Install | Why |
|---|---|---|---|
| **PostgreSQL** | 16+ | macOS: `brew install postgresql@16`. Linux: package manager. Windows: https://www.postgresql.org/download/windows/ | Database |
| **Python** | 3.12+ | https://www.python.org/downloads/ — or via your OS package manager | Backend runtime |
| **`uv`** | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (or `brew install uv`) | Python package + venv manager |
| **Node.js** | 20+ | https://nodejs.org/ (use `nvm` if you have multiple versions) | Frontend runtime |
| **Envoy** | 1.31+ | macOS: `brew install envoy`. Linux: https://www.envoyproxy.io/docs/envoy/latest/start/install | gRPC-Web bridge |
| **`grpcui`** *(optional)* | Latest | macOS: `brew install grpcui`. Linux: `go install github.com/fullstorydev/grpcui/cmd/grpcui@latest` (requires Go), or download a release binary from https://github.com/fullstorydev/grpcui/releases | Interactive gRPC API explorer (Step 6 below). The Docker stack ships this for you; for Path B install it separately or skip it. |

You can sanity-check each:

```sh
psql --version          # → psql (PostgreSQL) 16.x or later
python3 --version       # → Python 3.12.x or later
uv --version            # → uv x.y.z
node --version          # → v20.x.x or later
npm --version
envoy --version         # → envoy version: ... 1.31.x
grpcui --version        # → grpcui x.y.z   (optional)
```

### Step 1 — Bring up Postgres

```sh
# macOS Homebrew (brew install postgresql@16)
brew services start postgresql@16

# Linux systemd
sudo systemctl start postgresql

# Verify it's listening
psql -h localhost -p 5432 -U postgres -c '\l'
# (you may need to set up a `postgres` user with password `postgres` if it doesn't exist)
```

Create the application database:

```sh
psql -h localhost -p 5432 -U postgres -c "CREATE DATABASE library;"
```

### Step 2 — Set up the backend

```sh
cd backend

# Creates .venv with all dependencies
uv sync --extra dev

# Generate the gRPC Python stubs
uv run bash scripts/gen_proto.sh

# Set the connection URL — adjust user/password if yours differ
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/library"

# (Optional) override defaults
export GRPC_PORT=50051
export DEFAULT_LOAN_DAYS=14

# Run the migrations
uv run alembic upgrade head

# Start the gRPC server
uv run python -m library.main
```

You'll see:

```
... library.main library api: listening on :50051
```

Leave this running in its own terminal.

### Step 3 — Bring up Envoy

In a new terminal, run Envoy with the project's config:

```sh
cd /path/to/neighborhood-library
envoy -c deploy/envoy/envoy.yaml
```

> **Wait** — the `envoy.yaml` references the upstream cluster as `address: api`. That's the Docker service name; it won't resolve on your host. Edit `deploy/envoy/envoy.yaml` line ~52 to change `address: api` → `address: 127.0.0.1` *just for this Path B run*. Don't commit the change. (Alternative: add `127.0.0.1 api` to your `/etc/hosts` so the name resolves.)

Once running, Envoy serves on `:8080` (gRPC-Web) and `:9901` (admin). Verify:

```sh
curl http://localhost:9901/ready
# → LIVE
```

### Step 4 — Set up the frontend

In a new terminal:

```sh
cd frontend

# Install dependencies
npm install

# Generate TypeScript stubs from the proto
npm run gen:proto

# Point the client at Envoy on your host
export NEXT_PUBLIC_API_BASE_URL="http://localhost:8080"

# Start Next.js dev server
npm run dev
```

Output:

```
   ▲ Next.js 16.x.x
   - Local:        http://localhost:3000
```

### Step 5 — See it running

Open http://localhost:3000 — same dashboard as Path A. The app is now running with:
- Backend gRPC server: your host Python at `:50051`
- Postgres: your host install at `:5432`
- Envoy: your host install at `:8080` and `:9901`
- Next.js: your host Node at `:3000`

### Step 6 *(optional)* — API explorer with grpcui

For the Swagger-style interactive UI to call gRPC methods directly, run grpcui pointed at your local backend. Three options, pick whichever you prefer:

**Option 1 — host install (cleanest if you have it)**

```sh
grpcui -plaintext -bind=0.0.0.0 -port=8082 localhost:50051
# → gRPC Web UI available at http://127.0.0.1:8082/
```

`brew install grpcui` on macOS, or `go install github.com/fullstorydev/grpcui/cmd/grpcui@latest` if you have Go (requires `$GOPATH/bin` on PATH), or download a release binary from https://github.com/fullstorydev/grpcui/releases.

**Option 2 — run grpcui in Docker even when the rest is local**

If you'd rather not install grpcui on the host:

```sh
docker run --rm -it --name grpcui \
    -p 8082:8080 \
    fullstorydev/grpcui:latest \
    -port=8080 -plaintext -bind=0.0.0.0 \
    host.docker.internal:50051
```

`host.docker.internal` resolves to the host machine from inside Docker Desktop containers (works on macOS and Windows). On Linux Docker, use `--network=host` and connect to `localhost:50051` instead.

**Option 3 — skip it**

You don't need grpcui to use the app. `grpcurl` is a command-line alternative that's also useful and may already be installed (`brew install grpcurl`). For example:

```sh
grpcurl -plaintext localhost:50051 list
# → library.v1.BookService, library.v1.MemberService, library.v1.LoanService
grpcurl -plaintext -d '{"page_size": 10}' localhost:50051 library.v1.BookService/ListBooks
```

Either way, the app at http://localhost:3000 is fully functional without grpcui.

### Tearing down (Path B)

In each terminal, `Ctrl+C` to stop the running process. Stop Postgres if you don't want it running:

```sh
brew services stop postgresql@16    # macOS
sudo systemctl stop postgresql      # Linux
```

To clean the database without dropping it:

```sh
cd backend
uv run alembic downgrade base
```

Or drop and recreate:

```sh
psql -h localhost -p 5432 -U postgres -c "DROP DATABASE library;"
psql -h localhost -p 5432 -U postgres -c "CREATE DATABASE library;"
```

---

## Hybrid path — Postgres + Envoy in Docker, app locally

If you want most of Path B's flexibility without installing Postgres and Envoy on your host:

```sh
# Bring up only the infrastructure containers (skip api, web, grpcui)
docker compose up -d postgres envoy

# Run the backend on your host (in one terminal)
cd backend
uv sync --extra dev
uv run bash scripts/gen_proto.sh
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/library"
uv run alembic upgrade head
uv run python -m library.main

# Run the frontend on your host (in another terminal)
cd frontend
npm install
npm run gen:proto
export NEXT_PUBLIC_API_BASE_URL="http://localhost:8080"
npm run dev
```

This is the **most useful path for local debugging**: Docker handles the persistent and config-heavy services (Postgres, Envoy), while your IDE / debugger has direct visibility into the Python and Node processes you're iterating on.

The same `address: api` issue from Path B applies — except in this hybrid case, you keep Envoy in Docker (which can resolve `api`), and Envoy points at... hmm. **In this hybrid, your local backend isn't reachable from the Envoy container as `api`.** Two options:

1. **Run Envoy on your host too** (drop back to Path B for Envoy specifically): edit `deploy/envoy/envoy.yaml` → `address: 127.0.0.1`, run `envoy -c deploy/envoy/envoy.yaml`
2. **Use `host.docker.internal`** in `envoy.yaml` (Docker Desktop only): edit `address: host.docker.internal`. The Envoy container will route to your host's `:50051`.

Option 2 is the cleanest tweak.

**API explorer in hybrid mode:** the Compose `grpcui` service points at `api:50051` (the Compose service name), but in hybrid you're not running the api container. Two options:

- Bring up grpcui in Docker pointed at the host: `docker run --rm -p 8082:8080 fullstorydev/grpcui:latest -port=8080 -plaintext -bind=0.0.0.0 host.docker.internal:50051`
- Or run grpcui on the host: `grpcui -plaintext localhost:50051 -port=8082` (after `brew install grpcui`)

Either way, http://localhost:8082 ends up at the API explorer.

---

## What you should see when it's working

Regardless of path:

| URL / Command | Expected |
|---|---|
| http://localhost:3000 | Next.js dashboard with five count tiles |
| http://localhost:3000/books | Empty paginated table; "+ New book" button top-right |
| http://localhost:9901/ready | `LIVE` (Envoy admin) |
| http://localhost:8082 *(if grpcui is running)* | gRPC explorer page listing every method on `library.v1.BookService`, `library.v1.MemberService`, and `library.v1.LoanService` |
| `grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check` | `{"status": "SERVING"}` |
| `psql -h localhost -U postgres -d library -c '\dt'` | Five tables: alembic_version, book_copies, books, loans, members |

If all five check out, the stack is genuinely operational.

---

## Common setup issues

### "Bind for 0.0.0.0:8080 failed: port is already allocated"

Some other process owns one of the project's ports. Find it:

```sh
lsof -iTCP:8080 -sTCP:LISTEN
```

Stop that process. If it's another Compose stack, `docker compose down` in that project. If it's a stray process, kill it.

### "container ... is unhealthy"

A container's healthcheck is failing. Check its logs:

```sh
docker compose logs api      # or envoy, postgres, web
```

Most common causes:
- **postgres unhealthy**: rare; usually fixes itself after ~10 seconds. If persistent, the Docker daemon may be low on resources.
- **api unhealthy**: usually means alembic migration failed. Logs will show why (often a stale volume from a previous incompatible schema — `docker compose down -v` and retry).
- **envoy unhealthy on first install**: confirm Envoy is actually starting. The official image is minimal — our healthcheck uses `bash /dev/tcp/127.0.0.1/9901` which works without curl/wget.

### "ModuleNotFoundError: No module named 'grpc_tools'" (Path B)

You're running `python` from outside the venv. Use `uv run python ...` or activate the venv with `source .venv/bin/activate`.

### `npm install` fails

Usually a Node version mismatch. Check `node --version` matches what `frontend/package.json` `engines` requires (Node 20+). Use `nvm install 20 && nvm use 20` to switch.

### Browser shows "Cannot reach the library service"

Frontend can't talk to Envoy. Verify `NEXT_PUBLIC_API_BASE_URL` matches your actual Envoy host:port and that Envoy is running:

```sh
curl http://localhost:8080
# → 404 (correct — Envoy is up but no path matches)
```

If you get connection refused, Envoy isn't running.

### Docker is slow / out of memory

The default Docker Desktop allocation (4 GB) is enough for the 4-service stack. Bumping to 6-8 GB helps if you also run the SigNoz observability profile. macOS: Docker Desktop → Settings → Resources → Memory.

---

## What's next

Once you have the app running:

| You want to... | Read |
|---|---|
| Make a code change and run tests | [`test.md`](test.md) — the test runner has scenarios for unit, integration, e2e, and a "stack" mode that brings up the system without tests |
| Understand how the pieces fit together | [`architecture.md`](architecture.md) — components, gRPC-Web translation, tech-stack rationale |
| See the source-of-truth design docs | [`design/`](design/) — five focused docs (database, API contract, backend, frontend, infrastructure) |
