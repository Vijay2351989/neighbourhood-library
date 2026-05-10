"""Sample gRPC client demonstrating the Neighborhood Library API.

This script is a teaching artefact. Read it top-to-bottom to learn what
the three-service surface (BookService / MemberService / LoanService)
looks like end-to-end:

  1. Create a member.            (MemberService)
  2. Create a book (one copy).   (BookService)
  3. Borrow the book.            (LoanService)
  4. List active loans — borrow shows up.   (LoanService)
  5. Return the book.            (LoanService)
  6. List active loans — borrow gone.       (LoanService)

The script talks to the api service over **native gRPC** on
``localhost:50051``. Browsers can't speak native gRPC's HTTP/2 framing,
which is why the React app at ``http://localhost:3000`` uses gRPC-Web
through Envoy at ``:8080`` instead. From a regular Python process,
native gRPC is the simplest path; this file demonstrates that path.

Notice that all three stubs share a single ``grpc.insecure_channel`` —
gRPC multiplexes services over one HTTP/2 connection so opening three
channels would just waste sockets.

Run it after the stack is up:

    # From the host (requires `uv` and a synced backend venv):
    uv run python backend/scripts/sample_client.py

    # Or, from inside the api container:
    docker compose exec api python /app/scripts/sample_client.py

The script is idempotent in spirit but not in fact — every run creates
a fresh book and member with email and ISBN values keyed on the current
timestamp, so successive runs accumulate rows. Run
``DEMO_MODE=true docker compose restart api`` to reset to the seeded
demo state.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import grpc
from google.protobuf import wrappers_pb2

# Generated stubs. The codegen tree lives under ``library.generated.*``
# (see ``backend/scripts/gen_proto.sh`` for why one level deeper than
# protoc's default emit path). One pb2/pb2_grpc pair per service after
# the proto split.
from library.generated.library.v1 import (
    book_pb2,
    book_pb2_grpc,
    loan_pb2,
    loan_pb2_grpc,
    member_pb2,
    member_pb2_grpc,
)


# Address resolves to the api container's exposed port; if you've changed
# the GRPC_PORT env var or remapped the port in docker-compose.yml,
# update this constant accordingly.
_DEFAULT_TARGET = "localhost:50051"

# RPC deadline. 5 seconds is generous for a single in-process call;
# anything over this is almost certainly an infrastructure problem worth
# surfacing to the operator rather than retrying silently.
_RPC_TIMEOUT_S = 5.0


def _banner(title: str) -> None:
    """Print a section header so the run output reads like a story."""
    print()
    print(f"=== {title} ===")


def _print_loan(loan: loan_pb2.Loan) -> None:
    """Pretty-print a Loan message; omits the noise the demo doesn't need."""

    returned = (
        loan.returned_at.ToDatetime().isoformat()
        if loan.HasField("returned_at")
        else "—"
    )
    print(
        f"  loan id={loan.id} "
        f"member={loan.member_name!r} "
        f"book={loan.book_title!r} "
        f"borrowed_at={loan.borrowed_at.ToDatetime().isoformat()} "
        f"due_at={loan.due_at.ToDatetime().isoformat()} "
        f"returned_at={returned} "
        f"overdue={loan.overdue} fine_cents={loan.fine_cents}"
    )


