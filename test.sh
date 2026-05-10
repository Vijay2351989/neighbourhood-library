#!/usr/bin/env bash
# test.sh — parameterized test runner against an isolated test stack.
#
# Run `./test.sh --help` for usage. Quick reference:
#
#   ./test.sh                     # full pipeline (unit + integration + ts + sample + e2e)
#   ./test.sh unit                # backend unit tests only (no Docker)
#   ./test.sh integration         # backend integration tests only (testcontainers)
#   ./test.sh ts                  # frontend TypeScript check only
#   ./test.sh sample              # sample client (brings stack up + tears down)
#   ./test.sh e2e                 # Playwright happy-path (brings stack up + tears down)
#   ./test.sh e2e --headed        # Playwright with visible browser
#   ./test.sh e2e --debug         # Playwright with inspector + breakpoints
#   ./test.sh stack               # just bring up the stack and leave it running
#   ./test.sh teardown            # tear down any leftover test stack
#
# Data isolation:
#   Integration tests use testcontainers (independent ephemeral Postgres).
#   Sample/e2e/stack scenarios use compose project `library-test` on +1 ports
#   (5433, 50052, 8081, 9902, 3001) — fully separate from the dev stack.
#   On exit (success, failure, Ctrl+C), the stack is torn down via trap unless
#   the `stack` scenario was selected (which intentionally leaves it running).
#
# Prereqs: docker daemon running; uv, npm, curl on PATH.

set -euo pipefail

# ============================================================================
# config + styling
# ============================================================================

TEST_PROJECT="library-test"
COMPOSE_TEST=(docker compose -p "$TEST_PROJECT" -f docker-compose.yml -f docker-compose.test.yml)

TEST_PG_HOST_PORT=5433
TEST_API_HOST_PORT=50052
TEST_ENVOY_HOST_PORT=8081
TEST_ENVOY_ADMIN_PORT=9902
TEST_WEB_HOST_PORT=3001

if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RED=$(printf '\033[31m')
    GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); BLUE=$(printf '\033[34m')
    RESET=$(printf '\033[0m')
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

section() { echo ""; echo "${BOLD}${BLUE}===== $* =====${RESET}"; }
ok()      { echo "${GREEN}✓${RESET} $*"; }
warn()    { echo "${YELLOW}!${RESET} $*"; }
err()     { echo "${RED}✗${RESET} $*" >&2; }

usage() {
    cat <<EOF
${BOLD}test.sh${RESET} — parameterized test runner

${BOLD}USAGE${RESET}
    ./test.sh [SCENARIO] [OPTIONS]

${BOLD}SCENARIOS${RESET}
    (none)        Full pipeline: unit + integration + ts + sample + e2e (default).
    unit          Backend unit tests only. No Docker.
    integration   Backend integration tests only. Uses testcontainers Postgres.
    ts            Frontend TypeScript type-check only.
    sample        Sample client cycle. Brings up test stack + tears down.
    e2e           Playwright happy-path. Brings up test stack + tears down.
    stack         Bring up the test stack and leave it running. No tests, no cleanup.
                  Use for manual exploration. Tear down with: ./test.sh teardown.
    teardown      Tear down any leftover test stack (volume destroyed). Idempotent.

${BOLD}OPTIONS${RESET}
    --headed      For e2e: run Playwright with a visible browser window.
    --debug       For e2e: run Playwright with the inspector (pauses on each step).
    -h, --help    Show this help.

${BOLD}EXAMPLES${RESET}
    ./test.sh                       # everything, in order, fail-fast
    ./test.sh unit                  # ~5s pure-function check
    ./test.sh integration           # ~30s with testcontainer setup
    ./test.sh e2e --headed          # watch the browser drive the UI
    ./test.sh stack                 # leave a test stack running for ad-hoc work
    ./test.sh teardown              # clean up after a "stack" session

${BOLD}TEST STACK PORTS (when running)${RESET}
    Postgres        :$TEST_PG_HOST_PORT
    API gRPC        :$TEST_API_HOST_PORT
    Envoy listener  :$TEST_ENVOY_HOST_PORT  (gRPC-Web)
    Envoy admin     :$TEST_ENVOY_ADMIN_PORT
    Next.js web     :$TEST_WEB_HOST_PORT
EOF
}

