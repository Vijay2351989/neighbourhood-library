# Decisions, Open Questions & Risks

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)

This document is the registry of non-obvious design decisions and the risks we knowingly accept. Every row has a clear "decision" — there are no unresolved items here. New decisions land here as the project evolves.

---

| # | Topic | Decision / Mitigation |
|---|---|---|
| 1 | **ISBN uniqueness.** Real-world ISBNs aren't perfectly unique (different editions sometimes share, data-entry errors are common). | Make `isbn` nullable, **not unique**. Document this in the README. |
| 2 | **Member email uniqueness.** Staff need a stable identifier to disambiguate humans. | Enforce uniqueness on `lower(email)`. Map duplicate inserts to `ALREADY_EXISTS`. |
| 3 | **Deleting a member with active loans.** Hard delete would orphan loans; cascading delete would erase loan history. | Soft-delete is overkill for a take-home. **Block hard delete** if any loans (active or historical) reference the member; return `FAILED_PRECONDITION` with a clear message. (Alternative: don't expose Delete at all in the UI for v1 — recommended.) |
| 4 | **Reducing copy count below borrowed count.** | Reject with `FAILED_PRECONDITION` and message: "Cannot reduce copies below the number currently borrowed (X)." |
| 5 | **gRPC-Web tooling churn.** `protoc-gen-grpc-web` (Google) vs Connect (Buf). | We pick Connect (`@connectrpc/connect-web` + `@bufbuild/protoc-gen-es`). Risk: Connect's gRPC-Web mode has some edge cases with streaming, but we use no streaming RPCs, so we're fine. Document the choice in the README. |
| 6 | **Time zones.** Browser sees the staff member's local TZ; server stores UTC. | Use `TIMESTAMPTZ` everywhere, `google.protobuf.Timestamp` on the wire. Frontend formatters use `Intl.DateTimeFormat` with the user's locale. |
| 7 | **Default loan length.** Not specified by the assignment. | Default to **14 days**, configurable via `DEFAULT_LOAN_DAYS` env var. `BorrowBookRequest.due_at` allows override. |
| 8 | **Pagination style.** Offset vs cursor. | Offset. Lists are small, requirements are simple. Cursor pagination is over-engineering at this scale. |
| 9 | **What if Envoy can't reach the API on cold start?** | Compose `depends_on` with `condition: service_healthy` on the api service ensures Envoy starts after the api is up. The api healthcheck uses `grpc_health_probe`. |
| 10 | **Should the proto file live at repo root or inside backend/?** | **Repo root `proto/`.** Both backend and frontend codegen consume it from there; symlinking or copying inside containers is fine. |
| 11 | **Fine policy.** Late returns need a penalty model: grace, accrual rate, cap. | **14-day grace after `due_at`, then $0.25/day, capped at $20.** All three values are env-configurable (`FINE_GRACE_DAYS`, `FINE_PER_DAY_CENTS`, `FINE_CAP_CENTS`). Fines are computed at query time ([design/01-database.md §5](../design/01-database.md#5-fine-policy-computed-not-stored)), never stored. There is no payment ledger — once a fine exists, it remains visible on the loan record forever. |
| 12 | **Concurrency strategy: index alone, or index + `FOR UPDATE`?** | **Both.** The partial unique index is the structural guarantee; `SELECT ... FOR UPDATE SKIP LOCKED` lets concurrent borrows of *different* copies of the same book proceed without blocking each other. See [design/01-database.md §3](../design/01-database.md#3-concurrency-strategy-the-partial-unique-index). |
| 13 | **`available_copies` storage strategy.** Counter on `books` updated by triggers, vs computed at query time. | **Computed.** Library scale doesn't justify the denormalization complexity; the aggregate query is fast with the `(book_id, status)` index. |
| 14 | **`updated_at` maintenance.** Trigger vs application-side. | **Application-side.** Simpler to migrate; triggers add a hidden mutation that's painful to debug. |
| 15 | **Currency formatting.** Localized vs hardcoded. | **Hardcoded USD** for the take-home. Future work would internationalize. |

---

## How to add a new entry

1. Append a new row to the table.
2. State the topic concisely; state the decision crisply.
3. Cross-link to the relevant design doc when applicable.
4. Update `Last Updated` at the top.
