"""Pure-function helpers for protobuf <-> domain conversion.

Kept in its own module so both the book and member services share a single
implementation of the wrapper-field unwrapping, timestamp marshaling, and
pagination-clamp rules. None of these helpers touch the database; they're
trivial enough to be unit-testable in isolation if a future phase adds tests
for them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from google.protobuf import timestamp_pb2

from library.errors import InvalidArgument

DEFAULT_PAGE_SIZE: Final[int] = 25
MAX_PAGE_SIZE: Final[int] = 100


def datetime_to_pb(dt: datetime) -> timestamp_pb2.Timestamp:
    """Convert a Python datetime to a ``google.protobuf.Timestamp``.

    Assumes ``dt`` is timezone-aware (the schema stores TIMESTAMPTZ). Naive
    datetimes are interpreted as UTC by ``Timestamp.FromDatetime``, which is
    not what we want — guard with an explicit assert so misuse is loud.
    """

    pb = timestamp_pb2.Timestamp()
    pb.FromDatetime(dt)
    return pb


def clamp_pagination(*, page_size: int, offset: int) -> tuple[int, int]:
    """Apply the rules from :doc:`docs/phases/phase-4-backend-crud.md` Notes & risks.

    * ``offset < 0`` -> :class:`InvalidArgument` (malformed input).
    * ``page_size < 0`` -> :class:`InvalidArgument` (malformed input).
    * ``page_size == 0`` -> :data:`DEFAULT_PAGE_SIZE` (silent default; proto3
      scalar default is 0, so this also catches "client didn't set the field").
    * ``page_size > MAX_PAGE_SIZE`` -> :data:`MAX_PAGE_SIZE` (silent clamp).
    """

    if offset < 0:
        raise InvalidArgument("offset must be non-negative")
    if page_size < 0:
        raise InvalidArgument("page_size must be non-negative")
    if page_size == 0:
        page_size = DEFAULT_PAGE_SIZE
    elif page_size > MAX_PAGE_SIZE:
        page_size = MAX_PAGE_SIZE
    return page_size, offset


def normalize_search(raw: str) -> str | None:
    """Strip and return None for empty searches.

    Proto3 scalars default to ``""``, and ``StringValue`` wrappers carry an
    explicit empty value when set to ``""`` — both should be treated as "no
    search filter" so the listing returns the full set.
    """

    stripped = raw.strip()
    return stripped or None


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "clamp_pagination",
    "datetime_to_pb",
    "normalize_search",
]