# ============================================================================
# arg parsing
# ============================================================================

SCENARIO="full"
PLAYWRIGHT_EXTRA_ARGS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --headed)  PLAYWRIGHT_EXTRA_ARGS+=("--headed"); shift ;;
        --debug)   PLAYWRIGHT_EXTRA_ARGS+=("--debug"); shift ;;
        full|unit|integration|ts|sample|e2e|stack|teardown)
            SCENARIO="$1"; shift ;;
        *) err "unknown argument: $1"; echo ""; usage; exit 2 ;;
    esac
done

# Decide which steps to run + whether the stack is needed.
RUN_UNIT=0; RUN_INTEGRATION=0; RUN_TS=0; RUN_SAMPLE=0; RUN_E2E=0
NEEDS_STACK=0; KEEP_STACK=0

case "$SCENARIO" in
    full)        RUN_UNIT=1; RUN_INTEGRATION=1; RUN_TS=1; RUN_SAMPLE=1; RUN_E2E=1; NEEDS_STACK=1 ;;
    unit)        RUN_UNIT=1 ;;
    integration) RUN_INTEGRATION=1 ;;
    ts)          RUN_TS=1 ;;
    sample)      RUN_SAMPLE=1; NEEDS_STACK=1 ;;
    e2e)         RUN_E2E=1; NEEDS_STACK=1 ;;
    stack)       NEEDS_STACK=1; KEEP_STACK=1 ;;
    teardown)    : ;;  # handled below
esac

# ============================================================================
# script-relative working directory
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ============================================================================
# teardown — special case, runs and exits
# ============================================================================

if [ "$SCENARIO" = "teardown" ]; then
    section "Tearing down test stack (if present)"
    "${COMPOSE_TEST[@]}" down -v --remove-orphans 2>&1 | tail -5 || true
    ok "teardown complete"
    exit 0
fi

# ============================================================================
# cleanup trap (skipped if we're keeping the stack alive)
# ============================================================================

cleanup() {
    local exit_code=$?
    if [ "$KEEP_STACK" = "1" ] && [ $exit_code -eq 0 ]; then
        # Successful `stack` scenario — print URLs and exit without tearing down.
        echo ""
        echo "${BOLD}${GREEN}Test stack is up and running.${RESET}"
        echo ""
        echo "  Postgres        ${BOLD}localhost:$TEST_PG_HOST_PORT${RESET}     (user/pw: postgres / postgres, db: library)"
        echo "  API gRPC        ${BOLD}localhost:$TEST_API_HOST_PORT${RESET}    (native gRPC; reflection enabled)"
        echo "  Envoy listener  ${BOLD}localhost:$TEST_ENVOY_HOST_PORT${RESET}     (gRPC-Web)"
        echo "  Envoy admin     ${BOLD}localhost:$TEST_ENVOY_ADMIN_PORT${RESET}     (try /ready or /stats)"
        echo "  Web UI          ${BOLD}http://localhost:$TEST_WEB_HOST_PORT${RESET}"
        echo ""
        echo "Tear down with: ${BOLD}./test.sh teardown${RESET}"
        exit 0
    fi
    if [ "$NEEDS_STACK" = "1" ]; then
        section "Cleanup: tearing down isolated test stack"
        "${COMPOSE_TEST[@]}" down -v --remove-orphans 2>/dev/null || true
    fi
    if [ $exit_code -eq 0 ]; then
        echo ""
        echo "${BOLD}${GREEN}✓ ALL CHECKS PASSED${RESET}"
    else
        echo ""
        echo "${BOLD}${RED}✗ TEST PIPELINE FAILED (exit $exit_code)${RESET}"
    fi
    exit $exit_code
}
trap cleanup EXIT

# ============================================================================
# preflight
# ============================================================================

section "Preflight"
command -v docker >/dev/null || { err "docker not on PATH"; exit 1; }
docker info >/dev/null 2>&1  || { err "docker daemon not reachable"; exit 1; }

# Tool checks scoped to the scenario — don't require uv if you're only running ts.
if [ "$RUN_UNIT" = "1" ] || [ "$RUN_INTEGRATION" = "1" ] || [ "$RUN_SAMPLE" = "1" ]; then
    command -v uv >/dev/null || { err "uv not on PATH (https://astral.sh/uv)"; exit 1; }
