"""Resilience layer: timeouts, pool tuning, and the service-level retry decorator.

Phase 5.6 deliverable. Re-exports the public surface so callers say
``from library.resilience import with_retry, RETRY_READ`` rather than
reaching into individual submodules.
"""

from library.resilience.classify import (
    ErrorClass,
    classify,
    is_classified_transient,
)
from library.resilience.deadline import (
    DEADLINE_VAR,
    Deadline,
    set_deadline_from_grpc_context,
    time_remaining,
)
from library.resilience.decorator import RETRY_ATTEMPTS_VAR, with_retry
from library.resilience.policies import (
    RETRY_NEVER,
    RETRY_READ,
    RETRY_WRITE_TX,
    RetryPolicy,
)

__all__ = [
    "DEADLINE_VAR",
    "Deadline",
    "ErrorClass",
    "RETRY_ATTEMPTS_VAR",
    "RETRY_NEVER",
    "RETRY_READ",
    "RETRY_WRITE_TX",
    "RetryPolicy",
    "classify",
    "is_classified_transient",
    "set_deadline_from_grpc_context",
    "time_remaining",
    "with_retry",
]
