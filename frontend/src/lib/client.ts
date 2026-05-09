import { createPromiseClient } from "@connectrpc/connect";
import { createGrpcWebTransport } from "@connectrpc/connect-web";
import { LibraryService } from "@/generated/library/v1/library_connect";

// Singleton transport + client. NEXT_PUBLIC_API_BASE_URL points to Envoy
// (the gRPC-Web bridge in front of the Python backend). When unset we fall
// back to the local docker-compose default.
const baseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080";

const transport = createGrpcWebTransport({
  baseUrl,
  // Connect-web requires the binary format for gRPC-Web; stick with defaults.
});

export const client = createPromiseClient(LibraryService, transport);
