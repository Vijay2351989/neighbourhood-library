import { Timestamp } from "@bufbuild/protobuf";

const currencyFmt = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
});

const dateFmt = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
});

const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/**
 * Format a *_cents int (bigint or number) as USD. Backend uses bigint for
 * int64 fields, but we accept number for convenience. Currency is hard-coded
 * USD per design/04-frontend.md §6.
 */
export function formatCents(cents: bigint | number | undefined | null): string {
  if (cents == null) return currencyFmt.format(0);
  const asNumber = typeof cents === "bigint" ? Number(cents) : cents;
  return currencyFmt.format(asNumber / 100);
}

function timestampToDate(ts?: Timestamp): Date | null {
  if (!ts) return null;
  // Timestamp seconds is bigint; combine with nanos for Date.
  const seconds =
    typeof ts.seconds === "bigint" ? Number(ts.seconds) : (ts.seconds as number);
  return new Date(seconds * 1000 + Math.floor(ts.nanos / 1_000_000));
}

export function formatDate(ts?: Timestamp): string {
  const d = timestampToDate(ts);
  return d ? dateFmt.format(d) : "—";
}

export function formatDateTime(ts?: Timestamp): string {
  const d = timestampToDate(ts);
  return d ? dateTimeFmt.format(d) : "—";
}

/** Convert a YYYY-MM-DD string from <input type=date> to a Timestamp. */
export function dateInputToTimestamp(value: string): Timestamp | undefined {
  if (!value) return undefined;
  const d = new Date(value + "T23:59:59"); // end-of-day, local
  if (Number.isNaN(d.getTime())) return undefined;
  return new Timestamp({
    seconds: BigInt(Math.floor(d.getTime() / 1000)),
    nanos: 0,
  });
}
