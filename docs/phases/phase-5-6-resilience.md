# Phase 5.6 — Resilience: Timeouts, Pool Tuning, and Retry Layer

**Status:** Draft, awaiting approval
**Last Updated:** 2026-05-08
**Effort:** M (~4–5 hrs)
**Prerequisites:** [Phase 5](phase-5-borrow-return-fines.md), [Phase 5.5](phase-5-5-observability.md)
**Blocks:** [Phase 6](phase-6-frontend-mvp.md), [Phase 7](phase-7-polish.md)
**Pairs with:** [Phase 5.5](phase-5-5-observability.md) — retry-attempt span events surface in SigNoz

This phase is an addition to the original take-home plan, scoped between Phase 5.5 and Phase 6 to harden the backend against transient failures (deadlocks, lock contention, connection drops, pool exhaustion) before frontend work begins. Without this phase any of those failure modes surfaces as a `gRPC INTERNAL` to the user; with it the system either retries transparently or fails with a coherent, retryable status.

---

## Goal

Add four interlocking resilience mechanisms to the backend so transient failures self-heal where safe and surface coherently where not:

1. **Postgres-side timeouts** — bound how long a single statement, lock-wait, or idle transaction can hold a connection.
2. **Connection pool tuning** — surface saturation as a fast-fail rather than a 30-second queue, recycle long-lived connections, keep `pool_pre_ping` honest.
3. **Service-level retry decorator** — retry transient DB errors (deadlocks, serialization failures, lock-timeout, pool-timeout) with deadline-aware exponential backoff at exactly one layer.
4. **gRPC status-code mapping** — translate the resulting exceptions to the right gRPC code (`UNAVAILABLE`, `DEADLINE_EXCEEDED`, `RESOURCE_EXHAUSTED`, `FAILED_PRECONDITION`, `INTERNAL`) so clients can retry intelligently and dashboards can distinguish infrastructure issues from domain answers.

---

## Related design docs

- [design/01-database.md §3](../design/01-database.md) — borrow concurrency strategy that the retry layer protects.
- [design/03-backend.md](../design/03-backend.md) — error decorator that gains classify + map logic.
- [design/06-observability.md](../design/06-observability.md) — span/event vocabulary that retry observability extends.

> **Note on a separate design doc:** the architectural decisions for this phase (where retry lives, how policies are classified, deadline-propagation contract) are concentrated enough that they live in this document's *Design decisions* section rather than a standalone `07-resilience.md`. If the system's resilience surface grows (circuit breakers, bulkheads, hedged requests), promote that section to its own doc.

---

## Design decisions (pinned before scope)

The four mechanisms interlock; getting one wrong undermines the others. The decisions below are load-bearing and called out so they're easy to challenge before implementation.

### D1. Retry placement — service layer, never repository

Postgres aborts the entire transaction on `DeadlockDetected` / `SerializationFailure`. Retrying a query inside a poisoned transaction is invalid — PG refuses further work on it. Therefore retries must restart the **whole transaction**, which means restarting the `async with AsyncSessionLocal.begin()` block, which means the retry decorator must wrap a function that owns its session lifecycle.

That function is the **service method** (`LoanService.borrow`, `BookService.add_book`, etc.), one layer above the session boundary. Decorating repository methods is wrong because repositories are passed an open session and cannot legally re-enter it. A generic gRPC interceptor is also wrong because it has no way to pick the right policy per RPC.

**Rule:** `@with_retry(...)` lives only on `*_service.py` public methods. CI lints repository files for the import to enforce this.

### D2. One layer of retry, not nested

If both service and repository retried independently, we'd get N×M attempts, exponential blowup, and a deadlock storm under load. The decorator is applied once per RPC entry point and nowhere else. Client-side retry policy (in the frontend's gRPC channel config) operates at a different scope (whole-RPC, network-level) and is intentionally additive.

### D3. Three named policies, not free-form configuration

We commit to exactly three policies as module constants:

