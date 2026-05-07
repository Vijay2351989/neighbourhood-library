"""Pure-function tests for the fine formula.

These exercise :func:`library.services.fines.compute_fine_cents` against
every row in the policy table from
[design/01-database.md §5](../../docs/design/01-database.md). No DB, no
proto, no async — just arithmetic.

Defaults match the env-driven defaults in :mod:`library.config`:

* grace_days = 14
* per_day_cents = 25     ($0.25/day)
* cap_cents = 2000       ($20.00 cap)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from library.services.fines import compute_fine_cents

GRACE = 14
PER_DAY = 25
CAP = 2000

BASE = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _fine(*, due_at, returned_at, now):
    return compute_fine_cents(
        due_at=due_at,
        returned_at=returned_at,
        now=now,
        grace_days=GRACE,
        per_day_cents=PER_DAY,
        cap_cents=CAP,
    )


# ---------- active loans ----------


def test_active_within_grace_no_fine():
    """Borrowed but not yet past the grace period → fine is zero."""

    fine = _fine(due_at=BASE, returned_at=None, now=BASE + timedelta(days=10))
    assert fine == 0


def test_active_exactly_at_grace_boundary_no_fine():
    """``days_past_grace == 0`` -> still zero. The day the grace ends, the
    library is still being lenient."""

    fine = _fine(due_at=BASE, returned_at=None, now=BASE + timedelta(days=14))
    assert fine == 0


def test_active_one_day_past_grace_charges_one_day():
    """First day after grace: $0.25."""

    fine = _fine(due_at=BASE, returned_at=None, now=BASE + timedelta(days=15))
    assert fine == PER_DAY


def test_active_mid_fine_accrues_linearly():
    """10 days past grace -> $2.50."""

    fine = _fine(due_at=BASE, returned_at=None, now=BASE + timedelta(days=24))
    assert fine == 10 * PER_DAY


def test_active_at_cap_exactly():
    """80 days past grace × $0.25 = $20.00 = cap. Fine equals cap exactly."""

    days_to_reach_cap = CAP // PER_DAY  # 80
    fine = _fine(
        due_at=BASE,
        returned_at=None,
        now=BASE + timedelta(days=GRACE + days_to_reach_cap),
    )
    assert fine == CAP


def test_active_beyond_cap_clamps():
    """Months overdue: fine never exceeds cap."""

    fine = _fine(due_at=BASE, returned_at=None, now=BASE + timedelta(days=200))
    assert fine == CAP


# ---------- returned loans ----------


def test_returned_within_grace_no_fine():
    """Returned 14 days late -> still inside grace -> no fine, even on
    re-rendering well after the fact."""

    fine = _fine(
        due_at=BASE,
        returned_at=BASE + timedelta(days=14),
        now=BASE + timedelta(days=200),
    )
    assert fine == 0


def test_returned_past_grace_snapshot_at_return():
    """Returned 20 days late -> snapshot fine = 6 days × $0.25 = $1.50.

    The fine is frozen at the moment of return; ``now`` no longer
    influences the computation once ``returned_at`` is set.
    """

    fine = _fine(
        due_at=BASE,
        returned_at=BASE + timedelta(days=20),
        now=BASE + timedelta(days=200),
    )
    assert fine == 6 * PER_DAY


def test_returned_past_grace_snapshot_above_cap_clamps():
    """A loan returned 1000 days late should still clamp to the cap."""

    fine = _fine(
        due_at=BASE,
        returned_at=BASE + timedelta(days=1000),
        now=BASE + timedelta(days=2000),
    )
    assert fine == CAP


# ---------- edge cases ----------


def test_returned_before_due_no_fine():
    """Returned early (or on time) -> zero. ``days_overdue`` would be
    non-positive, the formula short-circuits to zero."""

    fine = _fine(
        due_at=BASE,
        returned_at=BASE - timedelta(days=1),
        now=BASE + timedelta(days=200),
    )
    assert fine == 0


@pytest.mark.parametrize(
    ("days_overdue", "expected_fine"),
    [
        (0, 0),
        (13, 0),
        (14, 0),       # boundary
        (15, 25),
        (20, 6 * 25),  # returned-late snapshot pattern
        (94, 2000),    # exactly at cap
        (1000, 2000),  # well beyond cap
    ],
)
def test_active_fine_progression(days_overdue: int, expected_fine: int):
    """Sweep across the grace boundary, mid-fine zone, and cap."""

    fine = _fine(
        due_at=BASE,
        returned_at=None,
        now=BASE + timedelta(days=days_overdue),
    )
    assert fine == expected_fine
