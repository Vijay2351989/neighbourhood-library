"""``@with_retry(policy)`` — the service-level retry decorator.

Placement is deliberate: this decorator wraps **public service methods**,
never repository methods or gRPC interceptors. See
``docs/phases/phase-5-6-resilience.md`` §"Design decisions D1, D2".

Per call:

1. Loop up to ``policy.attempts`` times.
2. On retryable failures (per :func:`classify` + ``policy.retryable``),
   compute a jittered exponential backoff. Skip the sleep if the active
   gRPC deadline can't accommodate it; re-raise instead.
3. Emit a span event ``retry.attempt`` for attempts past the first so
   SigNoz dashboards can count retry pressure per RPC.
4. Increment :data:`RETRY_ATTEMPTS_VAR` so the access-log line carries the
   total attempts for this call (1 for unretried, 2/3 for retried).
5. After exhausting attempts, re-raise the **original** exception unwrapped
   so ``library.errors.map_domain_errors`` can map it to the right gRPC
   status without unwrapping any custom envelope.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar

from opentelemetry import trace

from library.resilience.backoff import compute_backoff
from library.resilience.classify import classify
from library.resilience.deadline import time_remaining
from library.resilience.policies import RetryPolicy

logger = logging.getLogger("library.resilience")

# Per-request retry counter, read by the access-log emitter.
# Default 1 — every RPC has at least one attempt. The decorator increments
# on retry; if the decorator never fires (no @with_retry on the method),
# the access log shows 1, which is correct.
RETRY_ATTEMPTS_VAR: Final[contextvars.ContextVar[int]] = contextvars.ContextVar(
    "library.resilience.retry_attempts", default=1
)


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def with_retry(policy: RetryPolicy) -> Callable[[F], F]:
    """Return a decorator that applies ``policy`` to an async function.

    The wrapped function should own its session lifecycle: each retry
    attempt re-runs the function from the top, opening a fresh
    ``async with AsyncSessionLocal.begin()``. Retrying inside an open
    transaction is invalid (Postgres aborts the tx on deadlock and refuses
    further work on it).
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            attempts_token = RETRY_ATTEMPTS_VAR.set(1)
            try:
                for attempt in range(1, policy.attempts + 1):
                    if attempt > 1:
                        RETRY_ATTEMPTS_VAR.set(attempt)
                    try:
                        return await fn(*args, **kwargs)
                    except BaseException as exc:  # noqa: BLE001 - we re-raise after classify
                        last_exc = exc
                        # Cancellation must propagate without retry; ditto KeyboardInterrupt /
                        # SystemExit. Classify only handles "real" exceptions.
                        if isinstance(
                            exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
                        ):
                            raise

                        cls = classify(exc)
                        if cls not in policy.retryable:
                            raise
                        if attempt == policy.attempts:
                            # Last attempt failed — emit a final marker and
                            # propagate the original exception.
                            _emit_exhausted(policy, attempt, cls)
                            raise

                        delay = compute_backoff(attempt=attempt + 1, policy=policy)
                        remaining = time_remaining()
                        if remaining is not None and remaining < delay:
                            # Not enough budget left; skip retry and re-raise so
                            # the gRPC mapper can surface DEADLINE_EXCEEDED via
                            # the standard path.
                            _emit_deadline_skipped(policy, attempt, cls, remaining)
                            raise

                        _emit_attempt(policy, attempt + 1, cls, delay)
                        if delay > 0:
                            await asyncio.sleep(delay)
                # Defensive: loop should always either return or raise.
                assert last_exc is not None  # pragma: no cover - unreachable
                raise last_exc  # pragma: no cover - unreachable
            finally:
                RETRY_ATTEMPTS_VAR.reset(attempts_token)

        return wrapper  # type: ignore[return-value]

    return decorator


def _emit_attempt(
    policy: RetryPolicy, attempt_number: int, cls, delay_s: float
) -> None:
    """Emit a ``retry.attempt`` span event for the upcoming retry."""

    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return
    span.add_event(
        "retry.attempt",
        attributes={
            "retry.attempt": attempt_number,
            "retry.policy": policy.name,
            "retry.error_class": cls.value,
            "retry.backoff_ms": int(delay_s * 1000),
        },
    )


def _emit_exhausted(policy: RetryPolicy, attempt_number: int, cls) -> None:
    """Emit a ``retry.exhausted`` event when the policy budget runs out."""

    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return
    span.add_event(
        "retry.exhausted",
        attributes={
            "retry.attempt": attempt_number,
            "retry.policy": policy.name,
            "retry.error_class": cls.value,
        },
    )


def _emit_deadline_skipped(
    policy: RetryPolicy, attempt_number: int, cls, remaining_s: float
) -> None:
    """Emit a ``retry.deadline_skipped`` event when budget can't fit a retry."""

    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return
    span.add_event(
        "retry.deadline_skipped",
        attributes={
            "retry.attempt": attempt_number,
            "retry.policy": policy.name,
            "retry.error_class": cls.value,
            "retry.remaining_ms": int(remaining_s * 1000),
        },
    )


__all__ = ["RETRY_ATTEMPTS_VAR", "with_retry"]