- `RETRY_READ` — for pure-read RPCs.
- `RETRY_WRITE_TX` — for transactional writes; narrower error set than READ.
- `RETRY_NEVER` — explicit annotation for "we considered retry and chose not to".

Free-form per-call retry config (passing kwargs to the decorator) is forbidden by convention. A reviewer should be able to tell at a glance which of the three classes each RPC falls into; bespoke per-method tuning hides judgment calls.

### D4. Error classification by exception type, not by string match

The classifier is a small typed function:

```python
def classify(exc: BaseException) -> Literal["retry_safe", "retry_unsafe_write", "domain", "bug"]:
    ...
```

It dispatches on `isinstance` checks against the exception hierarchy plus, for `OperationalError`, the underlying `asyncpg` PG-code (`exc.orig.sqlstate` when available). It never grep-matches error message text — those drift across PG versions and would silently break under upgrade.

### D5. Deadline awareness is mandatory, not optional

Every retry checks `context.time_remaining()` before sleeping; if `remaining < next_backoff_estimate`, it skips the retry and re-raises. Without this, retries can outlive the client and waste DB capacity on results no one will see. A `RequestContextInterceptor` enhancement reads the gRPC `grpc-timeout` metadata, computes the deadline, and stamps a contextvar `_deadline_var` that the decorator reads.

### D6. Retry restarts the transaction with a fresh session

Each retry attempt opens a brand-new `AsyncSessionLocal` session inside the wrapped function. This guarantees:
- A fresh connection is checked out (or the same one re-checked out clean — pool_pre_ping validates it).
- No leftover SQLAlchemy ORM state from the failed attempt.
- The next transaction starts with no leftover tx state on Postgres's side either.

Reusing a session across retry attempts is forbidden — it's the chief source of "retry hides a real bug" failures.

### D7. The decorator re-raises the *last* exception, unwrapped

After exhausting attempts, the decorator re-raises the original exception (not a custom `RetryExhaustedError` wrapper). The reason: the gRPC error mapper at the outer layer needs the original type to pick the right status code. Wrapping forces the mapper to unwrap, which is busywork.

---

## Scope

### In

#### Postgres-side timeouts (set per-connection via SQLAlchemy `connect_args`)

| Setting | Value | Rationale |
|---|---|---|
| `command_timeout` (asyncpg, driver-side) | `5.0` (seconds) | Driver returns control to Python after 5s; protects the asyncio event loop and RPC latency budget |
| `statement_timeout` (server-side) | `'5000'` (ms) | Postgres actually stops the work and releases locks; needed for resource freeing, not just app-side timeout |
| `lock_timeout` (server-side) | `'3000'` (ms) | Bounds non-deadlock lock waits. Lower than `statement_timeout` so a lock wait surfaces as `lock_not_available` (clear signal) rather than `statement_timeout` (ambiguous) |
| `idle_in_transaction_session_timeout` (server-side) | `'15000'` (ms) | Kills a forgotten `BEGIN`. Higher than the longest expected handler so it doesn't fire during normal slow paths |
| `deadlock_timeout` | left at PG default `1s` | Detection delay; tuning is rarely worth it at our scale |

#### Connection pool tuning

| Knob | Value | Rationale |
|---|---|---|
| `pool_size` | `10` | Steady-state warm connections per worker process |
| `max_overflow` | `10` | Burst headroom; total cap = 20 concurrent in-flight DB ops per worker |
| `pool_timeout` | `5` (seconds) | Fast-fail under saturation rather than queueing for 30s; surfaces as `RESOURCE_EXHAUSTED` |
| `pool_recycle` | `1800` (seconds) | Proactively close connections older than 30 min to evade firewall idle-kills |
| `pool_pre_ping` | `True` (already set) | Reactive corpse detection on checkout |

These numbers are sized for local dev + a single API replica. When the system grows to N replicas the cap should be **divided by N** (each replica has its own pool), not multiplied — total connections = `N × (pool_size + max_overflow)` must stay under PG's `max_connections`.

