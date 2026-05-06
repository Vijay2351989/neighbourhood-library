# Database Design

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)
**Implemented in:** [Phase 2](../phases/phase-2-schema-migrations.md)
**Used by:** [Phase 4](../phases/phase-4-backend-crud.md), [Phase 5](../phases/phase-5-borrow-return-fines.md)

PostgreSQL 16. All timestamps use `TIMESTAMPTZ` (timezone-aware) — the application stores UTC, the UI renders in the staff member's local timezone.

---

## 1. DDL

```sql
-- Books: the abstract title. One row per ISBN/title-edition.
CREATE TABLE books (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT        NOT NULL,
    author          TEXT        NOT NULL,
    isbn            TEXT        NULL,        -- nullable; some old/local books have no ISBN
    published_year  INTEGER     NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Case-insensitive prefix search on title/author. Trigram extension is overkill
-- for a take-home, so we use plain B-tree on lower(...) for now.
CREATE INDEX books_title_lower_idx  ON books (lower(title));
CREATE INDEX books_author_lower_idx ON books (lower(author));

-- Members: library patrons.
CREATE TABLE members (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    email       TEXT        NOT NULL,
    phone       TEXT        NULL,
    address     TEXT        NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Email is the natural staff-facing identifier; enforce uniqueness.
CREATE UNIQUE INDEX members_email_unique_idx ON members (lower(email));

-- BookCopies: physical instances of a book.
CREATE TYPE copy_status AS ENUM ('AVAILABLE', 'BORROWED', 'LOST');

CREATE TABLE book_copies (
    id          BIGSERIAL PRIMARY KEY,
    book_id     BIGINT      NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    status      copy_status NOT NULL DEFAULT 'AVAILABLE',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX book_copies_book_id_status_idx ON book_copies (book_id, status);

-- Loans: a member borrowing a copy. returned_at NULL means active.
CREATE TABLE loans (
    id          BIGSERIAL PRIMARY KEY,
    copy_id     BIGINT      NOT NULL REFERENCES book_copies(id) ON DELETE RESTRICT,
    member_id   BIGINT      NOT NULL REFERENCES members(id)     ON DELETE RESTRICT,
    borrowed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at      TIMESTAMPTZ NOT NULL,
    returned_at TIMESTAMPTZ NULL
);

-- A given copy can have at most one active (un-returned) loan.
-- This is the structural guarantee against double-borrow.
CREATE UNIQUE INDEX loans_one_active_per_copy_idx
    ON loans (copy_id)
    WHERE returned_at IS NULL;

-- Common query indexes.
CREATE INDEX loans_member_active_idx ON loans (member_id) WHERE returned_at IS NULL;
CREATE INDEX loans_member_id_idx     ON loans (member_id);
CREATE INDEX loans_copy_id_idx       ON loans (copy_id);
```

---

## 2. Why each table looks this way

**`books`** — the abstract title. Two physical copies of *Dune* are one row here. `isbn` is nullable because real-world cataloging is messy: pre-ISBN books, locally produced zines, etc. We do not enforce uniqueness on ISBN: legitimate edge cases (different editions sharing an ISBN, data-entry typos that we correct later) make a unique constraint more painful than helpful for a small library.

**`members`** — straightforward. `email` is unique because staff use it to disambiguate humans with the same name. We compare lowercased to avoid `Bob@x.com` vs `bob@x.com` showing as different members.

**`book_copies`** — every physical book on the shelf is a row. Status tells us at a glance whether the copy is on the shelf, out on loan, or has been lost. The frontend hides this table entirely: when staff create a book they enter "title, author, ISBN, number of copies" and the server inserts the book row plus N copy rows in one transaction. When staff edit the count up or down, the server adds new copies (status `AVAILABLE`) or removes free copies (refusing to remove if the count would drop below the number currently `BORROWED`).

**`loans`** — one row per borrow event. `returned_at IS NULL` is the canonical "this loan is active" predicate. `due_at` is set at borrow time (default policy: 14 days from borrow). Overdue is a computed predicate (`returned_at IS NULL AND due_at < NOW()`), not a stored column. Fines are likewise computed (see §5 below).

---

## 3. Concurrency strategy: the partial unique index

We use **the partial unique index** (`loans_one_active_per_copy_idx`) as the primary defense against double-borrow, with `SELECT ... FOR UPDATE` as a secondary correctness aid inside the borrow transaction.

**Why the partial unique index is the cleaner option:**

- It is a database-level invariant. Even a buggy service can't violate it.
- It is declarative — the schema itself documents the rule.
- A unique-violation error is a clean, distinct signal we can map to `ALREADY_EXISTS` / `FAILED_PRECONDITION`.

