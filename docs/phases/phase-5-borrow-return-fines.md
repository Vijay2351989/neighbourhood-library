# Phase 5 — Borrow & Return with Concurrency + Fines

**Status:** Approved, not yet started
**Last Updated:** 2026-05-05
**Effort:** L (~12 hrs — includes the +2-3h of fine logic added on top of the original ~10h)
**Prerequisites:** [Phase 4](phase-4-backend-crud.md)
**Blocks:** [Phase 6](phase-6-frontend-mvp.md)

---

## Goal

`BorrowBook`, `ReturnBook`, `ListLoans`, `GetMemberLoans` work correctly, including:
- Under concurrent borrow attempts on a single-copy book.
- With fines computed correctly across the grace boundary, the cap, and the returned-late snapshot case.

---

## Related design docs

- [design/01-database.md §3](../design/01-database.md#3-concurrency-strategy-the-partial-unique-index) — `FOR UPDATE SKIP LOCKED` + partial unique index
- [design/01-database.md §5](../design/01-database.md#5-fine-policy-computed-not-stored) — fine policy, formula, env vars
- [design/02-api-contract.md](../design/02-api-contract.md) — `Loan` message including `overdue` and `fine_cents`; `LoanFilter` enum
- [design/03-backend.md](../design/03-backend.md) — module layout, including `services/fines.py`
- [reference/testing.md](../reference/testing.md) — concurrency test pattern

---

## Scope

### In
- `repositories/loans.py` — borrow transaction (the `FOR UPDATE SKIP LOCKED` flow); return transaction (set `returned_at`, flip copy status to `AVAILABLE`); list/filter queries with the `LoanFilter` enum semantics including `LOAN_FILTER_HAS_FINE`.
- `services/loan_service.py` — protobuf wiring + error translation. Default `due_at = now + DEFAULT_LOAN_DAYS`. `overdue` and `fine_cents` are computed at response-build time using the formula in [design/01-database.md §5](../design/01-database.md#5-fine-policy-computed-not-stored).
- `services/fines.py` — pure-function `compute_fine_cents(due_at, returned_at, now, grace_days, per_day_cents, cap_cents) -> int`. No I/O, no proto imports — purely arithmetic.
- `servicer.py` — register the four loan methods.
- Default loan length is read from `DEFAULT_LOAN_DAYS` env var (default 14).
- Fine config from env vars: `FINE_GRACE_DAYS` (14), `FINE_PER_DAY_CENTS` (25), `FINE_CAP_CENTS` (2000).
- The `Loan` response message is enriched with `book_title`, `book_author`, `member_name` via SQL joins so the UI doesn't need extra round-trips.
- The `Member` response is enriched with `outstanding_fines_cents` (sum of fines across that member's loans) when fetched via `GetMember`.

### Out
- Frontend integration (Phase 6).
- Seed data with overdue/fined loans (Phase 7).

---

## Deliverables

- `repositories/loans.py`, `services/loan_service.py`, `services/fines.py`.
- Servicer additions for the four loan methods.
- `tests/integration/test_borrow_return.py` — happy path, double-borrow rejection, return flow, return-already-returned rejection, overdue flag computation, **`fine_cents` computation across the grace boundary, capped fine at `FINE_CAP_CENTS`, returned-late snapshot fine, member `outstanding_fines_cents` aggregation across multiple loans**, list with each filter value (including `LOAN_FILTER_HAS_FINE`), member-scoped query.
- `tests/integration/test_concurrency.py` — spawn N=10 concurrent `BorrowBook` tasks against a 1-copy book; assert exactly 1 succeeds and 9 get `FAILED_PRECONDITION`.
- `tests/unit/test_loan_logic.py` — pure-function tests for the overdue predicate, **`compute_fine_cents` across all the table-of-behavior cases (within grace, exactly at grace boundary, mid-fine, at cap, beyond cap, returned within grace, returned past grace)**, and any state-transition helpers.

---

## Acceptance criteria

- All loan tests green: `pytest backend/tests/integration/test_borrow_return.py backend/tests/integration/test_concurrency.py backend/tests/unit/test_loan_logic.py`.
- Concurrency test green: exactly one borrow wins, others fail cleanly with `FAILED_PRECONDITION`, no partial state in DB (verified by checking `loans` row count and `book_copies.status`).
- `grpcurl` smoke: borrow → list active → return → list active again, observed counts make sense.
- Manual smoke for fines (against test data with manipulated `due_at`):
  - Loan due yesterday → `fine_cents = 0` (within grace).
  - Loan due 15 days ago → `fine_cents = 25` (1 day past grace).
  - Loan due 100 days ago → `fine_cents = 2000` (capped).
  - Loan returned 20 days after due → `fine_cents = 6 * 25 = 150` (snapshot).
  - Member with 3 of the above active → `outstanding_fines_cents` matches sum.

---

## Notes & risks

- **`SELECT ... FOR UPDATE SKIP LOCKED`.** Behavior verified in test_concurrency. The skip-locked clause matters for performance on multi-copy books — without it, parallel borrows of *different* copies of the same book would serialize.
- **Partial unique index as backstop.** Even if `FOR UPDATE SKIP LOCKED` had a bug, the index ensures the DB never holds two active loans for the same copy. Catch the `IntegrityError` and map to `FAILED_PRECONDITION`.
- **`overdue` and `fine_cents` are evaluated against `now()`.** Use `datetime.now(timezone.utc)` consistently and pass it down so tests can inject a fixed time.
- **Aggregation for `outstanding_fines_cents`.** Run as a single SQL query in the repository (sum of computed fine via SQL `CASE`) rather than fetching all loans and summing in Python. Document the SQL clearly.
- **No payment / waiver concept.** Once a fine is owed it remains visible forever. This is documented as out of scope in [00-overview.md §4](../00-overview.md#4-explicit-non-goals).