#### Retry decorator and policies

- `library/resilience/policies.py` — `RetryPolicy` frozen dataclass + the three named constants.
- `library/resilience/classify.py` — exception classifier.
- `library/resilience/decorator.py` — `with_retry(policy)` async-aware decorator.
- `library/resilience/deadline.py` — `_deadline_var` contextvar + helper to compute remaining budget.

The three policies, frozen at module load:

```python
RETRY_READ = RetryPolicy(
    attempts=3,
    backoff_base_s=0.05,
    backoff_cap_s=1.0,
    jitter_pct=0.25,
    retryable=frozenset({
        DeadlockDetected, SerializationFailure, LockNotAvailable,
        ConnectionDropped, PoolTimeout, ReadStatementTimeout,
    }),
)

RETRY_WRITE_TX = RetryPolicy(
    attempts=2,
    backoff_base_s=0.05,
    backoff_cap_s=0.5,
    jitter_pct=0.25,
    retryable=frozenset({
        DeadlockDetected, SerializationFailure, LockNotAvailable, PoolTimeout,
    }),  # NOTE: no ConnectionDropped, no StatementTimeout — ambiguous mid-commit
)

RETRY_NEVER = RetryPolicy(
    attempts=1, backoff_base_s=0, backoff_cap_s=0,
    jitter_pct=0, retryable=frozenset(),
)
```

The error classes above are application-level type aliases declared in `classify.py`; the classifier maps the raw `OperationalError` / `IntegrityError` / `asyncpg.PostgresConnectionError` exceptions onto them.

#### gRPC deadline propagation

- `RequestContextInterceptor` (Phase 5.5) is extended to read `context.time_remaining()` and stamp it on a contextvar `_deadline_var: ContextVar[Deadline]`.
- The decorator reads `_deadline_var` before each retry sleep; if `remaining < next_backoff_estimate`, raises rather than sleeping.

#### Per-RPC policy assignment

| RPC | Service method | Policy |
|---|---|---|
| `GetBook` | `BookService.get_book` | `RETRY_READ` |
| `ListBooks` | `BookService.list_books` | `RETRY_READ` |
| `AddBook` | `BookService.add_book` | `RETRY_WRITE_TX` |
| `UpdateBook` | `BookService.update_book` | `RETRY_WRITE_TX` |
| `RemoveBook` | `BookService.remove_book` | `RETRY_WRITE_TX` |
| `AddMember` | `MemberService.add_member` | `RETRY_WRITE_TX` |
| `GetMember` | `MemberService.get_member` | `RETRY_READ` |
| `RemoveMember` | `MemberService.remove_member` | `RETRY_WRITE_TX` |
| `BorrowBook` | `LoanService.borrow` | `RETRY_WRITE_TX` |
| `ReturnBook` | `LoanService.return_book` | `RETRY_WRITE_TX` |
| `ListLoans` | `LoanService.list_loans` | `RETRY_READ` |
| `GetMemberLoans` | `LoanService.get_member_loans` | `RETRY_READ` |

#### gRPC status-code mapping

`errors.map_domain_errors` (Phase 5.5) is extended to recognize the new transient-error classes and map them:

| Classified exception | gRPC code |
|---|---|
| `DeadlockDetected`, `SerializationFailure`, `LockNotAvailable`, `ConnectionDropped` (after retry exhaustion) | `UNAVAILABLE` |
| `PoolTimeout` (after retry exhaustion) | `RESOURCE_EXHAUSTED` |
| Deadline consumed during retry | `DEADLINE_EXCEEDED` |
| `IntegrityError` not mapped to a domain error | `INTERNAL` (with `record_exception`) |
| Existing `NotFound`, `FailedPrecondition`, etc. | unchanged |

