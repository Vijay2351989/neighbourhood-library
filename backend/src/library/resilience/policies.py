"""Three named retry policies — see ``docs/phases/phase-5-6-resilience.md`` §"Design decisions D3".

Free-form per-call policy configuration is intentionally not provided. A
reviewer should be able to glance at any service method and tell at a glance
which of these three classes it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from library.resilience.classify import ErrorClass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Immutable retry-policy descriptor.

    Attributes:
        name: Human-readable label, surfaced in span events for filtering.
        attempts: Total number of attempts (>=1). ``attempts=1`` disables retry.
        backoff_base_s: First-retry backoff before jitter, in seconds.
        backoff_cap_s: Upper bound on the per-attempt backoff after exponential
            scaling but before jitter.
        jitter_pct: Fraction (0..1) by which the computed backoff is randomly
            perturbed up or down. ``0.25`` means ``±25%``.
        retryable: Set of :class:`ErrorClass` values this policy will retry on.
            Anything else surfaces immediately on first attempt.
    """

    name: str
    attempts: int
    backoff_base_s: float
    backoff_cap_s: float
    jitter_pct: float
    retryable: frozenset[ErrorClass]


# ---- the three sanctioned policies ----

RETRY_READ: Final[RetryPolicy] = RetryPolicy(
    name="RETRY_READ",
    attempts=3,
    backoff_base_s=0.05,
    backoff_cap_s=1.0,
    jitter_pct=0.25,
    retryable=frozenset(
        {
            ErrorClass.DEADLOCK,
            ErrorClass.SERIALIZATION,
            ErrorClass.LOCK_TIMEOUT,
            ErrorClass.CONNECTION_DROPPED,
            ErrorClass.POOL_TIMEOUT,
            ErrorClass.STATEMENT_TIMEOUT,
        }
    ),
)


RETRY_WRITE_TX: Final[RetryPolicy] = RetryPolicy(
    name="RETRY_WRITE_TX",
    attempts=2,
    backoff_base_s=0.05,
    backoff_cap_s=0.5,
    jitter_pct=0.25,
    # Notably NOT CONNECTION_DROPPED (could've committed) or
    # STATEMENT_TIMEOUT (same — ambiguous mid-commit). The retryable set
    # here is the union of "tx aborted before any commit could happen".
    retryable=frozenset(
        {
            ErrorClass.DEADLOCK,
            ErrorClass.SERIALIZATION,
            ErrorClass.LOCK_TIMEOUT,
            ErrorClass.POOL_TIMEOUT,
        }
    ),
)


RETRY_NEVER: Final[RetryPolicy] = RetryPolicy(
    name="RETRY_NEVER",
    attempts=1,
    backoff_base_s=0.0,
    backoff_cap_s=0.0,
    jitter_pct=0.0,
    retryable=frozenset(),
)


__all__ = ["RETRY_NEVER", "RETRY_READ", "RETRY_WRITE_TX", "RetryPolicy"]
