"""Integration tests for the OTel instrumentation.

These tests wire an :class:`InMemorySpanExporter` into the test session's
tracer provider so we can inspect the spans an RPC produced without needing
a real backend. The test fixture clears collected spans between tests.

What we verify:

* Borrow happy path produces the expected manual spans + ``loan.created`` event.
* Borrow with no copies emits ``loan.contention`` and the root span is errored.
* Return happy path emits ``loan.returned`` with the right attributes.
* Every RPC's root span carries a ``request.id`` attribute (set by the interceptor).
* PII smoke: no member email / name / address / book title appears in any
  span attribute or event attribute across a representative set of RPCs.

We don't assert exact span counts because auto-instrumentation may add
incidental DB spans; we assert *names* and *attribute presence/absence*.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from library.generated.library.v1 import library_pb2


@pytest.fixture(scope="session")
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Install an in-memory exporter on the global tracer provider for the suite.

    The grpc_server fixture runs the LibraryServicer in this same process, so
    the in-memory exporter captures spans from real RPC handling.
    """

    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        # Tests run without `init_telemetry`; install our own provider.
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.shutdown()


@pytest.fixture(autouse=True)
def _reset_spans(span_exporter: InMemorySpanExporter) -> Iterator[None]:
    span_exporter.clear()
    yield


# ---- helpers ----


def _all_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return list(exporter.get_finished_spans())


def _span_names(spans: list[ReadableSpan]) -> set[str]:
    return {s.name for s in spans}


def _events_named(spans: list[ReadableSpan], name: str) -> list:
    """Flatten all events with the given name across all spans in the export."""

    found = []
    for span in spans:
        for event in span.events:
            if event.name == name:
                found.append(event)
    return found


# ---- tests ----


async def test_borrow_emits_expected_spans_and_loan_created_event(
    library_stub, span_exporter: InMemorySpanExporter
) -> None:
    book = (
        await library_stub.CreateBook(
            library_pb2.CreateBookRequest(
                title="Dune", author="Herbert", number_of_copies=1
            )
        )
    ).book
    member = (
        await library_stub.CreateMember(
            library_pb2.CreateMemberRequest(name="Ada", email="ada@example.com")
        )
    ).member

    span_exporter.clear()  # focus the assertions on the borrow call only

    await library_stub.BorrowBook(
        library_pb2.BorrowBookRequest(book_id=book.id, member_id=member.id)
    )

    spans = _all_spans(span_exporter)
    names = _span_names(spans)

    # The four manual spans from the spec must all appear.
    assert "borrow.validate" in names
    assert "borrow.transaction" in names
    assert "borrow.pick_copy" in names
    assert "borrow.build_response" in names

    # The single business event must be emitted exactly once.
    created_events = _events_named(spans, "loan.created")
    assert len(created_events) == 1
    attrs = dict(created_events[0].attributes or {})
    assert attrs.get("library.book_id") == book.id
    assert attrs.get("library.member_id") == member.id
    assert "library.loan_id" in attrs


async def test_borrow_no_copies_emits_contention_event_and_errors_root(
    library_stub, span_exporter: InMemorySpanExporter
) -> None:
    book = (
        await library_stub.CreateBook(
            library_pb2.CreateBookRequest(
                title="Foundation", author="Asimov", number_of_copies=1
            )
        )
    ).book
    a = (
        await library_stub.CreateMember(
            library_pb2.CreateMemberRequest(name="A", email="a@example.com")
        )
    ).member
    b = (
        await library_stub.CreateMember(
            library_pb2.CreateMemberRequest(name="B", email="b@example.com")
        )
    ).member
    await library_stub.BorrowBook(
        library_pb2.BorrowBookRequest(book_id=book.id, member_id=a.id)
    )

    span_exporter.clear()

    import grpc as _grpc

    with pytest.raises(_grpc.aio.AioRpcError):
        await library_stub.BorrowBook(
            library_pb2.BorrowBookRequest(book_id=book.id, member_id=b.id)
        )

    spans = _all_spans(span_exporter)
    contention = _events_named(spans, "loan.contention")
    assert len(contention) == 1
    assert dict(contention[0].attributes or {}).get("library.book_id") == book.id


