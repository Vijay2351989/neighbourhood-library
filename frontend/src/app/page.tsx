"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { client } from "@/lib/client";
import { bookKeys, loanKeys, memberKeys } from "@/lib/queryKeys";
import { LoanFilter } from "@/generated/library/v1/library_pb";
import { formatCents, formatDate } from "@/lib/format";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { StatTile } from "@/components/StatTile";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { useEffect } from "react";
import { toastMessage } from "@/lib/errors";

export default function DashboardPage() {
  const toast = useToast();

  const booksQ = useQuery({
    queryKey: bookKeys.list({ pageSize: 1 }),
    queryFn: () => client.listBooks({ pageSize: 1 }),
  });
  const membersQ = useQuery({
    queryKey: memberKeys.list({ pageSize: 1 }),
    queryFn: () => client.listMembers({ pageSize: 1 }),
  });
  const activeQ = useQuery({
    queryKey: loanKeys.list({ filter: LoanFilter.ACTIVE, pageSize: 1 }),
    queryFn: () =>
      client.listLoans({ filter: LoanFilter.ACTIVE, pageSize: 1 }),
  });
  const overdueQ = useQuery({
    queryKey: loanKeys.list({ filter: LoanFilter.OVERDUE, pageSize: 1 }),
    queryFn: () =>
      client.listLoans({ filter: LoanFilter.OVERDUE, pageSize: 1 }),
  });
  const finedQ = useQuery({
    queryKey: loanKeys.list({ filter: LoanFilter.HAS_FINE, pageSize: 100 }),
    queryFn: () =>
      client.listLoans({ filter: LoanFilter.HAS_FINE, pageSize: 100 }),
  });
  const recentQ = useQuery({
    queryKey: loanKeys.list({ pageSize: 10 }),
    queryFn: () => client.listLoans({ pageSize: 10 }),
  });

  // Aggregate fine total client-side. Backend doesn't yet expose a sum-fines
  // RPC; summing the HAS_FINE list is correct because rows with fine_cents=0
  // are excluded server-side.
  const totalFinesCents =
    finedQ.data?.loans.reduce(
      (acc, l) => acc + (typeof l.fineCents === "bigint" ? l.fineCents : 0n),
      0n,
    ) ?? 0n;

  // Surface the first error encountered as a toast.
  useEffect(() => {
    const errs = [booksQ, membersQ, activeQ, overdueQ, finedQ, recentQ]
      .map((q) => q.error)
      .filter(Boolean);
    if (errs.length > 0) {
      toast.error(toastMessage(errs[0]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    booksQ.error,
    membersQ.error,
    activeQ.error,
    overdueQ.error,
    finedQ.error,
    recentQ.error,
  ]);

  return (
    <div className="space-y-8">
      <PageHeader
        title="Dashboard"
        description="At-a-glance health of the lending desk."
      />

      <section
        aria-label="Counts"
        className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5"
      >
        <StatTile
          label="Total books"
          value={(booksQ.data?.totalCount ?? 0).toLocaleString()}
          loading={booksQ.isLoading}
        />
        <StatTile
          label="Total members"
          value={(membersQ.data?.totalCount ?? 0).toLocaleString()}
          loading={membersQ.isLoading}
        />
        <StatTile
          label="Active loans"
          value={(activeQ.data?.totalCount ?? 0).toLocaleString()}
          loading={activeQ.isLoading}
        />
        <StatTile
          label="Overdue"
          value={(overdueQ.data?.totalCount ?? 0).toLocaleString()}
          emphasize={
            (overdueQ.data?.totalCount ?? 0) > 0 ? "warning" : undefined
          }
          loading={overdueQ.isLoading}
        />
        <StatTile
          label="Outstanding fines"
          value={formatCents(totalFinesCents)}
          emphasize={totalFinesCents > 0n ? "warning" : undefined}
          loading={finedQ.isLoading}
        />
      </section>

      <Card>
        <CardHeader
          title="Recent activity"
          description="The 10 most recent loans across the library."
        />
        <CardBody className="p-0">
          {recentQ.isLoading ? (
            <ul className="divide-y divide-zinc-100">
              {Array.from({ length: 5 }).map((_, i) => (
                <li key={i} className="px-5 py-3">
                  <Skeleton width="40%" />
                  <div className="mt-2">
                    <Skeleton width="20%" height={10} />
                  </div>
                </li>
              ))}
            </ul>
          ) : recentQ.data?.loans.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-zinc-500">
              No loans yet. Start at{" "}
              <Link
                href="/loans/new"
                className="font-medium text-blue-700 hover:underline"
              >
                Loans → New
              </Link>
              .
            </p>
          ) : (
            <ul className="divide-y divide-zinc-100">
              {recentQ.data?.loans.map((l) => {
                const active = !l.returnedAt;
                return (
                  <li
                    key={l.id.toString()}
                    className="flex items-center justify-between px-5 py-3"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-zinc-900">
                        {l.bookTitle}
                        <span className="text-zinc-500"> · {l.memberName}</span>
                      </p>
                      <p className="text-xs text-zinc-500">
                        Borrowed {formatDate(l.borrowedAt)}
                        {l.returnedAt
                          ? ` · returned ${formatDate(l.returnedAt)}`
                          : ` · due ${formatDate(l.dueAt)}`}
                      </p>
                    </div>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                        l.overdue
                          ? "bg-amber-100 text-amber-800"
                          : active
                            ? "bg-blue-100 text-blue-700"
                            : "bg-zinc-100 text-zinc-600"
                      }`}
                    >
                      {l.overdue ? "Overdue" : active ? "Active" : "Returned"}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
