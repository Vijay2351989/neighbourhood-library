"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Code } from "@connectrpc/connect";
import { client } from "@/lib/client";
import { bookKeys } from "@/lib/queryKeys";
import { BookForm } from "@/components/BookForm";
import type { BookFormValues } from "@/components/BookForm";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { toFriendlyError, toastMessage } from "@/lib/errors";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/Button";

export function BookEdit({ id }: { id: string }) {
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const [fieldError, setFieldError] = useState<{
    field: string;
    message: string;
  } | null>(null);
  const [copiesPrecondition, setCopiesPrecondition] = useState<string | null>(
    null,
  );

  const bookQ = useQuery({
    queryKey: bookKeys.detail(id),
    queryFn: () => client.getBook({ id: BigInt(id) }),
  });

  const mutation = useMutation({
    mutationFn: (v: BookFormValues) =>
      client.updateBook({
        id: BigInt(id),
        title: v.title.trim(),
        author: v.author.trim(),
        isbn: v.isbn.trim() ? v.isbn.trim() : undefined,
        publishedYear: v.publishedYear.trim()
          ? Number(v.publishedYear)
          : undefined,
        numberOfCopies: Number(v.numberOfCopies),
      }),
    onSuccess: () => {
      toast.success("Book updated.");
      qc.invalidateQueries({ queryKey: bookKeys.detail(id) });
      qc.invalidateQueries({ queryKey: bookKeys.lists() });
      router.push(`/books/${id}`);
    },
    onError: (err) => {
      const f = toFriendlyError(err);
      if (f.code === Code.FailedPrecondition) {
        // Most common case: trying to drop number_of_copies below borrowed.
        setCopiesPrecondition(f.message);
        return;
      }
      if (f.code === Code.InvalidArgument && f.field) {
        setFieldError({ field: f.field, message: f.message });
        return;
      }
      toast.error(toastMessage(err));
    },
  });

  if (bookQ.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton width={200} height={28} />
        <Card>
          <CardBody>
            <Skeleton width="60%" />
            <div className="mt-3">
              <Skeleton width="80%" />
            </div>
          </CardBody>
        </Card>
      </div>
    );
  }

  if (bookQ.error) {
    const f = toFriendlyError(bookQ.error);
    if (f.code === Code.NotFound) {
      return (
        <EmptyState
          title="Book not found"
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
            <Button variant="secondary">Back</Button>
          </Link>
        }
      />
    );
  }

  const book = bookQ.data?.book;
  if (!book) return null;

  const borrowed = book.totalCopies - book.availableCopies;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader title={`Edit "${book.title}"`} />
      <Card>
        <CardHeader title="Book details" />
        <CardBody>
          <BookForm
            mode="edit"
            initial={{
              title: book.title,
              author: book.author,
              isbn: book.isbn ?? "",
              publishedYear:
                book.publishedYear != null ? String(book.publishedYear) : "",
              numberOfCopies: String(book.totalCopies),
            }}
            borrowedCopies={borrowed}
            fieldError={fieldError}
            copiesPrecondition={copiesPrecondition}
            loading={mutation.isPending}
            onSubmit={(v) => {
              setFieldError(null);
              setCopiesPrecondition(null);
              mutation.mutate(v);
            }}
            onCancel={() => router.push(`/books/${id}`)}
          />
        </CardBody>
      </Card>
    </div>
  );
}
