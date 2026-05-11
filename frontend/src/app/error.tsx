"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/EmptyState";

// App-level error boundary. Catches any uncaught render error from a route
// segment that doesn't have its own error.tsx. The lower-level segment
// boundaries (e.g. members/[id]/error.tsx) handle errors specific to that
// page; anything that escapes those bubbles up here.
//
// The Next.js 16 file convention provides `unstable_retry` (renamed from
// the previous `reset`) — calling it re-renders the failed segment.

export default function AppError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    // Surface the error in browser devtools and any future log aggregator
    // attached to console.error. `digest` correlates with server logs when
    // the error originated server-side.
    console.error("AppError boundary caught:", error, {
      digest: error.digest,
    });
  }, [error]);

  return (
    <EmptyState
      title="Something went wrong"
      description="An unexpected error broke this page. You can try again, or head back to the dashboard."
      action={
        <div className="flex gap-2">
          <Button onClick={() => unstable_retry()}>Try again</Button>
          <Link href="/">
            <Button variant="secondary">Go to dashboard</Button>
          </Link>
        </div>
      }
    />
  );
}
