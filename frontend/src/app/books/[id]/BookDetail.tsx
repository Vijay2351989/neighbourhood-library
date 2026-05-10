"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Code } from "@connectrpc/connect";
import { bookClient } from "@/lib/client";
import { bookKeys } from "@/lib/queryKeys";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/EmptyState";
import { formatDateTime } from "@/lib/format";
import { toFriendlyError } from "@/lib/errors";

export function BookDetail({ id }: { id: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: bookKeys.detail(id),
    queryFn: () => bookClient.getBook({ id: BigInt(id) }),
    retry: (count, err) => {
      const f = toFriendlyError(err);
      // Don't retry NOT_FOUND.
      if (f.code === Code.NotFound) return false;
      return count < 1;
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton width={220} height={28} />
        <Card>
          <CardBody>
            <Skeleton width="60%" />
            <div className="mt-3">
              <Skeleton width="40%" />
            </div>
          </CardBody>
        </Card>
      </div>
    );
  }

  if (error) {
    const f = toFriendlyError(error);
    if (f.code === Code.NotFound) {
      return (
        <EmptyState
          title="Book not found"
          description="It may have been deleted, or the link is wrong."
          action={
            <Link href="/books">
              <Button variant="secondary">Back to books</Button>
            </Link>
          }
        />
      );
    }
    return (
      <EmptyState
        title="Couldn't load book"
        description={f.message}
        action={
          <Link href="/books">
            <Button variant="secondary">Back to books</Button>
          </Link>
        }
      />
    );
  }

  const book = data?.book;
  if (!book) return null;

  return (
    <div className="space-y-6">
      <PageHeader
        title={book.title}
        description={`by ${book.author}`}
        actions={
          <Link href={`/books/${id}/edit`}>
            <Button variant="secondary">Edit</Button>
          </Link>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Details" />
          <CardBody>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
              <div>
                <dt className="text-zinc-500">ISBN</dt>
                <dd className="text-zinc-900">{book.isbn ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">Published year</dt>
                <dd className="text-zinc-900">{book.publishedYear ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">Created</dt>
                <dd className="text-zinc-900">
                  {formatDateTime(book.createdAt)}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">Last updated</dt>
                <dd className="text-zinc-900">
                  {formatDateTime(book.updatedAt)}
                </dd>
              </div>
            </dl>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Inventory" />
          <CardBody>
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold tabular-nums text-zinc-900">
                {book.availableCopies}
              </span>
              <span className="text-zinc-500">
                / {book.totalCopies} available
              </span>
            </div>
            <p className="mt-2 text-xs text-zinc-500">
              {book.totalCopies - book.availableCopies} currently borrowed
            </p>
          </CardBody>
        </Card>
      </div>

      <p className="text-sm text-zinc-500">
        <Link href="/books" className="hover:underline">
          ← Back to books
        </Link>
      </p>
    </div>
  );
}
