# Testing Strategy

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)
**Used by:** [Phase 4](../phases/phase-4-backend-crud.md), [Phase 5](../phases/phase-5-borrow-return-fines.md), [Phase 7](../phases/phase-7-polish.md)

Tests are layered to match where bugs actually appear.

---

## 1. Unit tests

**Where:** `backend/tests/unit/`.

**What:** Pure-function logic that can be tested without a database — the overdue predicate, the `compute_fine_cents` formula, request validation helpers, protobuf↔domain conversions. Small, fast, no I/O.

**What we deliberately don't unit-test:** repositories or services in isolation. Mocking SQLAlchemy is more bug-prone than running against a real Postgres, and we have testcontainers for that.

### Fine formula coverage (Phase 5)

The `compute_fine_cents` function is exercised across these cases (one parameterized test):

- Within grace, active.
- Within grace, returned.
- Exactly at grace boundary (`days_past_grace == 0`).
- One day past grace (mid-fine, $0.25).
- Mid-fine (e.g. 10 days past grace → $2.50).
- At cap (cap reached exactly).
- Beyond cap (fine should not exceed cap).
- Returned within grace (returned-at as reference, < grace).
- Returned past grace (returned-at as reference, snapshot).

---

## 2. Integration tests (the bulk of the suite)

**Where:** `backend/tests/integration/`.

**Setup:** A `pytest` session-scoped fixture spins up a Postgres testcontainer, runs `alembic upgrade head`, and starts an in-process gRPC server bound to a random port. Each test gets a fresh transaction that rolls back at teardown (or, where transactional rollback is incompatible with the test, a per-test `TRUNCATE` of the four tables).

**What's covered:**

- All CRUD happy paths for books and members.
- Validation: empty fields, oversized page sizes, negative copy counts.
- `NOT_FOUND` for missing IDs.
- `ALREADY_EXISTS` for duplicate member email.
- Borrow happy path; borrow when no copies available → `FAILED_PRECONDITION`.
- Return happy path; return-already-returned → `FAILED_PRECONDITION`.
- Loan listing with each `LoanFilter` value (Active / Returned / Overdue / **Has Fine**).
- Overdue computation (set `due_at` in the past, assert `overdue=true` in the response).
- **Fine computation:** across the grace boundary, the cap, returned-late snapshot, and `Member.outstanding_fines_cents` aggregation across multiple loans for one member.
- Copy reconciliation on `UpdateBook` (count up, count down, count down below borrowed → rejection).
- **Concurrency:** N concurrent borrow tasks against a single-copy book; assert exactly one succeeds, the rest get `FAILED_PRECONDITION`, and final DB state is consistent.

---

## 3. Frontend tests

Out of scope for the take-home. We focus our test budget on the backend, where correctness matters most.

**One optional Playwright test** in [Phase 7](../phases/phase-7-polish.md) walking the happy path (create book, create member, borrow, return) gives us a smoke-level guarantee that the wiring works without committing to a full UI test suite.

---

## 4. Sample client script

`backend/scripts/sample_client.py` is not a test per se but functions as one: every reviewer run is a free smoke test of the entire stack. It also satisfies the rubric's "sample client script" tip. See [Phase 7](../phases/phase-7-polish.md).

---

## 5. Test execution

All tests run inside the project — no external services needed beyond Docker for testcontainers. CI is out of scope but the structure is CI-ready.

```bash
# All tests
cd backend && uv run pytest

# Just unit
uv run pytest tests/unit/

# Just integration
uv run pytest tests/integration/

# Concurrency only (slow-ish)
uv run pytest tests/integration/test_concurrency.py
```

Testcontainers requires Docker to be running on the host.
