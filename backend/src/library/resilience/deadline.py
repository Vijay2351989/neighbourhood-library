"""Per-RPC deadline tracked via a contextvar.

The :class:`RequestContextInterceptor` reads the gRPC client's
``grpc-timeout`` header (via ``context.time_remaining()``) at the start of a
call and stamps :data:`DEADLINE_VAR` with an absolute monotonic deadline.
The retry decorator reads it before each retry sleep so retries don't
outlive the client.

If a caller didn't set a deadline (e.g. an internal sample-client call), the
contextvar is ``None`` and :func:`time_remaining` returns ``None`` —
signalling "no budget cap; respect only the policy's max attempts".
"""

from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Deadline:
    """Absolute monotonic deadline for the current RPC."""

    end_monotonic_s: float

    def time_remaining_s(self) -> float:
        return max(0.0, self.end_monotonic_s - time.monotonic())


DEADLINE_VAR: Final[contextvars.ContextVar[Deadline | None]] = contextvars.ContextVar(
    "library.resilience.deadline", default=None
)


def set_deadline_from_grpc_context(grpc_context) -> contextvars.Token | None:  # type: ignore[no-untyped-def]
    """Read ``time_remaining()`` from a ``grpc.aio.ServicerContext`` and stamp.

    Returns the contextvar token so the caller can ``DEADLINE_VAR.reset(token)``
    in a ``finally`` block. If the client didn't supply a deadline,
    ``time_remaining()`` returns ``None`` and we leave the contextvar unset
    (returning ``None``) so the decorator falls back to "policy attempts cap".
    """

    try:
        remaining = grpc_context.time_remaining()
    except Exception:  # pragma: no cover - context surface varies in tests
        return None
    if remaining is None:
        return None
    deadline = Deadline(end_monotonic_s=time.monotonic() + remaining)
    return DEADLINE_VAR.set(deadline)


def time_remaining() -> float | None:
    """Seconds left in the active RPC's deadline, or ``None`` if unset."""

    deadline = DEADLINE_VAR.get()
    if deadline is None:
        return None
    return deadline.time_remaining_s()


__all__ = [
    "DEADLINE_VAR",
    "Deadline",
    "set_deadline_from_grpc_context",
    "time_remaining",
]
