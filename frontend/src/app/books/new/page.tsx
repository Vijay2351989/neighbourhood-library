"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bookClient } from "@/lib/client";
import { bookKeys } from "@/lib/queryKeys";
import { BookForm } from "@/components/BookForm";
import type { BookFormValues } from "@/components/BookForm";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { toFriendlyError, toastMessage } from "@/lib/errors";
import { Code } from "@connectrpc/connect";

export default function NewBookPage() {
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const [fieldError, setFieldError] = useState<{
    field: string;
    message: string;
  } | null>(null);

  const mutation = useMutation({
    mutationFn: (v: BookFormValues) =>
      bookClient.createBook({
        title: v.title.trim(),
        author: v.author.trim(),
        isbn: v.isbn.trim() ? v.isbn.trim() : undefined,
        publishedYear: v.publishedYear.trim()
          ? Number(v.publishedYear)
          : undefined,
        numberOfCopies: Number(v.numberOfCopies),
      }),
    onSuccess: (resp) => {
      toast.success("Book created.");
      qc.invalidateQueries({ queryKey: bookKeys.lists() });
      const id = resp.book?.id?.toString();
      if (id) router.push(`/books/${id}`);
      else router.push("/books");
    },
    onError: (err) => {
      const f = toFriendlyError(err);
      if (f.code === Code.InvalidArgument && f.field) {
        setFieldError({ field: f.field, message: f.message });
      } else {
        toast.error(toastMessage(err));
      }
    },
  });

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        title="New book"
        description="Add a new title to the catalog."
      />
      <Card>
        <CardHeader title="Book details" />
        <CardBody>
          <BookForm
            mode="create"
            loading={mutation.isPending}
            fieldError={fieldError}
            onSubmit={(v) => {
              setFieldError(null);
              mutation.mutate(v);
            }}
            onCancel={() => router.push("/books")}
          />
        </CardBody>
      </Card>
      <p className="text-sm text-zinc-500">
        <Link href="/books" className="hover:underline">
          ← Back to books
        </Link>
      </p>
    </div>
  );
}
