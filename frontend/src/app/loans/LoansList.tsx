"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { loanClient } from "@/lib/client";
import { loanKeys } from "@/lib/queryKeys";
import { LoanFilter } from "@/generated/library/v1/loan_pb";
import { Button } from "@/components/ui/Button";
import { ChipFilter } from "@/components/ui/Tabs";
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
import { ReturnButton } from "@/components/ReturnButton";
import { useToast } from "@/components/ui/Toast";
import { formatCents, formatDate } from "@/lib/format";
import { toastMessage } from "@/lib/errors";

const PAGE_SIZE = 25;

type FilterKey = "all" | "active" | "overdue" | "fine" | "returned";

const FILTER_TO_PROTO: Record<FilterKey, LoanFilter> = {
  all: LoanFilter.UNSPECIFIED,
  active: LoanFilter.ACTIVE,
  overdue: LoanFilter.OVERDUE,
  fine: LoanFilter.HAS_FINE,
  returned: LoanFilter.RETURNED,
};

const FILTER_LABEL: Record<FilterKey, string> = {
  all: "All",
  active: "Active",
  overdue: "Overdue",
  fine: "Has fine",
  returned: "Returned",
};

function isFilterKey(v: string | null): v is FilterKey {
  return v === "all" || v === "active" || v === "overdue" || v === "fine" || v === "returned";
}

export function LoansList() {
  const router = useRouter();
  const sp = useSearchParams();
  const toast = useToast();

  const filterParam = sp.get("filter");
  const filter: FilterKey = isFilterKey(filterParam) ? filterParam : "all";
  const page = Math.max(1, Number(sp.get("page") ?? 1) || 1);
  const offset = (page - 1) * PAGE_SIZE;

  const { data, isLoading, error } = useQuery({
    queryKey: loanKeys.list({
      filter: FILTER_TO_PROTO[filter],
      offset,
      pageSize: PAGE_SIZE,
    }),
    queryFn: () =>
      loanClient.listLoans({
        filter: FILTER_TO_PROTO[filter],
        offset,
        pageSize: PAGE_SIZE,
      }),
  });

  useEffect(() => {
    if (error) toast.error(toastMessage(error));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error]);

  const setFilter = (next: FilterKey) => {
    const params = new URLSearchParams(sp.toString());
    if (next === "all") params.delete("filter");
    else params.set("filter", next);
    params.set("page", "1");
    router.replace(
      `/loans${params.toString() ? `?${params.toString()}` : ""}`,
      { scroll: false },
    );
  };

  const setPage = (newOffset: number) => {
    const newPage = Math.floor(newOffset / PAGE_SIZE) + 1;
    const params = new URLSearchParams(sp.toString());
    params.set("page", String(newPage));
    router.replace(`/loans?${params.toString()}`, { scroll: false });
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Loans"
        description="Every borrow and return on record."
        actions={
          <Link href="/loans/new">
            <Button>+ New loan</Button>
          </Link>
        }
      />

      <ChipFilter<FilterKey>
        value={filter}
        onChange={setFilter}
        options={(["all", "active", "overdue", "fine", "returned"] as FilterKey[]).map(
          (k) => ({ value: k, label: FILTER_LABEL[k] }),
        )}
      />

      <Table>
        <THead>
          <tr>
            <TH>Member</TH>
            <TH>Book</TH>
            <TH>Borrowed</TH>
            <TH>Due</TH>
            <TH>Returned</TH>
            <TH className="text-right">Fine</TH>
            <TH className="text-right">Action</TH>
          </tr>
        </THead>
        <TBody>
          {isLoading ? (
            Array.from({ length: 6 }).map((_, i) => (
              <SkeletonRow key={i} cols={7} />
            ))
          ) : data?.loans.length === 0 ? (
            <EmptyRow cols={7} message="No loans match this filter." />
          ) : (
            data?.loans.map((l) => {
              const f = typeof l.fineCents === "bigint" ? l.fineCents : 0n;
              return (
                <TR key={l.id.toString()} highlighted={f > 0n}>
                  <TD className="font-medium">
                    <Link
                      href={`/members/${l.memberId.toString()}`}
                      className="hover:underline"
                    >
                      {l.memberName}
                    </Link>
                  </TD>
                  <TD>
                    <Link
                      href={`/books/${l.bookId.toString()}`}
                      className="hover:underline"
                    >
                      {l.bookTitle}
                    </Link>
                    <div className="text-xs text-zinc-500">{l.bookAuthor}</div>
                  </TD>
                  <TD>{formatDate(l.borrowedAt)}</TD>
                  <TD className={l.overdue ? "font-medium text-amber-700" : ""}>
                    {formatDate(l.dueAt)}
                    {l.overdue ? " (overdue)" : ""}
                  </TD>
                  <TD>{l.returnedAt ? formatDate(l.returnedAt) : "—"}</TD>
                  <TD className="text-right tabular-nums">
                    {f > 0n ? (
                      <span className="font-medium text-amber-700">
                        {formatCents(f)}
                      </span>
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </TD>
                  <TD className="text-right">
                    {!l.returnedAt ? (
                      <ReturnButton
                        loanId={l.id}
                        memberId={l.memberId}
                        bookTitle={l.bookTitle}
                      />
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </TD>
                </TR>
              );
            })
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
