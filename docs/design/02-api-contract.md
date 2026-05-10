# API Contract — Protobuf Service Definition

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)
**Implemented in:** [Phase 3](../phases/phase-3-proto-codegen.md)
**Used by:** [Phase 4](../phases/phase-4-backend-crud.md), [Phase 5](../phases/phase-5-borrow-return-fines.md), [Phase 6](../phases/phase-6-frontend-mvp.md)

The wire contract between the browser and the backend. Lives in three files under `proto/library/v1/` (repo root) — a single source of truth for both backend and frontend codegen. We version with `v1` in the package path so future breaking changes are clearly delineated.

The 12 RPCs are split across three services (one per subdomain):

| Service | File | RPCs |
|---|---|---|
| `library.v1.BookService`   | `book.proto`   | `CreateBook`, `UpdateBook`, `GetBook`, `ListBooks` |
| `library.v1.MemberService` | `member.proto` | `CreateMember`, `UpdateMember`, `GetMember`, `ListMembers` |
| `library.v1.LoanService`   | `loan.proto`   | `BorrowBook`, `ReturnBook`, `ListLoans`, `GetMemberLoans` |

All three live under the same `package library.v1`. There is no proto-level dependency between the files — `Loan` references books and members by `int64 id` only, with denormalized `book_title` / `book_author` / `member_name` strings for UI rendering — so each `.proto` compiles independently.

---

## 1. Full `.proto` files

Each file below shows its full contents. The Resource message and the RPC messages it serves are co-located with the matching `service` block.

### 1.1 `proto/library/v1/book.proto`

```protobuf
syntax = "proto3";

package library.v1;

import "google/protobuf/timestamp.proto";
import "google/protobuf/wrappers.proto";

message Book {
  int64 id = 1;
  string title = 2;
  string author = 3;
  google.protobuf.StringValue isbn = 4;            // nullable
  google.protobuf.Int32Value published_year = 5;   // nullable
  int32 total_copies = 6;                           // computed
  int32 available_copies = 7;                       // computed
  google.protobuf.Timestamp created_at = 8;
  google.protobuf.Timestamp updated_at = 9;
}

message CreateBookRequest {
  string title = 1;
  string author = 2;
  google.protobuf.StringValue isbn = 3;
  google.protobuf.Int32Value published_year = 4;
  int32 number_of_copies = 5;   // must be >= 1
}
message CreateBookResponse { Book book = 1; }

message UpdateBookRequest {
  int64 id = 1;
  string title = 2;
  string author = 3;
  google.protobuf.StringValue isbn = 4;
  google.protobuf.Int32Value published_year = 5;
  google.protobuf.Int32Value number_of_copies = 6;  // optional; if set, server reconciles copy rows
}
message UpdateBookResponse { Book book = 1; }

message GetBookRequest  { int64 id = 1; }
message GetBookResponse { Book book = 1; }

message ListBooksRequest {
  google.protobuf.StringValue search = 1;   // matches title or author, case-insensitive
  int32 page_size = 2;                       // default 25, max 100
  int32 offset = 3;
}
message ListBooksResponse {
  repeated Book books = 1;
  int32 total_count = 2;
}

service BookService {
  rpc CreateBook (CreateBookRequest) returns (CreateBookResponse);
  rpc UpdateBook (UpdateBookRequest) returns (UpdateBookResponse);
  rpc GetBook    (GetBookRequest)    returns (GetBookResponse);
  rpc ListBooks  (ListBooksRequest)  returns (ListBooksResponse);
}
```

### 1.2 `proto/library/v1/member.proto`

```protobuf
syntax = "proto3";

package library.v1;

import "google/protobuf/timestamp.proto";
import "google/protobuf/wrappers.proto";

message Member {
  int64 id = 1;
  string name = 2;
  string email = 3;
  google.protobuf.StringValue phone = 4;
  google.protobuf.StringValue address = 5;
  google.protobuf.Timestamp created_at = 6;
  google.protobuf.Timestamp updated_at = 7;
  int64 outstanding_fines_cents = 8;            // computed: sum of compute_fine_cents over all member's loans (see design/01-database.md §5)
}

message CreateMemberRequest {
  string name = 1;
  string email = 2;
  google.protobuf.StringValue phone = 3;
  google.protobuf.StringValue address = 4;
}
message CreateMemberResponse { Member member = 1; }

message UpdateMemberRequest {
  int64 id = 1;
  string name = 2;
  string email = 3;
  google.protobuf.StringValue phone = 4;
  google.protobuf.StringValue address = 5;
}
message UpdateMemberResponse { Member member = 1; }

message GetMemberRequest  { int64 id = 1; }
message GetMemberResponse { Member member = 1; }

message ListMembersRequest {
  google.protobuf.StringValue search = 1;
  int32 page_size = 2;
  int32 offset = 3;
}
message ListMembersResponse {
  repeated Member members = 1;
  int32 total_count = 2;
}

service MemberService {
  rpc CreateMember (CreateMemberRequest) returns (CreateMemberResponse);
  rpc UpdateMember (UpdateMemberRequest) returns (UpdateMemberResponse);
  rpc GetMember    (GetMemberRequest)    returns (GetMemberResponse);
  rpc ListMembers  (ListMembersRequest)  returns (ListMembersResponse);
}
```

