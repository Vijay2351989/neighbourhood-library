"""Exponential backoff with jitter for the retry decorator.

Pure-function module; no I/O, no clock reads. The decorator owns sleeping
and clock interaction so this can be unit-tested with deterministic seeds.
"""

from __future__ import annotations

import random
from typing import Callable

from library.resilience.policies import RetryPolicy


def compute_backoff(
    *,
    attempt: int,
    policy: RetryPolicy,
    rng: Callable[[], float] | None = None,
) -> float:
    """Compute the seconds-to-sleep before retrying ``attempt``.

    ``attempt`` is 1-indexed: passing ``2`` means "delay before the second
    attempt", i.e. after the first failure. Returning a non-negative float;
    callers should clamp at zero before sleeping.

    The formula:

    1. ``raw = base * 2 ** (attempt - 1)`` — exponential growth.
    2. ``capped = min(raw, cap)`` — bound the worst case.
    3. ``jittered = capped * (1 + jitter_pct * (2*r - 1))`` for r in [0,1).

    With ``base=0.05`` and ``cap=1.0``:

    * attempt=2 → ~0.05s ± 25%
    * attempt=3 → ~0.10s ± 25%
    * attempt=4 → ~0.20s ± 25%
    * attempt=5 → ~0.40s ± 25%
    * attempt≥6 → ~1.00s ± 25% (cap binds)
    """

    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    if policy.attempts <= 1 or attempt == 1:
        # No retry, or "delay before first attempt" — neither needs a wait.
        return 0.0

    raw = policy.backoff_base_s * (2 ** (attempt - 2))
    capped = min(raw, policy.backoff_cap_s)
    if policy.jitter_pct <= 0.0:
        return max(0.0, capped)

    r = (rng or random.random)()
    jittered = capped * (1.0 + policy.jitter_pct * (2.0 * r - 1.0))
    return max(0.0, jittered)


__all__ = ["compute_backoff"]
