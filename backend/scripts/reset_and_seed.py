"""Reset and seed the library database for the DEMO_MODE bring-up.

Intent
------
Populate the library schema with a representative cross-section of state
that exercises every screen and every code path the reviewer might want
to look at:

  * ~20 books (with realistic titles, authors, ISBNs, and a variety of
    copy counts) — enough to drive Books list pagination and search.
  * ~10 members — enough to drive Members list and the borrow flow's
    member picker.
  * ~5 active (un-returned) loans, mostly within their loan window —
    "everything is fine" rows.
  * ~3 returned loans, returned on time — historical records.
  * 1 overdue loan within the 14-day grace period (``due_at`` 7 days
    ago) — visible as overdue in the UI but with ``fine_cents = 0``.
  * 1 overdue loan past grace, currently accruing a fine
    (``due_at`` 30 days ago) — the actively-accruing fines case.
  * 1 returned-late loan (returned after grace expired) — the snapshot
    fines case, where the fine sticks to the loan record forever.

The three fine scenarios above match the rows in
``docs/design/01-database.md §5`` so a reviewer can open any of three
member detail pages and see the policy actually enforced on real data.

Why this is direct DB writes (not the gRPC API)
-----------------------------------------------
This script runs at container start (from ``entrypoint.sh``) **before**
the gRPC server has bound its socket. It therefore cannot dial the
public API. Instead it talks to Postgres directly through the same
``AsyncSessionLocal`` and ORM models the application uses, which keeps
us honest about schema fidelity without standing up the API surface.

A second reason direct writes are necessary: the gRPC API (correctly)
does not expose ``borrowed_at`` or ``due_at`` as caller-supplied
parameters — they are server-controlled to prevent clients from
constructing fraudulent loan history. Backdating loans for the three
fine demos is intrinsically a database-administrator action, not a
public-API action. Doing it via SQL also documents that fact.

Idempotency
-----------
Step 1 of this script is a single ``TRUNCATE TABLE ... RESTART IDENTITY
CASCADE`` over all four tables. Re-running the script therefore yields
the same final state byte-for-byte (modulo the per-row timestamps,
which are computed relative to ``NOW()`` so the fine math stays
correct). This is what lets ``DEMO_MODE=true docker compose restart
api`` produce a clean repeatable demo state.

Operational notes
-----------------
* The script is invoked as ``python /app/scripts/reset_and_seed.py``
  from inside the api container, so the working directory is ``/app``
  and ``library.*`` is importable from the venv.
* Logging goes to stdout via the standard ``logging`` module; the
  entrypoint surfaces it through ``docker compose logs api``.
* Exits non-zero on any failure so the container start fails fast
  rather than launching the gRPC server against a half-seeded DB.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# When invoked as ``python /app/scripts/reset_and_seed.py``, ``sys.path[0]``
# is ``/app/scripts`` and the ``library`` package wouldn't resolve. Add the
# venv's site-packages location implicitly by adding ``/app/src`` if it
# exists (covers running from a source checkout on the host); inside the
# container the package is installed into the venv and resolves directly.
_repo_src = Path(__file__).resolve().parent.parent / "src"
if _repo_src.is_dir() and str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import get_settings
from library.db.engine import AsyncSessionLocal, get_engine
from library.db.models import Book, BookCopy, CopyStatus, Loan, Member


logger = logging.getLogger("library.scripts.reset_and_seed")


# ---------------------------------------------------------------------------
# Static seed catalogues. Kept in module-scope tuples so the data is easy to
# eyeball, easy to extend, and so successive runs of the script produce
# byte-identical content (modulo NOW()-relative timestamps).
# ---------------------------------------------------------------------------

# (title, author, isbn, published_year, copies)
_BOOK_CATALOGUE: tuple[tuple[str, str, str | None, int | None, int], ...] = (
    ("Dune", "Frank Herbert", "9780441172719", 1965, 3),
    ("The Left Hand of Darkness", "Ursula K. Le Guin", "9780441478125", 1969, 2),
    ("Foundation", "Isaac Asimov", "9780553293357", 1951, 2),
    ("Neuromancer", "William Gibson", "9780441569595", 1984, 2),
    ("Snow Crash", "Neal Stephenson", "9780553380958", 1992, 1),
    ("A Wizard of Earthsea", "Ursula K. Le Guin", "9780547773742", 1968, 2),
    ("The Dispossessed", "Ursula K. Le Guin", "9780061054884", 1974, 1),
    ("Hyperion", "Dan Simmons", "9780553283686", 1989, 2),
    ("The Stars My Destination", "Alfred Bester", "9780679767800", 1956, 1),
    ("A Canticle for Leibowitz", "Walter M. Miller Jr.", "9780060892999", 1959, 1),
    ("Stranger in a Strange Land", "Robert A. Heinlein", "9780441788385", 1961, 2),
    ("The Forever War", "Joe Haldeman", "9780312536633", 1974, 1),
    ("Ringworld", "Larry Niven", "9780345333926", 1970, 1),
    ("Childhood's End", "Arthur C. Clarke", "9780345347954", 1953, 1),
    ("The Moon Is a Harsh Mistress", "Robert A. Heinlein", "9780312863555", 1966, 1),
    ("Gateway", "Frederik Pohl", "9780345475831", 1977, 1),
    ("Old Man's War", "John Scalzi", "9780765348272", 2005, 2),
    ("Ancillary Justice", "Ann Leckie", "9780316246620", 2013, 2),
    ("The Fifth Season", "N. K. Jemisin", "9780316229296", 2015, 2),
    ("A Memory Called Empire", "Arkady Martine", "9781250186430", 2019, 2),
    # A book with no ISBN, exercising the nullable-isbn code path on the
    # books page. Local zine, single copy.
    ("The Library Pamphlet, Vol. 3", "Neighborhood Library Staff", None, 2024, 1),
)


# (name, email, phone-or-None, address-or-None)
_MEMBER_ROSTER: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("Ada Lovelace",        "ada@example.com",        "+1-555-0101", "12 Babbage Lane, Analytica"),
    ("Grace Hopper",        "grace@example.com",      "+1-555-0102", "200 Compiler Way, Arlington"),
    ("Alan Turing",         "alan@example.com",       None,           "Bletchley Park"),
    ("Katherine Johnson",   "katherine@example.com",  "+1-555-0104", "1000 NASA Rd, Hampton"),
    ("Linus Torvalds",      "linus@example.com",      "+1-555-0105", None),
    ("Margaret Hamilton",   "margaret@example.com",   "+1-555-0106", "Apollo Apartments"),
    ("Donald Knuth",        "knuth@example.com",      None,           "Stanford, CA"),
    ("Barbara Liskov",      "liskov@example.com",     "+1-555-0108", "MIT, Cambridge"),
    ("Tim Berners-Lee",     "timbl@example.com",      "+1-555-0109", "CERN, Geneva"),
    ("Edsger Dijkstra",     "ewd@example.com",        None,           "Eindhoven"),
)


# Loan plan — entries describe what happens to a particular (book, member)
# pairing. We resolve "first available copy of book N" at insert time; the
# member is referenced by 1-based index into _MEMBER_ROSTER.
#
# ``kind`` controls the time-shape:
#   active_recent    — borrowed_at = now - days_ago, due in future, not returned
#   returned_ontime  — borrowed and returned cleanly inside the loan window
#   overdue_in_grace — overdue but within the 14-day grace window (no fine)
#   overdue_accruing — overdue past grace, fine accruing right now
#   returned_late    — returned after grace, fine snapshot stuck on the row
#
# These are illustrative, not exhaustive; they exercise every UI tab.

_ACTIVE_LOANS: tuple[tuple[int, int, int], ...] = (
    # (book index, member index, days_since_borrowed)
    (0, 0, 1),    # Dune → Ada,        borrowed yesterday
    (1, 1, 3),    # Left Hand → Grace, borrowed 3 days ago
    (3, 4, 7),    # Neuromancer → Linus, borrowed 1 week ago
    (7, 5, 2),    # Hyperion → Margaret
    (16, 8, 5),   # Old Man's War → Tim
)

_RETURNED_ONTIME: tuple[tuple[int, int, int, int], ...] = (
    # (book index, member index, days_since_borrowed, days_loan_held)
    (2, 0, 30, 10),   # Foundation → Ada, returned cleanly
    (5, 2, 50, 12),   # Earthsea → Alan
    (10, 6, 60, 14),  # Stranger → Knuth
)


def _build_overdue_in_grace_plan() -> dict[str, int]:
    """7 days overdue (within the 14-day grace window) → overdue=true, fine=0."""
    return {
        "book_index": 11,        # Forever War
        "member_index": 3,       # Katherine
        "days_since_borrowed": 21,
        "days_since_due": 7,
    }


def _build_overdue_accruing_plan() -> dict[str, int]:
    """30 days overdue (past 14-day grace) → fine accruing at $0.25/day."""
    return {
        "book_index": 13,        # Childhood's End
        "member_index": 7,       # Liskov
        "days_since_borrowed": 44,
        "days_since_due": 30,
    }


def _build_returned_late_plan() -> dict[str, int]:
    """Returned 30 days late, after grace → snapshot fine on the loan row."""
    return {
        "book_index": 14,        # Moon Is a Harsh Mistress
        "member_index": 9,       # Dijkstra
        "days_since_borrowed": 60,
        "days_since_due": 46,
        "days_since_returned": 16,  # returned 16 days ago, well past grace
    }


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


async def _truncate_all(session: AsyncSession) -> None:
    """Reset all four tables and their primary-key sequences.

    A single ``TRUNCATE`` with ``RESTART IDENTITY CASCADE`` is the cleanest
    way to wipe both the rows and the BIGSERIAL sequences in one round trip.
    Listing all four tables explicitly (rather than relying on CASCADE to
    chase foreign-key chains) keeps the statement self-documenting.
    """

    logger.info("seed: truncating books, members, book_copies, loans")
    await session.execute(
        text("TRUNCATE TABLE loans, book_copies, books, members RESTART IDENTITY CASCADE")
    )


async def _seed_books(session: AsyncSession) -> list[Book]:
    """Insert the static book catalogue and their copies.

    Returns the list of persisted Book rows in catalogue order so the loan
    seeding step can resolve "the first available copy of book N" by index
    without an extra query.
    """

    logger.info("seed: inserting %d books", len(_BOOK_CATALOGUE))
    books: list[Book] = []
    for title, author, isbn, year, copies in _BOOK_CATALOGUE:
        book = Book(title=title, author=author, isbn=isbn, published_year=year)
        session.add(book)
        await session.flush()  # populate book.id for the BookCopy FKs

        session.add_all(
            BookCopy(book_id=book.id, status=CopyStatus.AVAILABLE)
            for _ in range(copies)
        )
        books.append(book)
    await session.flush()
    return books


async def _seed_members(session: AsyncSession) -> list[Member]:
    """Insert the static member roster.

    Order is preserved so loan plans can reference members by 1-based index
    into the catalogue.
    """

    logger.info("seed: inserting %d members", len(_MEMBER_ROSTER))
    members: list[Member] = []
    for name, email, phone, address in _MEMBER_ROSTER:
        member = Member(name=name, email=email, phone=phone, address=address)
        session.add(member)
        members.append(member)
    await session.flush()
    return members


async def _checkout_first_available_copy(
    session: AsyncSession,
    book: Book,
) -> BookCopy:
    """Pick the lowest-id AVAILABLE copy of ``book`` and flip it to BORROWED.

    The seed runs single-threaded inside one transaction, so we don't need
    ``FOR UPDATE SKIP LOCKED`` here — the partial unique index on
    ``loans(copy_id) WHERE returned_at IS NULL`` still keeps us honest if a
    plan accidentally tried to double-borrow the same copy.
    """

    from sqlalchemy import select

    stmt = (
        select(BookCopy)
        .where(BookCopy.book_id == book.id, BookCopy.status == CopyStatus.AVAILABLE)
        .order_by(BookCopy.id.asc())
        .limit(1)
    )
    result = await session.execute(stmt)
    copy = result.scalar_one_or_none()
    if copy is None:
        raise RuntimeError(
            f"seed plan error: no AVAILABLE copy of book id={book.id} "
            f"({book.title!r}) — increase the catalogue's copy count or "
            f"re-target the loan plan."
        )
    copy.status = CopyStatus.BORROWED
    return copy


async def _seed_loans(
    session: AsyncSession,
    books: list[Book],
    members: list[Member],
    *,
    now: datetime,
    default_loan_days: int,
) -> None:
    """Create every active, returned, and historical-date loan from the plans.

    Active and returned-on-time loans use ``borrowed_at`` close to ``now``,
    so they're indistinguishable from loans created via the API. The three
    fine-scenario loans backdate ``borrowed_at`` and ``due_at`` so the
    fine-policy code paths in ``services/fines.py`` and the SQL
    ``_fine_expression`` light up against real data.
    """

    # ---- Active recent loans ----
    logger.info("seed: creating %d active recent loans", len(_ACTIVE_LOANS))
    for book_idx, member_idx, days_ago in _ACTIVE_LOANS:
        book = books[book_idx]
        member = members[member_idx]
        copy = await _checkout_first_available_copy(session, book)

        borrowed_at = now - timedelta(days=days_ago)
        due_at = borrowed_at + timedelta(days=default_loan_days)
        session.add(
            Loan(
                copy_id=copy.id,
                member_id=member.id,
                borrowed_at=borrowed_at,
                due_at=due_at,
                returned_at=None,
            )
        )

    # ---- Returned-on-time loans ----
    logger.info("seed: creating %d returned-on-time loans", len(_RETURNED_ONTIME))
    for book_idx, member_idx, days_borrowed_ago, hold_days in _RETURNED_ONTIME:
        book = books[book_idx]
        member = members[member_idx]
        # Returned-on-time loans need an AVAILABLE copy at borrow time, then
        # we put it back AVAILABLE because the loan is returned. Net effect
        # on copy status: no change. We still flip-and-flip so the seed
        # exercises the same path as a real return.
        copy = await _checkout_first_available_copy(session, book)
        borrowed_at = now - timedelta(days=days_borrowed_ago)
        due_at = borrowed_at + timedelta(days=default_loan_days)
        returned_at = borrowed_at + timedelta(days=hold_days)
        session.add(
            Loan(
                copy_id=copy.id,
                member_id=member.id,
                borrowed_at=borrowed_at,
                due_at=due_at,
                returned_at=returned_at,
            )
        )
        copy.status = CopyStatus.AVAILABLE  # back on the shelf

    # ---- Overdue within grace (no fine yet) ----
    plan = _build_overdue_in_grace_plan()
    logger.info("seed: creating overdue-in-grace loan (no fine yet)")
    book = books[plan["book_index"]]
    member = members[plan["member_index"]]
    copy = await _checkout_first_available_copy(session, book)
    borrowed_at = now - timedelta(days=plan["days_since_borrowed"])
    due_at = now - timedelta(days=plan["days_since_due"])
    session.add(
        Loan(
            copy_id=copy.id,
            member_id=member.id,
            borrowed_at=borrowed_at,
            due_at=due_at,
            returned_at=None,
        )
    )

    # ---- Overdue past grace, accruing fine ----
    plan = _build_overdue_accruing_plan()
    logger.info("seed: creating overdue-past-grace loan (fine accruing)")
    book = books[plan["book_index"]]
    member = members[plan["member_index"]]
    copy = await _checkout_first_available_copy(session, book)
    borrowed_at = now - timedelta(days=plan["days_since_borrowed"])
    due_at = now - timedelta(days=plan["days_since_due"])
    session.add(
        Loan(
            copy_id=copy.id,
            member_id=member.id,
            borrowed_at=borrowed_at,
            due_at=due_at,
            returned_at=None,
        )
    )

    # ---- Returned late (snapshot fine) ----
    plan = _build_returned_late_plan()
    logger.info("seed: creating returned-late loan (snapshot fine)")
    book = books[plan["book_index"]]
    member = members[plan["member_index"]]
    copy = await _checkout_first_available_copy(session, book)
    borrowed_at = now - timedelta(days=plan["days_since_borrowed"])
    due_at = now - timedelta(days=plan["days_since_due"])
    returned_at = now - timedelta(days=plan["days_since_returned"])
    session.add(
        Loan(
            copy_id=copy.id,
            member_id=member.id,
            borrowed_at=borrowed_at,
            due_at=due_at,
            returned_at=returned_at,
        )
    )
    copy.status = CopyStatus.AVAILABLE  # was returned (late, but returned)

    await session.flush()


async def _summarize(session: AsyncSession) -> None:
    """Log a one-line summary of the final seeded state."""

    counts = {
        "books": "SELECT count(*) FROM books",
        "members": "SELECT count(*) FROM members",
        "copies": "SELECT count(*) FROM book_copies",
        "loans_total": "SELECT count(*) FROM loans",
        "loans_active": "SELECT count(*) FROM loans WHERE returned_at IS NULL",
        "loans_overdue": (
            "SELECT count(*) FROM loans "
            "WHERE returned_at IS NULL AND due_at < NOW()"
        ),
    }
    out: dict[str, int] = {}
    for label, sql in counts.items():
        result = await session.execute(text(sql))
        out[label] = int(result.scalar_one())
    logger.info(
        "seed: summary — books=%d members=%d copies=%d "
        "loans_total=%d loans_active=%d loans_overdue=%d",
        out["books"],
        out["members"],
        out["copies"],
        out["loans_total"],
        out["loans_active"],
        out["loans_overdue"],
    )


async def _run() -> None:
    """End-to-end: truncate, seed, summarize.

    Wraps every write in a single ``session.begin()`` so the DB is either
    fully reseeded or untouched (modulo the truncate, which is the first
    statement in that same transaction). Failure here exits the script
    non-zero so the entrypoint aborts before launching the gRPC server.
    """

    settings = get_settings()
    now = datetime.now(timezone.utc)

    logger.info("seed: starting reset_and_seed against %s", settings.database_url.split("@")[-1])
    async with AsyncSessionLocal.begin() as session:
        await _truncate_all(session)
        books = await _seed_books(session)
        members = await _seed_members(session)
        await _seed_loans(
            session,
            books,
            members,
            now=now,
            default_loan_days=settings.default_loan_days,
        )
        await _summarize(session)

    # Dispose the engine so the gRPC server starts with a fresh, untainted
    # pool. Cheap; the engine is recreated lazily on the first request.
    await get_engine().dispose()
    logger.info("seed: complete")


def main() -> None:
    """CLI entry point.

    Configures a basic root logger so output shows up under
    ``docker compose logs api`` even when invoked outside the
    OpenTelemetry-configured server runtime.
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run())
    except Exception:
        logger.exception("seed: failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
