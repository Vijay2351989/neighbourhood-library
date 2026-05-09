// Friendly error rendering. The backend returns standard gRPC status codes;
// we map those to short human-readable strings and try to extract the field
// name from `INVALID_ARGUMENT` messages so the form can highlight it.

import { Code, ConnectError } from "@connectrpc/connect";

export type FriendlyError = {
  code: Code | "unknown";
  message: string;
  /** Best-effort field name parsed out of the server error. */
  field?: string;
};

/**
 * Heuristic extraction of a field name from server messages that look like
 * "title: must not be empty" or "email: invalid format". The Python service
 * emits messages in this shape (see backend/library/api/validation.py).
 */
function parseField(msg: string): string | undefined {
  const m = msg.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\s*[:.-]/);
  if (!m) return undefined;
  // camelCase the snake_case name to match form field names.
  return m[1].replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

export function toFriendlyError(err: unknown): FriendlyError {
  if (err instanceof ConnectError) {
    const raw = err.rawMessage ?? err.message;
    const code = err.code;
    const field = code === Code.InvalidArgument ? parseField(raw) : undefined;
    return { code, message: raw || friendlyDefault(code), field };
  }
  if (err instanceof Error) {
    return { code: "unknown", message: err.message };
  }
  return { code: "unknown", message: "Something went wrong." };
}

function friendlyDefault(code: Code): string {
  switch (code) {
    case Code.InvalidArgument:
      return "Some fields are invalid.";
    case Code.NotFound:
      return "Not found.";
    case Code.AlreadyExists:
      return "That already exists.";
    case Code.FailedPrecondition:
      return "That isn't allowed right now.";
    case Code.Unavailable:
      return "Cannot reach the library service. Try again.";
    case Code.Internal:
      return "Server error. Try again.";
    default:
      return "Something went wrong.";
  }
}

/** Format an error for a top-bar toast. */
export function toastMessage(err: unknown): string {
  const f = toFriendlyError(err);
  if (f.code === Code.Unavailable) {
    return "Cannot reach the library service. Try again.";
  }
  return `This operation failed: ${f.message}`;
}
