# Phase 7 — Polish: Seed, Sample Client, README, Optional Test

**Status:** Approved, not yet started
**Last Updated:** 2026-05-05
**Effort:** M (~6 hrs)
**Prerequisites:** [Phase 6](phase-6-frontend-mvp.md)
**Blocks:** —

---

## Goal

The deliverable is reviewer-ready: zero-friction setup, sample client demonstrating the API, comprehensive README, and (time permitting) one e2e test.

---

## Related design docs

- [reference/readme-outline.md](../reference/readme-outline.md) — the README skeleton this phase fills out
- [design/05-infrastructure.md §5](../design/05-infrastructure.md#5-seed-data) — seed service profile
- [design/03-backend.md](../design/03-backend.md) — `scripts/seed.py` and `scripts/sample_client.py` locations
- [reference/testing.md](../reference/testing.md) — Playwright test note

---

## Scope

### In
- `backend/scripts/seed.py` — populates ~20 books, ~10 members, ~5 active loans, ~3 returned loans, ~1 overdue loan still within grace (no fine yet), **~1 overdue loan past grace (currently accruing fine), ~1 returned-late loan (snapshot fine)**. Uses the gRPC API (not direct SQL). To produce historic dates, the seed script may write directly to the DB for `borrowed_at` / `due_at` overrides — document this caveat clearly in the script header.
- `backend/scripts/sample_client.py` — standalone script: connects, creates a member + book, borrows, lists, returns, lists again. Heavily commented as it doubles as API documentation.
- `seed` Compose service profile that runs `seed.py` once.
- Root `README.md` filled out per [reference/readme-outline.md](../reference/readme-outline.md).
- `frontend/README.md` and `backend/README.md` — short, link to root.
- *(Optional, time permitting)*: one Playwright test that drives the demo script in [Phase 6](phase-6-frontend-mvp.md)'s acceptance criteria. Skip if running long — flag in README.

### Out
- Anything not on the above list.

---

## Deliverables

- `seed.py`, `sample_client.py`.
- Updated Compose with `seed` profile.
- Final root `README.md`.
- Optional: `frontend/e2e/happy-path.spec.ts`.

---

## Acceptance criteria

- A reviewer who has never seen the repo can: clone → `docker compose up` → `docker compose --profile seed up seed` → open `http://localhost:3000` and see populated data — by following only the README.
- `python backend/scripts/sample_client.py` (against a running stack) prints a clean before/after of a full borrow/return cycle.
- Root README has a "How to test" section explaining `pytest`.
- Seed produces visible loans in three fine states: no-fine (within grace), accruing, returned-late snapshot.

---

## Notes & risks

- **Seed direct-SQL caveat.** Creating overdue loans requires `borrowed_at` and `due_at` in the past — the gRPC API doesn't expose those as parameters. The script should call the public API for the simple seed cases and fall back to direct DB writes for the historic-date cases, with a banner comment explaining why.
- **Sample client value.** The rubric explicitly mentions sample client scripts; this is high-leverage. Keep the script short and well-commented — a reviewer should be able to read it linearly and understand the API surface.
- **README is the rubric's documentation deliverable.** Don't shortchange it. Every section in [reference/readme-outline.md](../reference/readme-outline.md) earns rubric points.
- **Playwright optional.** If running tight on time, skip Playwright — Phase 5's integration tests already cover the correctness story; Playwright would just demonstrate the UI wiring works, which the demo script already proves manually.
