# Frontend Design

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)
**Implemented in:** [Phase 1](../phases/phase-1-scaffolding.md), [Phase 6](../phases/phase-6-frontend-mvp.md)

Next.js project structure, gRPC-Web client choice, data-fetching pattern, and page-by-page responsibilities.

---

## 1. Directory layout

```
frontend/
├── package.json                # next, react, typescript, tailwind, @tanstack/react-query,
│                               # @bufbuild/protobuf, @connectrpc/connect, @connectrpc/connect-web
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── buf.gen.yaml                # codegen config for ts protobuf stubs
├── README.md
├── src/
│   ├── app/                    # Next.js App Router
│   │   ├── layout.tsx          # global shell: top nav, QueryClientProvider
│   │   ├── page.tsx            # dashboard
│   │   ├── books/
│   │   │   ├── page.tsx        # ListBooks with search box + pagination + "New book" button
│   │   │   ├── new/page.tsx    # create form
│   │   │   └── [id]/
│   │   │       ├── page.tsx    # book detail (copies count, status)
│   │   │       └── edit/page.tsx
│   │   ├── members/
│   │   │   ├── page.tsx        # ListMembers
│   │   │   ├── new/page.tsx
│   │   │   └── [id]/
│   │   │       ├── page.tsx    # member detail + loan history (active + returned)
│   │   │       └── edit/page.tsx
│   │   └── loans/
│   │       ├── page.tsx        # all loans, filter chips
│   │       └── new/page.tsx    # the borrow flow: pick member → pick book → confirm
│   ├── components/
│   │   ├── ui/                 # buttons, inputs, table, pagination, toast
│   │   ├── BookForm.tsx
│   │   ├── MemberForm.tsx
│   │   ├── BorrowDialog.tsx
│   │   └── ReturnButton.tsx
│   ├── lib/
│   │   ├── client.ts           # createPromiseClient(LibraryService, createGrpcWebTransport({baseUrl: ENVOY_URL}))
│   │   ├── queryKeys.ts        # central TanStack Query key factory
│   │   └── format.ts           # date/timestamp + currency formatters
│   └── generated/              # Connect-generated TS — gitignored, regenerated on build
│       └── library/v1/
│           ├── library_pb.ts
│           └── library_connect.ts
└── public/
```

---

## 2. gRPC-Web client choice

The decision is between `protoc-gen-grpc-web` (Google's older codegen) and `@bufbuild/protobuf` + `@connectrpc/connect-web` (the newer Buf/Connect ecosystem). **We pick Connect.** It's actively maintained, has better TypeScript types, the `buf` CLI is a one-stop codegen tool, and `connect-web` speaks the gRPC-Web protocol that Envoy serves. The older `protoc-gen-grpc-web` works but its tooling has stagnated.

See [reference/decisions.md](../reference/decisions.md) row 5 for the tradeoff and risk.

---

## 3. Data-fetching pattern

- Every list page wraps a single `useQuery` keyed by request params (search, page, filter).
- Mutations (`CreateBook`, `BorrowBook`, etc.) use `useMutation` with `onSuccess` invalidating the relevant query keys.
- A central `lib/queryKeys.ts` exports factories like `bookKeys.list({search, offset})` so invalidation is type-safe.
- Loading states render skeleton rows; error states render an inline alert with the gRPC status code mapped to a friendly message.

---

## 4. Page responsibilities

- **`/`** — at-a-glance count tiles (total books, members, active loans, overdue, **total outstanding fines**) plus a "Recent activity" feed of the last 10 loans.
- **`/books`** — paginated, searchable table; row click → detail; "New book" button → form.
- **`/books/new`** — create form (title, author, ISBN, published year, number of copies).
- **`/books/[id]`** — title, author, ISBN, year, total/available copies, "Edit" link.
- **`/books/[id]/edit`** — same form as create, plus copy-count reconciliation (server enforces "can't drop below currently borrowed").
- **`/members`** — paginated, searchable table; row click → member detail.
- **`/members/new`** — create form (name, email, optional phone, optional address).
- **`/members/[id]`** — member info, **outstanding-fines tile** (renders only when `outstanding_fines_cents > 0`), tabbed loan history (Active / Returned / All) with "Return" buttons on active rows. Each loan row in the table shows a fine column when applicable.
- **`/members/[id]/edit`** — same form as create.
- **`/loans`** — global loan list with filter chips (Active / Overdue / **Has Fine** / Returned). Fine column rendered with currency formatting; rows where `fine_cents > 0` are visually highlighted.
- **`/loans/new`** — the borrow flow: search-and-pick a member, search-and-pick a book with available copies, optional due date override, submit.

---

## 5. Error UX

- Any non-`OK` gRPC status renders a toast with the friendly message.
- `INVALID_ARGUMENT` highlights the offending form field where possible (parse the message or use a structured detail field — start with parsing).
- `FAILED_PRECONDITION` on borrow ("no copies available") and return ("already returned") show a clear inline message rather than a toast.

---

## 6. Currency formatting

All `*_cents` fields are formatted via `lib/format.ts`:

```ts
export const formatCents = (cents: number) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' })
    .format(cents / 100);
```

(Currency is hardcoded to USD for the take-home; configurable would be future work.)

---

## Cross-references

- Wire contract this client consumes: [design/02-api-contract.md](02-api-contract.md)
- Envoy that bridges browser ↔ server: [design/05-infrastructure.md](05-infrastructure.md)
- Codegen mechanics: [phases/phase-3-proto-codegen.md](../phases/phase-3-proto-codegen.md)
- The acceptance demo for the full UI: [phases/phase-6-frontend-mvp.md](../phases/phase-6-frontend-mvp.md)
