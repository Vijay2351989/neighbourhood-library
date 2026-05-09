"""Unit tests for ``library.resilience.decorator.with_retry``.

The decorator is async, so all tests use ``pytest_asyncio``. We exercise:

* retryable failures lead to retry-then-success
* non-retryable failures raise on attempt 1 (no sleep)
* attempts cap is honored; final exception is the *original* type
* deadline-aware skip when remaining < computed backoff
* RETRY_ATTEMPTS_VAR reflects the attempt count
* span ``retry.attempt`` events are emitted with expected attributes
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from asyncpg import exceptions as apg_exc
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.exc import OperationalError

from library.errors import NotFound
from library.resilience.classify import ErrorClass
from library.resilience.decorator import RETRY_ATTEMPTS_VAR, with_retry
from library.resilience.deadline import DEADLINE_VAR, Deadline
from library.resilience.policies import (
    RETRY_NEVER,
    RETRY_READ,
    RETRY_WRITE_TX,
)


@pytest_asyncio.fixture
async def in_memory_spans():
    """Attach an in-memory exporter to the global tracer provider.

    OTel's :func:`set_tracer_provider` is one-shot — once the conftest's
    ``init_telemetry`` has installed a real provider, we can't override it.
    Instead, get the existing provider and bolt on a SimpleSpanProcessor
    pointing at our exporter; remove it on teardown.
    """

    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        # In rare cases (e.g. running this file in isolation without the
        # session conftest) the provider is the no-op default; install one.
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        # SimpleSpanProcessor.shutdown() flushes; the processor is left
        # attached but the exporter is shut down — subsequent tests get a
        # fresh exporter via the next fixture activation.
        processor.shutdown()


def _wrap_deadlock() -> OperationalError:
    return OperationalError("borrow", {}, apg_exc.DeadlockDetectedError("dl"))


@pytest.mark.asyncio
async def test_returns_immediately_on_success() -> None:
    @with_retry(RETRY_READ)
    async def ok() -> int:
        return 42

    assert await ok() == 42
    # No retry happened, so attempts contextvar should be reset to default.
    assert RETRY_ATTEMPTS_VAR.get() == 1


@pytest.mark.asyncio
async def test_retries_then_succeeds_for_retryable_class() -> None:
    calls = {"n": 0}

    @with_retry(RETRY_READ)
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _wrap_deadlock()
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_non_retryable_raises_on_first_attempt() -> None:
    calls = {"n": 0}

    @with_retry(RETRY_READ)
    async def domain_failure() -> None:
        calls["n"] += 1
        raise NotFound("missing")

    with pytest.raises(NotFound):
        await domain_failure()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_never_runs_once_even_on_retryable_class() -> None:
    calls = {"n": 0}

    @with_retry(RETRY_NEVER)
    async def explicit_no_retry() -> None:
        calls["n"] += 1
        raise _wrap_deadlock()

    with pytest.raises(OperationalError):
        await explicit_no_retry()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_attempts_cap_is_honored_and_original_exception_is_raised() -> None:
    calls = {"n": 0}

    @with_retry(RETRY_WRITE_TX)  # attempts=2
    async def always_fails() -> None:
        calls["n"] += 1
        raise _wrap_deadlock()

    with pytest.raises(OperationalError) as exc_info:
        await always_fails()
    # Importantly: the raised exception is the ORIGINAL type (OperationalError),
    # not a custom RetryExhausted wrapper. This is the contract.
    assert isinstance(exc_info.value, OperationalError)
    # WRITE_TX has attempts=2.
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_deadline_aware_skip() -> None:
    """When the active deadline can't accommodate the next backoff, retry
    is skipped and the original exception re-raised immediately."""

    calls = {"n": 0}

    @with_retry(RETRY_READ)
    async def flaky() -> None:
        calls["n"] += 1
        raise _wrap_deadlock()

    # Set an extremely tight deadline (1ms) so any computed backoff > remaining.
    token = DEADLINE_VAR.set(Deadline(end_monotonic_s=time.monotonic() + 0.001))
    try:
        with pytest.raises(OperationalError):
            await flaky()
    finally:
        DEADLINE_VAR.reset(token)
    # First attempt fired; we tried to compute backoff for retry #2 but
    # deadline was too tight, so we re-raised. Total attempts == 1.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_attempts_var_increments_per_attempt() -> None:
    """The contextvar should reflect the current attempt while the function
    runs; outside the decorator it resets to default."""

    seen: list[int] = []

    @with_retry(RETRY_READ)
    async def observe() -> str:
        seen.append(RETRY_ATTEMPTS_VAR.get())
        if len(seen) < 3:
            raise _wrap_deadlock()
        return "done"

    assert await observe() == "done"
    assert seen == [1, 2, 3]
    # Decorator should reset on exit; outside we see the default again.
    assert RETRY_ATTEMPTS_VAR.get() == 1


@pytest.mark.asyncio
async def test_emits_retry_attempt_span_event(in_memory_spans) -> None:
    """Each retry past the first should emit a ``retry.attempt`` event with
    policy/error_class/backoff_ms attributes on the active span."""

    tracer = trace.get_tracer("test")

    @with_retry(RETRY_READ)
    async def flaky() -> str:
        if not getattr(flaky, "_done", False):
            flaky._done = True  # type: ignore[attr-defined]
            raise _wrap_deadlock()
        return "ok"

    with tracer.start_as_current_span("root"):
        await flaky()

    finished = in_memory_spans.get_finished_spans()
    # Find the root span; it should carry our retry.attempt event.
    root = next(s for s in finished if s.name == "root")
    event_names = [e.name for e in root.events]
    assert "retry.attempt" in event_names
    attempt_event = next(e for e in root.events if e.name == "retry.attempt")
    assert attempt_event.attributes["retry.policy"] == "RETRY_READ"
    assert attempt_event.attributes["retry.error_class"] == ErrorClass.DEADLOCK.value
    assert attempt_event.attributes["retry.attempt"] == 2
    assert attempt_event.attributes["retry.backoff_ms"] >= 0


@pytest.mark.asyncio
async def test_emits_retry_exhausted_span_event(in_memory_spans) -> None:
    """When all attempts fail the decorator emits a ``retry.exhausted`` event
    before re-raising."""

    tracer = trace.get_tracer("test")

    @with_retry(RETRY_WRITE_TX)
    async def always_fails() -> None:
        raise _wrap_deadlock()

    with tracer.start_as_current_span("root"):
        with pytest.raises(OperationalError):
            await always_fails()

    finished = in_memory_spans.get_finished_spans()
    root = next(s for s in finished if s.name == "root")
    event_names = [e.name for e in root.events]
    assert "retry.exhausted" in event_names


@pytest.mark.asyncio
async def test_cancelled_propagates_without_retry() -> None:
    """asyncio.CancelledError must propagate immediately even if it would
    otherwise be classified — the decorator must never swallow cancellation."""

    calls = {"n": 0}

    @with_retry(RETRY_READ)
    async def cancellable() -> None:
        calls["n"] += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cancellable()
    assert calls["n"] == 1