`UNAVAILABLE` and `RESOURCE_EXHAUSTED` are explicitly retryable per gRPC convention, so well-behaved clients (the frontend's gRPC channel config) will retry them at the network level.

#### Observability (Phase 5.5 extension)

- Each retry attempt past the first emits a span event `retry.attempt` on the active span with attributes:
  - `retry.attempt` (int, 2-indexed; first attempt is unmarked)
  - `retry.policy` (string, e.g. `"RETRY_WRITE_TX"`)
  - `retry.backoff_ms` (int)
  - `retry.error_class` (string, the classified type, not the raw exception)
- Final-failure paths emit `retry.exhausted` event before the exception propagates.
- The access log line emitted by `RequestContextInterceptor` gains a `retry.attempts` field (0 for unretried calls) so SigNoz log queries can distinguish a happy path from a retried-but-ultimately-successful call.

### Out

- **Circuit breakers.** Useful but premature at this scale; would protect a *downstream* dependency, and PG is the only one. Deferred until the architecture has more than one external service to break circuits against.
- **Bulkheads / per-RPC connection pools.** Same — premature.
- **Hedged requests.** No replicated reads to hedge against.
- **Idempotency keys.** Required if we ever want to retry connection-dropped writes safely. Out of scope here; future phase if/when needed (e.g., for `PayFine` or external-payment integrations).
- **Client-side retry config.** The frontend's gRPC channel config (Phase 6) is responsible for declaring its own retry policy via gRPC service config JSON. This phase makes the *server* retry-friendly; the client decision is layered on top.
- **Per-replica sizing for production scale.** Pool sizing is set for single-replica local dev. Phase 7 polish or a deploy-config phase should revisit when N > 1.
- **Postgres `idle_session_timeout`** (PG14+, kills idle pool members). `pool_recycle` covers this from the app side already; adding the server-side variant is duplicative for now.

---

## Deliverables

### New files

- `backend/src/library/resilience/__init__.py` — package init; re-exports `with_retry`, the three policies, and `classify`.
- `backend/src/library/resilience/policies.py` — `RetryPolicy` frozen dataclass; module-level `RETRY_READ`, `RETRY_WRITE_TX`, `RETRY_NEVER` constants.
- `backend/src/library/resilience/classify.py` — exception classifier; type aliases for the application-level error classes (`DeadlockDetected`, `SerializationFailure`, `LockNotAvailable`, `ConnectionDropped`, `PoolTimeout`, `ReadStatementTimeout`).
- `backend/src/library/resilience/decorator.py` — `with_retry(policy)` async decorator. ~80 lines.
- `backend/src/library/resilience/deadline.py` — `_deadline_var: ContextVar`, `set_deadline_from_context(grpc_context)`, `time_remaining()` helpers.
- `backend/src/library/resilience/backoff.py` — `compute_backoff(attempt, policy)` with exponential + jitter.

### Modified files

- `backend/src/library/db/engine.py` — add `connect_args` (asyncpg `command_timeout`, PG `server_settings` for `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`); add pool sizing knobs (`pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`).
- `backend/src/library/observability/interceptors.py` — read `context.time_remaining()` and stamp `_deadline_var`; add `retry.attempts` field to access-log emission (read from a request-scoped counter that the decorator increments).
- `backend/src/library/services/loan_service.py` — `@with_retry(RETRY_WRITE_TX)` on `borrow` and `return_book`; `@with_retry(RETRY_READ)` on `list_loans` and `get_member_loans`.
- `backend/src/library/services/book_service.py` — `@with_retry(RETRY_WRITE_TX)` on `add_book`, `update_book`, `remove_book`; `@with_retry(RETRY_READ)` on `get_book`, `list_books`.
- `backend/src/library/services/member_service.py` — `@with_retry(RETRY_WRITE_TX)` on `add_member`, `remove_member`; `@with_retry(RETRY_READ)` on `get_member`.
- `backend/src/library/errors.py` — extend `map_domain_errors` to recognize the new transient-error classes from `resilience.classify` and map them to gRPC codes per the table above. Keep the span-status / `record_exception` hooks Phase 5.5 added.
- `backend/pyproject.toml` — no new third-party deps; this phase is pure stdlib + existing SQLAlchemy/asyncpg surface.
- `backend/src/library/config.py` — add settings fields for the timeouts and pool knobs so they're tunable via env (`DB_STATEMENT_TIMEOUT_MS`, `DB_LOCK_TIMEOUT_MS`, `DB_IDLE_TX_TIMEOUT_MS`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_S`, `DB_POOL_RECYCLE_S`). Defaults match the values pinned above.
- `docker-compose.yml` — surface those env vars on the `api` service with the spec'd defaults; no `.env` change needed.

### New tests

- `backend/tests/unit/test_classify.py` — exhaustive table-driven test: every documented error class maps to the right `Literal`. Includes `OperationalError` wrapping each PG sqlstate from the table.
- `backend/tests/unit/test_backoff.py` — exponential-with-jitter math; jitter stays in band; cap is honored.
- `backend/tests/unit/test_decorator.py` — decorator behavior on:
  - All retryable errors lead to retry then success on attempt N.
  - Non-retryable errors raise on first attempt with no sleep.
  - Attempts cap is honored; final exception is the *original* type.
  - Deadline-aware skip — when contextvar reports `remaining < backoff`, decorator raises without sleeping.
  - Each retry past attempt 1 emits a `retry.attempt` span event with the right attributes.
- `backend/tests/integration/test_resilience.py` —
  - **Forced deadlock** between two concurrent `borrow` calls: arrange a fixture that injects `DeadlockDetected` on the first attempt of one transaction, assert the service-level call still succeeds with `loan_id` populated and a `retry.attempt` span event was emitted.
  - **Lock timeout** under contention: hold a row lock from a side connection longer than `lock_timeout`; assert the second call fails with `lock_not_available`, gets retried per policy, and (on persistent contention) surfaces as gRPC `UNAVAILABLE`.
  - **Pool exhaustion**: configure a tiny pool (size=1, overflow=0), launch `N+1` concurrent reads, assert one fails-then-succeeds via retry and the gRPC-status mapping is `RESOURCE_EXHAUSTED` after exhaustion.
  - **Statement timeout**: run a deliberately slow query (e.g., `pg_sleep(10)` injected via a test-only repo method) under a low `statement_timeout`; assert the read variant retries and the write variant does not.
  - **Idle-in-transaction timeout**: open a session, `BEGIN`, sleep past `idle_in_transaction_session_timeout`, then attempt the next statement; assert the session is invalidated and `pool_pre_ping` discards the dead connection on next checkout. (Verifies the four-layer chain end-to-end.)
  - **Deadline propagation**: call an RPC with a 100ms deadline against a path that would otherwise retry for 500ms; assert the call returns `DEADLINE_EXCEEDED` and the retry counter shows fewer-than-max attempts.

### Documentation updates

- `README.md` — short subsection under "Local observability" describing the resilience-layer env vars and how to lower them in tests/dev to surface failures faster.
- `docs/progress-report.md` — Phase 5.6 status entry, mirroring the Phase 5.5 / 5.5b style.

---

## Acceptance criteria

1. All prior tests still pass: `pytest backend/tests/` — 85 tests from Phase 5.5 plus the new unit/integration suite.
2. New unit tests pass — `test_classify.py`, `test_backoff.py`, `test_decorator.py`.
3. New integration tests pass — `test_resilience.py`, including the forced-deadlock and pool-exhaustion scenarios.
4. Test suite runtime regresses by less than 15% relative to Phase 5.5's baseline (~5.3s). The integration suite adds genuine concurrency tests so some growth is expected; 15% is the budget.
5. End-to-end smoke against the live container:
   - Two concurrent `BorrowBook` calls forcing copy contention both succeed (one picks the copy, the other gets `FAILED_PRECONDITION` — no retry needed for that, but the *infrastructure* is there if a deadlock had occurred).
   - With `DB_POOL_SIZE=1, DB_MAX_OVERFLOW=0` and 5 concurrent reads, at least one returns `RESOURCE_EXHAUSTED` with the right gRPC code (not `INTERNAL`).
   - Killing the Postgres container mid-flight produces `UNAVAILABLE` to the client (not `INTERNAL`), and the next request after Postgres restarts succeeds via `pool_pre_ping` reconnect.
6. SigNoz UI: under any forced-retry scenario, the trace tree shows the root RPC span carrying a `retry.attempt` event with `retry.policy`, `retry.error_class`, and `retry.backoff_ms` populated.
7. Code-review checklist passes:
   - No `@with_retry` decorator anywhere in `repositories/`.
   - No bespoke per-call retry config (kwargs to the decorator) — only the three named policies.
   - Every public service method has exactly one retry policy attached, including `RETRY_NEVER` where applicable.

---

## Notes & risks

- **Forcing a deadlock in a test is harder than it sounds.** The standard trick is two concurrent transactions that lock rows in opposite orders. Our current code is structurally deadlock-resistant (see [Design decisions D1](#design-decisions-pinned-before-scope) discussion), so the test will need to either (a) use a test-only repo method that explicitly takes locks in a deadlock-prone order, or (b) inject a `DeadlockDetected` exception via a SQLAlchemy event hook on the first attempt. Option (b) is simpler and tests the decorator path; option (a) is more realistic but adds test-only SQL. **Recommended:** ship (b) for the decorator-coverage test, and add (a) only if reviewers want a "real" deadlock demonstration.
- **`statement_timeout` interaction with long-running admin operations.** If we add a future admin RPC like "rebuild fine totals across all members", a 5-second `statement_timeout` may be too tight. The fix is a per-session override (`SET LOCAL statement_timeout = '60s'`) inside that handler — *not* raising the global default. Document this pattern in `design/03-backend.md` so future contributors don't bump the global value.
- **`lock_timeout < statement_timeout` ordering matters.** If we ever raise `lock_timeout` past `statement_timeout`, lock waits would surface as `statement_timeout` (less informative) instead of `lock_not_available`. Add a config-time assertion: `lock_timeout < statement_timeout`.
- **Pool sizing under multiple workers.** Today `main.py` runs a single asyncio gRPC server. If we ever switch to multi-process serving (gunicorn-style), `pool_size + max_overflow` must be divided by worker count to stay under PG's `max_connections`. Flag in `config.py` docstring.
- **Deadline propagation requires gRPC clients to set deadlines.** The grpc-Python sample client and the frontend gRPC-Web stub must call with `timeout=...` for the contextvar to be populated. Without a client deadline, `time_remaining()` returns `None` and the decorator falls back to the policy's max budget. Document in the sample-client README that callers should set deadlines explicitly.
- **Retry can mask real outages.** A noisy `retry.attempt` event stream in SigNoz is a leading indicator of underlying contention. The phase delivers the events; phase 7 polish or a follow-up ops phase should add a SigNoz alert for "retry rate above threshold".
- **Connection-error classification is hairy.** asyncpg connection errors come in several shapes (`ConnectionDoesNotExistError`, `InterfaceError`, raw `OSError` after a TCP RST). The classifier needs a comprehensive `isinstance` set; underspecifying leaks transient errors as `INTERNAL`. The unit test must enumerate every variant.
- **`SerializationFailure` is mostly defensive.** We use `READ COMMITTED` isolation, where 40001 doesn't fire. Listing it in the policies costs nothing and future-proofs against an isolation-level bump.

---

## Cross-references

- Concurrency design that this phase protects: [design/01-database.md §3](../design/01-database.md)
- Error decorator that gains transient-error mapping: [design/03-backend.md](../design/03-backend.md)
- Observability events that retry attempts emit: [design/06-observability.md §5](../design/06-observability.md)
- Phase whose request-id contextvar is reused: [phases/phase-5-5-observability.md](phase-5-5-observability.md)
- Phase that benefits next from this work: [phases/phase-6-frontend-mvp.md](phase-6-frontend-mvp.md) — frontend gRPC channel config can declare retry policies on top of these server-side guarantees.
