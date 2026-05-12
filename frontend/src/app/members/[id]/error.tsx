"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/EmptyState";

// Per-segment boundary for /members/[id]. A bad member detail page won't
// take down /members or the rest of the app — the boundary contains the
// crash to this route. Errors that escape this still bubble up to
// app/error.tsx.

export default function MemberDetailError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error("MemberDetailError boundary caught:", error, {
      digest: error.digest,
    });
  }, [error]);

  return (
    <EmptyState
      title="Couldn't load this member"
      description="Something went wrong rendering this member's details."
      action={
        <div className="flex gap-2">
          <Button onClick={() => unstable_retry()}>Try again</Button>
          <Link href="/members">
            <Button variant="secondary">Back to members</Button>
          </Link>
        </div>
      }
    />
  );
}
