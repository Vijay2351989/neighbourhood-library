# Frontend e2e (Playwright)

A single happy-path test that drives the Phase 6 acceptance demo through a real
browser. Phase 5 integration tests cover correctness exhaustively; this proves
the wiring (Next 16 → gRPC-Web → Envoy → backend → Postgres) all works end to
end.

## Prerequisites

Before running the test, the full stack must be up:

1. **Backend & infra:** from the repo root, `docker compose up`. This starts
   Postgres, the gRPC service on `:50051`, and Envoy's gRPC-Web bridge on
   `:8080`.
2. **Frontend dev server:** `cd frontend && npm run dev` (serves on `:3000`).
3. **`DEMO_MODE` should be off** (or just empty data). The test creates its
   own book, member, and loan with a `Date.now()` suffix so reruns don't
   collide on the unique-email constraint.

Playwright does **not** auto-start the dev server — by design. The README's
quickstart is the canonical way to bring the stack up; the test just attaches.

## One-time browser install

Playwright needs a Chromium binary (~150 MB):

```bash
cd frontend
npm run test:e2e:install
```

## Run the test

```bash
cd frontend
npm run test:e2e
```

Open the HTML report after a run:

```bash
npx playwright show-report
```

## What the test covers

In one ~150-line spec at `e2e/happy-path.spec.ts`:

1. Dashboard renders the five count tiles.
2. Create a book (2 copies, unique title).
3. Create a member (unique email).
4. Borrow the book for the member via `/loans/new` (member picker → book
   picker → BorrowDialog confirmation).
5. The new loan appears on the member's Active tab.
6. Return the loan via the row's Return button + dialog.
7. The loan disappears from Active and reappears on Returned.
8. The book's inventory card shows `2 / 2 available` again.

What it deliberately does **not** cover:

- Multi-browser (chromium only).
- Accessibility audits, screenshots, visual diffs.
- Error / sad paths (FAILED_PRECONDITION, INVALID_ARGUMENT) — those have
  unit/integration coverage in Phase 5.
- Pagination, search, fines UI — out of Phase 7's "happy-path" scope.

## Notes for CI

- `retries: 1` and `trace: on-first-retry` keep the success case fast while
  giving you a Playwright trace if a transient flake happens.
- `workers: 1, fullyParallel: false` — there's only one test, and a serial
  default is the right shape if more tests are added later (the backend
  shares a single Postgres).
- `playwright-report/` and `test-results/` are gitignored.
