# Testing Guide

Everything a developer needs to run before checking in. The `test.sh` script at the repo root is the single entry point — it can run the entire pipeline or any individual layer.

This guide covers:
- [Quick reference](#quick-reference) — one-liner commands per task
- [What each test layer does](#what-each-test-layer-does)
- [Common dev workflows](#common-dev-workflows)
- [Playwright in-depth](#playwright-in-depth) — headed mode, debug mode, traces
- [Manual API verification](#manual-api-verification) — `grpcurl`, `buf curl`, raw `curl`
- [Troubleshooting](#troubleshooting)

---

## Quick reference

Run from the repo root unless noted otherwise.

| Goal | Command |
|---|---|
| **Everything before pushing a PR** | `./test.sh` |
| Backend unit tests only (~5s) | `./test.sh unit` |
| Backend integration tests only (~30s) | `./test.sh integration` |
| Frontend TypeScript check only (~10s) | `./test.sh ts` |
| Sample client smoke test | `./test.sh sample` |
| Playwright happy-path (headless) | `./test.sh e2e` |
| Playwright with visible browser | `./test.sh e2e --headed` |
| Playwright with step-debugger | `./test.sh e2e --debug` |
| Bring up a test stack and leave it running | `./test.sh stack` |
| Tear down a leftover test stack | `./test.sh teardown` |
| Show all options | `./test.sh --help` |

The `e2e` and `sample` scenarios automatically bring up an isolated test stack (compose project `library-test` on +1 ports) and tear it down on exit — even on failure or Ctrl+C.

---

## What each test layer does

### `unit` — backend pure-function tests

```sh
./test.sh unit
```

Lives in `backend/tests/unit/`. Tests pure-function logic: the fine formula, retry classifier, exponential backoff, loan state-transition helpers. **No database, no gRPC, no containers** — just Python imports and assertions.

- Runtime: ~3-5 seconds
- Prereqs: `uv` on PATH
- When to run: every code change. This is the fastest feedback loop.

### `integration` — backend gRPC + DB tests

```sh
./test.sh integration
```

Lives in `backend/tests/integration/`. Spins up an in-process gRPC server in pytest, plus an ephemeral Postgres container via `testcontainers-postgres`. ~130 tests covering CRUD, validation, error mapping, copy reconciliation, the partial-unique-index double-borrow guard, and the borrow/return state machine.

- Runtime: ~10s warm cache, ~60s cold (image pull + first build)
- Prereqs: `uv` + Docker daemon running
- When to run: after touching service / repository / DB code, or before any commit that changes business logic
- Data: testcontainer is destroyed on exit — never touches your dev `pgdata` volume

### `ts` — frontend TypeScript check

```sh
./test.sh ts
```

Runs `npx tsc --noEmit` in the frontend. Catches type errors, missing imports, generated-stub mismatches.

- Runtime: ~10s
- Prereqs: `npm` on PATH (will run `npm install` if `node_modules` is missing)
- When to run: after editing `.ts`/`.tsx`, or after `.proto` changes (regenerated stubs may have new types)

### `sample` — sample client smoke test

```sh
./test.sh sample
```

Brings up the test stack, runs `backend/scripts/sample_client.py` against it (native gRPC), tears the stack down. The sample client walks: create member → create book → borrow → list active → return → list active.

- Runtime: ~45s (mostly stack boot)
- Prereqs: `uv`, Docker daemon, ports 5433/50052/8081/9902/3001 free
- When to run: after a change that affects the gRPC API surface (proto, servicer, errors)
- Data: test stack is fully isolated and destroyed on exit

### `e2e` — Playwright happy-path

```sh
./test.sh e2e                # headless (default)
./test.sh e2e --headed       # visible browser
./test.sh e2e --debug        # step-by-step inspector
```

Brings up the test stack, runs the single happy-path Playwright spec at `frontend/e2e/happy-path.spec.ts`. The spec drives the full UI: dashboard → create book → create member → borrow → return → verify.

- Runtime: ~90s (stack boot + chromium + the test itself)
- Prereqs: `npm`, Docker daemon, ports free; first run downloads chromium browser binaries (~150 MB)
- When to run: before merging UI changes, or to verify wire-level integration end-to-end through the browser
- Data: test stack destroyed on exit; test creates uniquely-suffixed records so re-runs don't collide

### `stack` — bring up the test stack and leave it

```sh
./test.sh stack
```

Boots the isolated test stack (`library-test` project) on the +1 ports and **does NOT tear it down**. Useful for ad-hoc exploration: connect with `psql`, hit the API with `grpcurl`, run `npm run dev` against it, etc.

- Runtime: ~30s to bring up
- Tear down with: `./test.sh teardown`

When `stack` succeeds, the script prints the live URLs:

```
Postgres        localhost:5433     (user/pw: postgres / postgres, db: library)
API gRPC        localhost:50052    (native gRPC; reflection enabled)
Envoy listener  localhost:8081     (gRPC-Web)
Envoy admin     localhost:9902     (try /ready or /stats)
Web UI          http://localhost:3001
```

### `teardown` — clean up a leftover stack

```sh
./test.sh teardown
```

Runs `docker compose -p library-test ... down -v --remove-orphans`. Idempotent — safe to run even if no test stack is up. Use this after `./test.sh stack` or to recover from any previous abrupt exit.

---

## Common dev workflows

### "I just want to push my PR with confidence"

```sh
./test.sh
```

Full pipeline. Takes ~3-5 minutes on a warm cache; ~5-10 on a cold first run (Docker image builds + chromium download). Fail-fast — first red step aborts and tears down.

### "I'm iterating on a backend RPC"

```sh
# Tight inner loop:
./test.sh integration

# When the integration suite is green and you want a final smoke test:
./test.sh sample
```

### "I'm iterating on a frontend page"

Two terminals:

```sh
# Terminal 1 — keep an isolated stack running
./test.sh stack

# Terminal 2 — run the dev server pointing at the test stack
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8081 npm run dev
# Open http://localhost:3000 (yes, dev server on :3000 even though stack web is on :3001)
```

When done:

```sh
./test.sh teardown
```

This pattern keeps the test stack stable while you iterate on the frontend, with no risk of corrupting your dev DB.

### "I'm chasing a Playwright flake"

```sh
./test.sh e2e --debug
```

Opens Playwright's inspector. Each line in the test is a step you can pause at; you can click in the browser to see the locator picker. Best for figuring out why a selector isn't matching.

For a less interactive but more reproducible debug:

```sh
./test.sh e2e --headed
```

Runs the test at normal speed but with a visible browser so you can watch what happens.

### "I changed the .proto"

After editing `proto/library/v1/library.proto`:

```sh
# Regenerate stubs on both sides
( cd backend && uv run bash scripts/gen_proto.sh )
( cd frontend && npm run gen:proto )

# Then run the full test suite
./test.sh
```

The TypeScript check (`./test.sh ts`) will catch frontend type drift; the integration tests will catch backend drift.

### "I want to inspect an integration test failure"

```sh
cd backend
uv run pytest tests/integration/test_books.py::test_create_book_happy_path -v -s
```

The `-s` flag disables pytest's stdout capture so `print()` and log statements show up.

To run a single test in isolation under a debugger:

```sh
uv run python -m pytest tests/integration/test_books.py::test_create_book_happy_path -v -s --pdb
```

---

## Playwright in-depth

### View the HTML report from the last run

After any `e2e` run (success or failure), Playwright writes a report to `frontend/playwright-report/`:

```sh
cd frontend
npx playwright show-report
```

Opens an interactive browser report with:
- Pass/fail timeline
- Failure stack trace
- Step-by-step actions
- DOM snapshots before/after each step
- Network log

### Capture a trace

A failed test automatically saves:
- `frontend/test-results/<test>/error-context.md` — the failure details
- `frontend/test-results/<test>/test-failed-1.png` — final screenshot
- `frontend/test-results/<test>/video.webm` — full session video
- `frontend/test-results/<test>-retry1/trace.zip` — full action trace (only if a retry happened)

To open a trace.zip interactively:

```sh
cd frontend
npx playwright show-trace test-results/<dir>/trace.zip
```

The trace UI lets you scrub through the test, see DOM at each step, view console + network logs. Best for "the test failed but I can't tell why" diagnostics.

### Run a specific test (one of many)

The repo only ships one happy-path test today, but if you add more:

```sh
cd frontend
PLAYWRIGHT_BASE_URL=http://localhost:3001 npx playwright test happy-path
PLAYWRIGHT_BASE_URL=http://localhost:3001 npx playwright test --grep "borrow"
```

(Requires the test stack to already be up — easiest is `./test.sh stack` in another terminal.)

---

## Troubleshooting

### "port already in use"

`./test.sh` checks ports 5433, 50052, 8081, 9902, 3001 before booting. If something's listening on one of them:

```sh
# Find the offending process
lsof -iTCP:8081 -sTCP:LISTEN

# Likely culprit: a previous test stack that didn't tear down cleanly
./test.sh teardown
```

### "container library-test-* is unhealthy"

The healthcheck for that service is failing. Inspect its logs:

```sh
docker compose -p library-test logs <service-name>
# e.g. docker compose -p library-test logs envoy
```

Common causes:
- envoy unhealthy in old configs: the official envoy image lacks `wget`. Our healthcheck uses `bash /dev/tcp` — if you forked it, make sure it doesn't reference `wget` or `curl`.
- api unhealthy: usually means alembic migration failed or DB isn't reachable. Check both `api` and `postgres` logs.

### Playwright can't connect

```
Error: page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:3001/
```

Means the `web` service isn't up (or `PLAYWRIGHT_BASE_URL` is wrong). Confirm:

```sh
curl -sf http://localhost:3001
docker compose -p library-test ps
```

### Integration tests fail on a fresh clone

testcontainers needs Docker running and the ability to reach Docker Hub for the `postgres:16-alpine` pull. First run is slow (~30s) while it pulls; subsequent runs are fast. If the pull is blocked (proxy / firewall), pull it manually:

```sh
docker pull postgres:16-alpine
```

### "uv: command not found"

Install uv:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
# or
brew install uv
```

### Tests pass locally but fail in CI

Most common causes:
- CI's Docker has less memory; integration tests / testcontainers may time out. Bump the runner's resources.
- Port conflicts on a shared CI runner. Use `./test.sh teardown` as a CI pre-step.
- Browser binaries not installed on the runner. The script runs `npx playwright install chromium` automatically, but in some sandboxes that fails — install during image build instead.

### "Test stack didn't tear down after Ctrl+C"

The trap should always fire, but if for some reason it didn't:

```sh
./test.sh teardown
```

Also confirms by listing leftover containers:

```sh
docker compose -p library-test ps
```

Should be empty after teardown.

---

## Reference: what runs where

| Layer | Runs on host? | Runs in container? | Spawns containers? |
|---|---|---|---|
| Unit tests | Python in host venv | — | — |
| Integration tests | pytest in host venv | — | Yes (testcontainers Postgres) |
| TS check | tsc on host | — | — |
| Sample client | Python in host venv | — | Test stack (4 containers) |
| Playwright | node + chromium on host | — | Test stack (4 containers) |
| `stack` scenario | docker compose CLI | — | Test stack (4 containers, kept alive) |

The system-under-test is always in containers (production-shaped). The runners (pytest, sample client, Playwright) are host processes that connect into the containers via host port mappings. The test stack uses compose project `library-test` with +1 ports (5433, 50052, 8081, 9902, 3001) for full isolation from the dev stack.
