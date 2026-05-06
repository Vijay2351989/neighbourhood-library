# Phase 6 — Frontend MVP

**Status:** Approved, not yet started
**Last Updated:** 2026-05-05
**Effort:** L (~14 hrs)
**Prerequisites:** [Phase 4](phase-4-backend-crud.md), [Phase 5](phase-5-borrow-return-fines.md)
**Blocks:** [Phase 7](phase-7-polish.md)

---

## Goal

Staff can perform every operation through the web UI: create/update books and members, borrow, return, list, search, paginate, and see fines.

---

## Related design docs

- [design/04-frontend.md](../design/04-frontend.md) — directory layout, page responsibilities, data-fetching pattern, currency formatting
- [design/02-api-contract.md](../design/02-api-contract.md) — wire types and error semantics
- [design/05-infrastructure.md](../design/05-infrastructure.md) — Envoy as the gRPC-Web bridge

---

## Scope

### In
- `lib/client.ts` — `createPromiseClient(LibraryService, createGrpcWebTransport({baseUrl: NEXT_PUBLIC_API_BASE_URL}))`.
- `lib/queryKeys.ts` — typed key factory.
- `lib/format.ts` — date/timestamp + currency formatters.
- Layout shell with top nav: Dashboard, Books, Members, Loans.
- Books: list (search, paginate, "New book" button), create form, edit form, detail page.
- Members: list (search, paginate), create form, edit form, detail page (with outstanding-fines tile + tabbed loan history).
- Loans: global list (filter chips Active / Overdue / Has Fine / Returned), borrow form (`/loans/new`) with member-picker and book-picker, "Return" button on active loans.
- Dashboard: five tiles (total books, total members, active loans, overdue, total outstanding fines formatted as currency).
- Error UX: any non-`OK` gRPC status renders a toast with the friendly message; `INVALID_ARGUMENT` highlights the offending form field where possible.
- Loading skeletons on all list pages.

### Out
- Comprehensive Playwright tests (one optional happy-path Playwright test in [Phase 7](phase-7-polish.md)).
- Authentication, member-facing UI, multi-tenancy.

---

## Deliverables

- All pages and components listed in [design/04-frontend.md §1](../design/04-frontend.md#1-directory-layout).
- A consistent UI kit in `components/ui/` (button, input, table, pagination, toast, dialog).

---

## Acceptance criteria

Manual run-through (a "demo script"):

1. Open `http://localhost:3000`. Dashboard loads with all five tiles.
2. Books → New → create "Dune" with 2 copies. Appears in the list.
3. Members → New → create "Alice". Appears in the list.
4. Loans → New → pick Alice → pick Dune → submit. Loan appears in active list.
5. Try to borrow Dune again for Alice — it succeeds (2 copies available initially). Try a third time — `FAILED_PRECONDITION` toast.
6. Members → Alice → tab Active → click Return on first loan. Disappears from active, appears in Returned tab.
7. Books → Dune detail → available count is 1 (after one of two was borrowed and the other returned).
8. Search "dun" in books list → finds it. Pagination works on a list of 30+ books (use seed data from [Phase 7](phase-7-polish.md) — for Phase 6, manually create extras).
9. Open the member with a fined loan (from seed): outstanding-fines tile renders with the right amount; the loan row shows a fine column; `/loans` with the "Has Fine" filter lists exactly that loan.

---

## Notes & risks

- **CORS.** Envoy already adds the right CORS headers ([design/05-infrastructure.md §1](../design/05-infrastructure.md#1-envoy-configuration)). If a browser request fails CORS, check `NEXT_PUBLIC_API_BASE_URL` matches the Envoy listener.
- **Form validation UX.** The backend is the source of truth. Frontend does the cheap checks (required fields, max length) but always renders `INVALID_ARGUMENT` from the server cleanly.
- **Currency formatting.** All `*_cents` fields go through `formatCents` from [design/04-frontend.md §6](../design/04-frontend.md#6-currency-formatting). Hard-coded USD; flag as future-work if a reviewer asks.
- **Pagination state in URL.** Tempting to keep page in component state, but URL params survive refresh and back-button. Use Next.js `searchParams`.
- **Effort compressibility.** This phase is ~14h as scoped. If running tight, the simplest cut is to merge create/edit pages into single forms with inline validation rather than separate routes.
