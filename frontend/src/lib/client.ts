import { createPromiseClient } from "@connectrpc/connect";
import { createGrpcWebTransport } from "@connectrpc/connect-web";
import { BookService } from "@/generated/library/v1/book_connect";
import { MemberService } from "@/generated/library/v1/member_connect";
import { LoanService } from "@/generated/library/v1/loan_connect";

// One transport, three clients. NEXT_PUBLIC_API_BASE_URL points to Envoy
// (the gRPC-Web bridge in front of the Python backend); when unset we fall
// back to the local docker-compose default. gRPC multiplexes services over
// a single HTTP/2 connection so all three clients share the same transport
// — opening one transport per service would just waste sockets.
const baseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080";

const transport = createGrpcWebTransport({
  baseUrl,
  // Connect-web requires the binary format for gRPC-Web; stick with defaults.
});

export const bookClient = createPromiseClient(BookService, transport);
export const memberClient = createPromiseClient(MemberService, transport);
export const loanClient = createPromiseClient(LoanService, transport);
