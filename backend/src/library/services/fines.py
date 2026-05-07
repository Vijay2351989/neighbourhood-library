"""Pure-function fine arithmetic.

The formula is the canonical reference for the policy described in
[docs/design/01-database.md §5](../../../docs/design/01-database.md). It does
not touch I/O, the database, the protobuf modules, or settings — those are
all the caller's responsibility. The signature exposes every knob the policy
mentions so this module can also be re-used at query time inside a SQL
expression (the SQL form lives in :mod:`library.repositories.loans`; both
must agree on the arithmetic).

The function is the unit-testable heart of fine handling: every behavior row
in the policy table (within grace, at the boundary, mid-fine, at cap, past
cap, returned within grace, returned past grace) maps to a single call.
"""

from __future__ import annotations

from datetime import datetime


def compute_fine_cents(
    *,
    due_at: datetime,
    returned_at: datetime | None,
    now: datetime,
    grace_days: int,
    per_day_cents: int,
    cap_cents: int,
) -> int:
    """Return the fine in cents accrued on a single loan.

    The "reference time" against which days-overdue are measured is

    * ``returned_at`` when the loan has been returned (a snapshot — the fine
      is frozen at the moment of return);
    * ``now`` while the loan is still active (the fine continues to accrue
      day-by-day until either return or the cap is reached).

    The number of overdue days uses Python's ``timedelta.days``, which is the
    integer-floor of the elapsed days. ``days_past_grace`` then subtracts the
    grace period; if negative or zero, the fine is zero. Otherwise the fine
    is ``days_past_grace * per_day_cents`` capped at ``cap_cents``.
    """

    reference = returned_at if returned_at is not None else now
    days_overdue = (reference - due_at).days
    days_past_grace = days_overdue - grace_days
    if days_past_grace <= 0:
        return 0
    return min(cap_cents, days_past_grace * per_day_cents)


__all__ = ["compute_fine_cents"]