def _run(target: str) -> int:
    """Execute the demo against ``target``; return a process exit code."""

    # ``insecure_channel`` is correct for local dev; production would
    # use TLS via ``grpc.secure_channel``. The channel is a long-lived
    # multiplexed HTTP/2 connection — we keep one open for all six RPCs.
    print(f"connecting to gRPC server at {target} ...")
    with grpc.insecure_channel(target) as channel:
        # Block until the channel is connected so we can give a clean
        # error message if the server isn't up yet, instead of letting
        # the first RPC fail with the generic UNAVAILABLE.
        try:
            grpc.channel_ready_future(channel).result(timeout=_RPC_TIMEOUT_S)
        except grpc.FutureTimeoutError:
            print(
                f"error: could not connect to {target} within {_RPC_TIMEOUT_S}s.\n"
                f"is the api service running? try: docker compose ps",
                file=sys.stderr,
            )
            return 1

        # One stub per service — they share the channel above.
        book_stub = book_pb2_grpc.BookServiceStub(channel)
        member_stub = member_pb2_grpc.MemberServiceStub(channel)
        loan_stub = loan_pb2_grpc.LoanServiceStub(channel)
        unique = int(time.time())  # disambiguates rows across runs

        # ---- 1. Create a member -------------------------------------
        _banner("1. CreateMember (MemberService)")
        create_member_resp = member_stub.CreateMember(
            member_pb2.CreateMemberRequest(
                name="Sample Reader",
                email=f"sample-{unique}@example.com",
                # phone and address are wrapped (StringValue) for nullability;
                # leaving them unset means the field is null in the DB.
            ),
            timeout=_RPC_TIMEOUT_S,
        )
        member = create_member_resp.member
        print(f"  created member id={member.id} name={member.name!r} email={member.email!r}")

        # ---- 2. Create a book with one copy --------------------------
        _banner("2. CreateBook (BookService)")
        create_book_resp = book_stub.CreateBook(
            book_pb2.CreateBookRequest(
                title="The Sample Manuscript",
                author="Alex Demo",
                # ISBN is wrapped (StringValue); supplying it exercises the
                # nullable-field code path on both sides of the wire.
                isbn=wrappers_pb2.StringValue(value=f"DEMO-{unique}"),
                published_year=wrappers_pb2.Int32Value(value=2026),
                number_of_copies=1,
            ),
            timeout=_RPC_TIMEOUT_S,
        )
        book = create_book_resp.book
        print(
            f"  created book id={book.id} title={book.title!r} "
            f"copies={book.total_copies} available={book.available_copies}"
        )

        # ---- 3. Borrow the book --------------------------------------
        _banner("3. BorrowBook (LoanService)")
        # ``due_at`` is optional; the server defaults to now + 14 days
        # (DEFAULT_LOAN_DAYS). Omitting it here demonstrates that path.
        borrow_resp = loan_stub.BorrowBook(
            loan_pb2.BorrowBookRequest(
                book_id=book.id,
                member_id=member.id,
            ),
            timeout=_RPC_TIMEOUT_S,
        )
        loan = borrow_resp.loan
        _print_loan(loan)

        # ---- 4. List active loans ------------------------------------
        _banner("4. ListLoans (filter=ACTIVE) — should include the new loan")
        active_before = loan_stub.ListLoans(
            loan_pb2.ListLoansRequest(
                filter=loan_pb2.LOAN_FILTER_ACTIVE,
                page_size=50,
            ),
            timeout=_RPC_TIMEOUT_S,
        )
        print(f"  total_count={active_before.total_count}")
        for active_loan in active_before.loans:
            marker = "  ▶" if active_loan.id == loan.id else "   "
            print(marker, end="")
            _print_loan(active_loan)

        # ---- 5. Return the book --------------------------------------
        _banner("5. ReturnBook (LoanService)")
        return_resp = loan_stub.ReturnBook(
            loan_pb2.ReturnBookRequest(loan_id=loan.id),
            timeout=_RPC_TIMEOUT_S,
        )
        _print_loan(return_resp.loan)

        # ---- 6. List active loans again ------------------------------
        _banner("6. ListLoans (filter=ACTIVE) — should NOT include the loan")
        active_after = loan_stub.ListLoans(
            loan_pb2.ListLoansRequest(
                filter=loan_pb2.LOAN_FILTER_ACTIVE,
                page_size=50,
            ),
            timeout=_RPC_TIMEOUT_S,
        )
        print(f"  total_count={active_after.total_count}")
        still_present = [loan_row for loan_row in active_after.loans if loan_row.id == loan.id]
        if still_present:
            print(
                f"  WARNING: loan {loan.id} is still in the active list after return!",
                file=sys.stderr,
            )
            return 2

        # ---- Summary -------------------------------------------------
        _banner("Summary")
        print(f"  active loans before borrow+return cycle: {active_before.total_count - 1}")
        print(f"  active loans after  borrow+return cycle: {active_after.total_count}")
        print(f"  member {member.id} ({member.name!r}) borrowed and returned book {book.id}")
        print(f"  loan {loan.id} now has returned_at = "
              f"{return_resp.loan.returned_at.ToDatetime(tzinfo=timezone.utc).isoformat()}")
        print()
        print("OK — all six steps completed successfully.")
        return 0


def main() -> None:
    """Allow optional ``host:port`` override on argv for non-local targets."""

    target = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_TARGET
    sys.exit(_run(target))


if __name__ == "__main__":
    main()