### 1.3 `proto/library/v1/loan.proto`

```protobuf
syntax = "proto3";

package library.v1;

import "google/protobuf/timestamp.proto";
import "google/protobuf/wrappers.proto";

message Loan {
  int64 id = 1;
  int64 member_id = 2;
  int64 book_id = 3;
  int64 copy_id = 4;
  string book_title = 5;       // denormalized into responses for UI convenience
  string book_author = 6;
  string member_name = 7;
  google.protobuf.Timestamp borrowed_at = 8;
  google.protobuf.Timestamp due_at = 9;
  google.protobuf.Timestamp returned_at = 10;  // unset = active
  bool overdue = 11;                            // computed: returned_at unset AND due_at < now
  int64 fine_cents = 12;                        // computed per design/01-database.md §5; 0 when within grace or never overdue
}

enum LoanFilter {
  LOAN_FILTER_UNSPECIFIED = 0;  // both active and returned
  LOAN_FILTER_ACTIVE = 1;       // returned_at IS NULL
  LOAN_FILTER_RETURNED = 2;
  LOAN_FILTER_OVERDUE = 3;      // active AND due_at < now
  LOAN_FILTER_HAS_FINE = 4;     // fine_cents > 0 (active accruing, or returned-late snapshot)
}

message BorrowBookRequest {
  int64 book_id = 1;
  int64 member_id = 2;
  google.protobuf.Timestamp due_at = 3;   // optional; server defaults to now+14d
}
message BorrowBookResponse { Loan loan = 1; }

message ReturnBookRequest  { int64 loan_id = 1; }
message ReturnBookResponse { Loan loan = 1; }

message ListLoansRequest {
  google.protobuf.Int64Value member_id = 1;   // optional filter
  google.protobuf.Int64Value book_id = 2;     // optional filter
  LoanFilter filter = 3;
  int32 page_size = 4;
  int32 offset = 5;
}
message ListLoansResponse {
  repeated Loan loans = 1;
  int32 total_count = 2;
}

message GetMemberLoansRequest {
  int64 member_id = 1;
  LoanFilter filter = 2;
}
message GetMemberLoansResponse {
  repeated Loan loans = 1;
}

service LoanService {
  rpc BorrowBook     (BorrowBookRequest)     returns (BorrowBookResponse);
  rpc ReturnBook     (ReturnBookRequest)     returns (ReturnBookResponse);
  rpc ListLoans      (ListLoansRequest)      returns (ListLoansResponse);
  rpc GetMemberLoans (GetMemberLoansRequest) returns (GetMemberLoansResponse);
}
```

---

## 2. Error semantics

| Failure | gRPC status | Example |
|---|---|---|
| Required field missing or invalid | `INVALID_ARGUMENT` | empty title, negative `number_of_copies`, `page_size > 100` |
| Resource not found | `NOT_FOUND` | `GetBook(id=999)` when no such book |
| Borrow attempted but no AVAILABLE copy | `FAILED_PRECONDITION` | every copy of the book is `BORROWED` |
| Return attempted on already-returned loan | `FAILED_PRECONDITION` | `returned_at IS NOT NULL` |
| Duplicate email on member create | `ALREADY_EXISTS` | enforced via `members_email_unique_idx` |
| Reducing `number_of_copies` below currently-borrowed | `FAILED_PRECONDITION` | with a clear message |
| Catastrophic / unexpected | `INTERNAL` | logged with stack trace |

The Python server uses `grpc.aio.AioRpcError`-compatible exceptions raised from the service layer; the outer servicer wrapper translates any uncaught exception into `INTERNAL` and logs it.

---

## 3. Conventions

- **Method names:** verb-noun, mirroring resource lifecycle (`Create*`, `Update*`, `Get*`, `List*`, plus the verbs `BorrowBook` and `ReturnBook` for the lending operations).
- **Distinct request/response messages per RPC** — never share. This makes future field additions safe.
- **Nullable scalars** use `google.protobuf.StringValue` / `Int32Value` / `Int64Value` wrappers; required scalars use the bare type.
- **Timestamps** use `google.protobuf.Timestamp`. Server stores UTC; client renders local.
- **Pagination** is offset-based (`page_size`, `offset`). See [reference/decisions.md](../reference/decisions.md) for why we didn't pick cursor pagination.
- **Computed fields** (`overdue`, `fine_cents`, `total_copies`, `available_copies`, `outstanding_fines_cents`) are populated by the server on each response — never written by clients.

---

## Cross-references

- Schema that backs these messages: [design/01-database.md](01-database.md)
- Backend code that implements the service: [design/03-backend.md](03-backend.md)
- Frontend client that consumes the service: [design/04-frontend.md](04-frontend.md)
- Codegen wiring: [phases/phase-3-proto-codegen.md](../phases/phase-3-proto-codegen.md)
