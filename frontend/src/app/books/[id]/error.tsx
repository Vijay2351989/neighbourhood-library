"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/EmptyState";

// Per-segment boundary for /books/[id]. Same shape as the members detail
// boundary — a crash here doesn't lose the user's place on /books.

export default function BookDetailError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error("BookDetailError boundary caught:", error, {
      digest: error.digest,
    });
  }, [error]);

  return (
    <EmptyState
      title="Couldn't load this book"
      description="Something went wrong rendering this book's details."
      action={
        <div className="flex gap-2">
          <Button onClick={() => unstable_retry()}>Try again</Button>
          <Link href="/books">
            <Button variant="secondary">Back to books</Button>
          </Link>
        </div>
      }
    />
  );
}