fi
if [ "$RUN_TS" = "1" ] || [ "$RUN_E2E" = "1" ]; then
    command -v npm >/dev/null || { err "npm not on PATH"; exit 1; }
fi
if [ "$NEEDS_STACK" = "1" ]; then
    command -v curl >/dev/null || { err "curl not on PATH"; exit 1; }
    for p in $TEST_PG_HOST_PORT $TEST_API_HOST_PORT $TEST_ENVOY_HOST_PORT $TEST_ENVOY_ADMIN_PORT $TEST_WEB_HOST_PORT; do
        if lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
            err "port $p is already in use — stop the process or run: ./test.sh teardown"
            exit 1
        fi
    done
fi
ok "preflight clean"

# ============================================================================
# wait_for helper (used by stack scenarios)
# ============================================================================

wait_for() {
    local label="$1"; shift
    local max=120
    local attempt=0
    echo -n "  $label "
    while [ $attempt -lt $max ]; do
        if "$@" >/dev/null 2>&1; then
            echo "${GREEN}ready${RESET}"
            return 0
        fi
        attempt=$((attempt + 1))
        echo -n "."
        sleep 1
    done
    echo ""
    err "$label did not become ready within ${max}s"
    "${COMPOSE_TEST[@]}" logs --tail=50 || true
    return 1
}

# ============================================================================
# steps
# ============================================================================

if [ "$RUN_UNIT" = "1" ]; then
    section "Backend unit tests"
    (
        cd backend
        uv sync --extra dev --quiet
        uv run pytest tests/unit -v --no-header
    )
    ok "unit tests passed"
fi

if [ "$RUN_INTEGRATION" = "1" ]; then
    section "Backend integration tests (testcontainers Postgres)"
    (
        cd backend
        uv sync --extra dev --quiet
        uv run pytest tests/integration -v --no-header
    )
    ok "integration tests passed"
fi

if [ "$RUN_TS" = "1" ]; then
    section "Frontend TypeScript check"
    (
        cd frontend
        if [ ! -d node_modules ]; then
            echo "${DIM}(installing frontend deps)${RESET}"
            npm install --silent
        fi
        npx tsc --noEmit
    )
    ok "frontend type-check clean"
fi

if [ "$NEEDS_STACK" = "1" ]; then
    section "Booting isolated test stack ($TEST_PROJECT)"
    "${COMPOSE_TEST[@]}" up -d --build
    ok "containers started (waiting for readiness next)"

    section "Waiting for service readiness"
    wait_for "api gRPC"        "${COMPOSE_TEST[@]}" exec -T api grpc_health_probe -addr=127.0.0.1:50051
    wait_for "envoy /ready"    curl -sf "http://localhost:$TEST_ENVOY_ADMIN_PORT/ready"
    wait_for "web (Next dev)"  curl -sf "http://localhost:$TEST_WEB_HOST_PORT"
    ok "all services responding"
fi

if [ "$RUN_SAMPLE" = "1" ]; then
    section "Sample client (native gRPC, against test stack)"
    (
        cd backend
        uv sync --extra dev --quiet
        uv run python scripts/sample_client.py "localhost:$TEST_API_HOST_PORT"
    )
    ok "sample client succeeded"
fi

if [ "$RUN_E2E" = "1" ]; then
    section "Playwright e2e (against test stack)"
    (
        cd frontend
        if [ ! -d node_modules ]; then
            echo "${DIM}(installing frontend deps)${RESET}"
            npm install --silent
        fi
        npx playwright install --with-deps chromium 2>/dev/null || npx playwright install chromium
        # Bash 3.2 (macOS default) raises "unbound variable" under `set -u`
        # when expanding an empty array as `${arr[@]}`. The `${var+...}` form
        # only expands when the array has at least one element, which is the
        # portable workaround.
        PLAYWRIGHT_BASE_URL="http://localhost:$TEST_WEB_HOST_PORT" \
            npx playwright test ${PLAYWRIGHT_EXTRA_ARGS[@]+"${PLAYWRIGHT_EXTRA_ARGS[@]}"}
    )
    ok "Playwright happy-path passed"
fi

# Trap fires next: tears down (or prints stack URLs for `stack` scenario).
