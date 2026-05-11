"""Shared fixtures for the service-layer unit tests.

The integration suite's session-scoped fixtures (Postgres container, gRPC
server) still fire here because they're declared autouse in
``tests/conftest.py`` — that's pre-existing behavior. These helpers add a
*minimal* fake session factory so service classes can be constructed and
exercised without touching either of those.

``FakeSessionFactory`` mirrors the two call shapes the services use:

* ``async with self._session_factory.begin() as session:`` (write paths)
* ``async with self._session_factory() as session:`` (read paths)

The yielded ``session`` is an opaque sentinel — repository functions are
monkeypatched in the tests, so the session itself is never queried.
"""

from __future__ import annotations

import pytest


class _AsyncCM:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSessionFactory:
    def __init__(self) -> None:
        self.session: object = object()

    def __call__(self) -> _AsyncCM:
        return _AsyncCM(self.session)

    def begin(self) -> _AsyncCM:
        return _AsyncCM(self.session)


@pytest.fixture
def fake_session_factory() -> FakeSessionFactory:
    return FakeSessionFactory()
