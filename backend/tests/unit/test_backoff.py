"""Unit tests for ``library.resilience.backoff.compute_backoff``.

Pure-function module — easy to assert exact values with a fixed RNG.
"""

from __future__ import annotations

import pytest

from library.resilience.backoff import compute_backoff
from library.resilience.policies import (
    RETRY_NEVER,
    RETRY_READ,
    RETRY_WRITE_TX,
)


def _rng_at(value: float):
    """Return a closure that always emits ``value`` — turns jitter deterministic."""

    return lambda: value


def test_attempt_one_has_no_delay() -> None:
    """No retry has happened yet; the first attempt fires immediately."""

    assert compute_backoff(attempt=1, policy=RETRY_READ) == 0.0


def test_retry_never_returns_zero_for_any_attempt() -> None:
    for n in range(1, 5):
        assert compute_backoff(attempt=n, policy=RETRY_NEVER) == 0.0


def test_exponential_growth_at_jitter_midpoint() -> None:
    """With rng()=0.5, jitter cancels out and we see the raw exponential value."""

    # attempt=2 -> base * 2**0 = 0.05
    # attempt=3 -> base * 2**1 = 0.10
    # attempt=4 -> base * 2**2 = 0.20 (still under 1.0 cap)
    assert compute_backoff(attempt=2, policy=RETRY_READ, rng=_rng_at(0.5)) == pytest.approx(
        0.05
    )
    assert compute_backoff(attempt=3, policy=RETRY_READ, rng=_rng_at(0.5)) == pytest.approx(
        0.10
    )
    assert compute_backoff(attempt=4, policy=RETRY_READ, rng=_rng_at(0.5)) == pytest.approx(
        0.20
    )


def test_cap_clamps_long_runs() -> None:
    """A late attempt should hit the cap, not exponential blow-up."""

    # Very high attempt; raw would be huge, but cap binds.
    out = compute_backoff(attempt=20, policy=RETRY_READ, rng=_rng_at(0.5))
    assert out == pytest.approx(RETRY_READ.backoff_cap_s)


def test_jitter_stays_within_band() -> None:
    """For any rng() in [0,1], the result stays within +/- jitter_pct of the
    capped exponential."""

    # attempt=4 → un-jittered 0.20s, jitter ±25% → [0.15, 0.25]
    for r in (0.0, 0.25, 0.5, 0.75, 0.999999):
        v = compute_backoff(attempt=4, policy=RETRY_READ, rng=_rng_at(r))
        assert 0.15 - 1e-9 <= v <= 0.25 + 1e-9


def test_zero_jitter_pct_returns_exact_capped_value() -> None:
    """If a policy disables jitter entirely the result is deterministic."""

    from library.resilience.policies import RetryPolicy
    from library.resilience.classify import ErrorClass

    policy = RetryPolicy(
        name="TEST_NO_JITTER",
        attempts=4,
        backoff_base_s=0.1,
        backoff_cap_s=10.0,
        jitter_pct=0.0,
        retryable=frozenset({ErrorClass.DEADLOCK}),
    )
    # rng must NOT be consulted when jitter_pct=0; pass a callable that would
    # blow up if called.
    sentinel = lambda: (_ for _ in ()).throw(AssertionError("rng was called"))
    assert compute_backoff(attempt=2, policy=policy, rng=sentinel) == pytest.approx(0.1)
    assert compute_backoff(attempt=3, policy=policy, rng=sentinel) == pytest.approx(0.2)


def test_invalid_attempt_raises() -> None:
    with pytest.raises(ValueError):
        compute_backoff(attempt=0, policy=RETRY_READ)


def test_write_policy_has_tighter_cap() -> None:
    """RETRY_WRITE_TX must cap below RETRY_READ — the spec explicitly chose
    a smaller backoff cap for writes so retry budget is bounded."""

    long_attempt = 20
    read = compute_backoff(attempt=long_attempt, policy=RETRY_READ, rng=_rng_at(0.5))
    write = compute_backoff(attempt=long_attempt, policy=RETRY_WRITE_TX, rng=_rng_at(0.5))
    assert write < read
