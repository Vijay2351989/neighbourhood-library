"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { client } from "@/lib/client";
import { bookKeys } from "@/lib/queryKeys";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/PageHeader";
import {
  EmptyRow,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
} from "@/components/ui/Table";
import { Pagination } from "@/components/ui/Pagination";
import { SkeletonRow } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { toastMessage } from "@/lib/errors";

const PAGE_SIZE = 25;

export function BooksList() {
  const router = useRouter();
  const sp = useSearchParams();
  const toast = useToast();

  const initialQ = sp.get("q") ?? "";
  const page = Math.max(1, Number(sp.get("page") ?? 1) || 1);
  const offset = (page - 1) * PAGE_SIZE;
  const search = initialQ.trim();

  // Local search input that debounces into the URL.
  const [searchInput, setSearchInput] = useState(initialQ);
  useEffect(() => {
    setSearchInput(initialQ);
  }, [initialQ]);
  useEffect(() => {
    const handle = setTimeout(() => {
      const q = searchInput.trim();
      const params = new URLSearchParams(sp.toString());
      if (q) params.set("q", q);
      else params.delete("q");
      // Searching resets page to 1.
      if (q !== initialQ) params.set("page", "1");
      const next = `/books${params.toString() ? `?${params.toString()}` : ""}`;
      router.replace(next, { scroll: false });
    }, 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  const queryParams = useMemo(
    () => ({ search, offset, pageSize: PAGE_SIZE }),
    [search, offset],
  );

  const { data, isLoading, error } = useQuery({
    queryKey: bookKeys.list(queryParams),
    queryFn: () =>
      client.listBooks({
        search: search || undefined,
        offset,
        pageSize: PAGE_SIZE,
      }),
  });

  useEffect(() => {
    if (error) toast.error(toastMessage(error));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error]);

  const setPage = (newOffset: number) => {
    const newPage = Math.floor(newOffset / PAGE_SIZE) + 1;
    const params = new URLSearchParams(sp.toString());
    params.set("page", String(newPage));
    router.replace(`/books?${params.toString()}`, { scroll: false });
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Books"
        description="Browse, search, and edit the catalog."
        actions={
          <Link href="/books/new">
            <Button>+ New book</Button>
          </Link>
        }
      />

      <div className="max-w-md">
        <Input
          aria-label="Search books"
          placeholder="Search by title or author..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
      </div>

      <Table>
        <THead>
          <tr>
            <TH>Title</TH>
            <TH>Author</TH>
            <TH>ISBN</TH>
            <TH>Year</TH>
            <TH className="text-right">Available / Total</TH>
          </tr>
        </THead>
        <TBody>
          {isLoading ? (
            Array.from({ length: 6 }).map((_, i) => (
              <SkeletonRow key={i} cols={5} />
            ))
          ) : data?.books.length === 0 ? (
            <EmptyRow
              cols={5}
              message={
                search
                  ? `No books match "${search}".`
                  : "No books yet. Create your first one."
              }
            />
          ) : (
            data?.books.map((b) => (
              <TR
                key={b.id.toString()}
                onClick={() => router.push(`/books/${b.id.toString()}`)}
              >
                <TD className="font-medium">{b.title}</TD>
                <TD>{b.author}</TD>
                <TD className="text-zinc-500">{b.isbn ?? "—"}</TD>
                <TD className="text-zinc-500">{b.publishedYear ?? "—"}</TD>
                <TD className="text-right tabular-nums">
                  <span
                    className={
                      b.availableCopies === 0
                        ? "font-medium text-amber-700"
                        : "text-zinc-800"
                    }
                  >
                    {b.availableCopies}
                  </span>
                  <span className="text-zinc-400"> / {b.totalCopies}</span>
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>

      <Pagination
        offset={offset}
        pageSize={PAGE_SIZE}
        totalCount={data?.totalCount ?? 0}
        onChange={setPage}
      />
    </div>
  );
}