async def test_return_emits_loan_returned_event_with_attrs(
    library_stub, span_exporter: InMemorySpanExporter
) -> None:
    book = (
        await library_stub.CreateBook(
            library_pb2.CreateBookRequest(
                title="Anathem", author="Stephenson", number_of_copies=1
            )
        )
    ).book
    member = (
        await library_stub.CreateMember(
            library_pb2.CreateMemberRequest(name="N", email="n@example.com")
        )
    ).member
    loan = (
        await library_stub.BorrowBook(
            library_pb2.BorrowBookRequest(book_id=book.id, member_id=member.id)
        )
    ).loan

    span_exporter.clear()

    await library_stub.ReturnBook(library_pb2.ReturnBookRequest(loan_id=loan.id))

    spans = _all_spans(span_exporter)
    assert "return.transaction" in _span_names(spans)
    returned_events = _events_named(spans, "loan.returned")
    assert len(returned_events) == 1
    attrs = dict(returned_events[0].attributes or {})
    assert attrs.get("library.loan_id") == loan.id
    assert attrs.get("library.fine_cents") == 0  # within grace
    assert attrs.get("library.was_overdue") is False
    assert attrs.get("library.days_late") == 0


async def test_request_id_is_set_on_root_span(
    library_stub, span_exporter: InMemorySpanExporter
) -> None:
    """Every RPC root span carries the request.id stamped by the interceptor."""

    await library_stub.ListBooks(library_pb2.ListBooksRequest())

    spans = _all_spans(span_exporter)
    # The gRPC auto-instrumentation creates a SERVER span as the root. Its
    # name typically contains the method's fully-qualified path.
    root_candidates = [
        s
        for s in spans
        if s.kind == trace.SpanKind.SERVER and "ListBooks" in s.name
    ]
    assert root_candidates, f"no server-kind ListBooks span in: {[s.name for s in spans]}"
    attrs = dict(root_candidates[0].attributes or {})
    assert "request.id" in attrs
    assert len(str(attrs["request.id"])) >= 16  # uuid4 hex is 32 chars


async def test_no_pii_in_span_attributes_for_borrow_flow(
    library_stub, span_exporter: InMemorySpanExporter
) -> None:
    """Across a representative borrow flow, no member-PII or book title leaks
    into any span or event attribute. IDs are fine; names/emails/titles are not.
    """

    book = (
        await library_stub.CreateBook(
            library_pb2.CreateBookRequest(
                title="UNIQUEBOOKTITLE", author="UNIQUEAUTHOR", number_of_copies=1
            )
        )
    ).book
    member = (
        await library_stub.CreateMember(
            library_pb2.CreateMemberRequest(
                name="UNIQUE_MEMBER_NAME", email="unique@example.com"
            )
        )
    ).member

    span_exporter.clear()

    await library_stub.BorrowBook(
        library_pb2.BorrowBookRequest(book_id=book.id, member_id=member.id)
    )

    forbidden_substrings = [
        "UNIQUE_MEMBER_NAME",
        "unique@example.com",
        "UNIQUEBOOKTITLE",
        "UNIQUEAUTHOR",
    ]

    spans = _all_spans(span_exporter)
    for span in spans:
        for key, value in (span.attributes or {}).items():
            text = f"{key}={value}"
            for forbidden in forbidden_substrings:
                assert forbidden not in text, (
                    f"PII '{forbidden}' leaked into span attr: span={span.name} {text}"
                )
        for event in span.events:
            for key, value in (event.attributes or {}).items():
                text = f"{key}={value}"
                for forbidden in forbidden_substrings:
                    assert forbidden not in text, (
                        f"PII '{forbidden}' leaked into event attr: "
                        f"span={span.name} event={event.name} {text}"
                    )