**The borrow transaction flow:**

```
BEGIN;
  -- 1. Pick an available copy and lock it for the duration of this txn.
  SELECT id FROM book_copies
  WHERE book_id = $1 AND status = 'AVAILABLE'
  ORDER BY id
  LIMIT 1
  FOR UPDATE SKIP LOCKED;
  -- 2. If no row, abort with FAILED_PRECONDITION (no copies available).
  -- 3. Insert into loans (copy_id, member_id, borrowed_at, due_at).
  --    If two transactions race past step 1 somehow, the partial unique index rejects the second.
  -- 4. UPDATE book_copies SET status = 'BORROWED' WHERE id = <picked id>.
COMMIT;
```

`FOR UPDATE SKIP LOCKED` lets concurrent borrows pick *different* copies of the same book without blocking each other — important for popular titles with multiple copies.

---

## 4. Computing `available_copies`

Two viable approaches:

1. **Aggregate query** (recommended): `SELECT COUNT(*) FILTER (WHERE status = 'AVAILABLE') FROM book_copies WHERE book_id = $1`, joined into the book list query.
2. **Materialized view / denormalized counter** on `books` updated via triggers.

We pick **approach 1**. The book list is small (a neighborhood library has hundreds of titles, not millions) and the aggregate is fast with the `book_copies (book_id, status)` index. Denormalization adds invariant-maintenance complexity we don't need at this scale. The `ListBooks` RPC's SQL will look like:

```sql
SELECT b.*,
       COUNT(c.id)                                            AS total_copies,
       COUNT(c.id) FILTER (WHERE c.status = 'AVAILABLE')      AS available_copies
FROM books b
LEFT JOIN book_copies c ON c.book_id = b.id
WHERE (... search predicates ...)
GROUP BY b.id
ORDER BY b.title
LIMIT $page_size OFFSET $offset;
```

---

## 5. Fine policy (computed, not stored)

Fines accrue on overdue loans **after a 14-day grace period** past `due_at`. The schema does not change — fines are computed at query time, the same way `overdue` is.

### Formula

Pure function, easy to unit-test:

```python
def compute_fine_cents(
    due_at: datetime,
    returned_at: datetime | None,
    now: datetime,
    grace_days: int = 14,
    per_day_cents: int = 25,
    cap_cents: int = 2000,
) -> int:
    reference = returned_at if returned_at is not None else now
    days_past_grace = (reference - due_at).days - grace_days
    if days_past_grace <= 0:
        return 0
    return min(cap_cents, days_past_grace * per_day_cents)
```

### Defaults (env-configurable on the `api` service)

| Env var | Default | Meaning |
|---|---|---|
| `FINE_GRACE_DAYS`     | `14`   | Days past `due_at` before fines start accruing |
| `FINE_PER_DAY_CENTS`  | `25`   | $0.25 per overdue day after grace |
| `FINE_CAP_CENTS`      | `2000` | Maximum fine per loan: $20.00 |

### Behavior summary

| Loan state | Fine |
|---|---|
| Active or returned, still within `grace_days` of `due_at` | 0 |
| Active, overdue past grace | accrues per day, capped at `FINE_CAP_CENTS` |
| Returned after grace expired | snapshot as of `returned_at`; remains visible on the loan record forever |
| Returned before grace expired | 0 |

### Aggregating across loans (`Member.outstanding_fines_cents`)

Sum of `compute_fine_cents` over all of the member's loans. Active overdue loans contribute their currently-accruing value; returned-late loans contribute their snapshot. There is no "paid" state — once a fine exists it remains visible on the loan record. (Real-world: a payment ledger would clear these; out of scope here, see [00-overview.md §4 non-goals](../00-overview.md#4-explicit-non-goals).)

### Why computed and not stored

Storing fines would require a periodic job to update them as days tick over, plus a "today" timezone reference, plus invalidation when a loan is returned. Computing at query time avoids all three classes of bug. The per-row arithmetic cost is negligible at neighborhood-library scale.

### Concurrency note

Because fines are computed, no additional locking is needed for fine display. The borrow/return transactions don't touch any fine state.

---

## Cross-references

- Wire contract that exposes these fields: [design/02-api-contract.md](02-api-contract.md)
- Migration that creates this schema: [phases/phase-2-schema-migrations.md](../phases/phase-2-schema-migrations.md)
- Borrow/return implementation that uses §3 and §5: [phases/phase-5-borrow-return-fines.md](../phases/phase-5-borrow-return-fines.md)
- Decision rationales: [reference/decisions.md](../reference/decisions.md)
