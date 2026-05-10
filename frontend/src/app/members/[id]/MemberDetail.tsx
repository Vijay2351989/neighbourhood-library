"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Code } from "@connectrpc/connect";
import { loanClient, memberClient } from "@/lib/client";
import { memberKeys } from "@/lib/queryKeys";
import { LoanFilter } from "@/generated/library/v1/loan_pb";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs } from "@/components/ui/Tabs";
import {
  EmptyRow,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
} from "@/components/ui/Table";
import { ReturnButton } from "@/components/ReturnButton";
import { EmptyState } from "@/components/EmptyState";
import { formatCents, formatDate } from "@/lib/format";
import { toFriendlyError } from "@/lib/errors";

type TabKey = "active" | "returned" | "all";
const FILTER_FOR: Record<TabKey, LoanFilter> = {
  active: LoanFilter.ACTIVE,
  returned: LoanFilter.RETURNED,
  all: LoanFilter.UNSPECIFIED,
};

export function MemberDetail({ id }: { id: string }) {
  const [tab, setTab] = useState<TabKey>("active");

  const memberQ = useQuery({
    queryKey: memberKeys.detail(id),
    queryFn: () => memberClient.getMember({ id: BigInt(id) }),
    retry: (count, err) =>
      toFriendlyError(err).code === Code.NotFound ? false : count < 1,
  });

  const loansQ = useQuery({
    queryKey: memberKeys.loans(id, FILTER_FOR[tab]),
    queryFn: () =>
      loanClient.getMemberLoans({
        memberId: BigInt(id),
        filter: FILTER_FOR[tab],
      }),
    enabled: memberQ.isSuccess,
  });

  if (memberQ.isLoading) {
    return (
      <div className="space-y-4">
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

  if (memberQ.error) {
    const f = toFriendlyError(memberQ.error);
    if (f.code === Code.NotFound) {
      return (
        <EmptyState
          title="Member not found"
          action={
            <Link href="/members">
              <Button variant="secondary">Back to members</Button>
            </Link>
          }
        />
      );
    }
    return (
      <EmptyState
        title="Couldn't load member"
        description={f.message}
        action={
          <Link href="/members">
            <Button variant="secondary">Back</Button>
          </Link>
        }
      />
    );
  }

  const member = memberQ.data?.member;
  if (!member) return null;
  const fine =
    typeof member.outstandingFinesCents === "bigint"
      ? member.outstandingFinesCents
      : 0n;

  return (
    <div className="space-y-6">
      <PageHeader
        title={member.name}
        description={member.email}
        actions={
          <Link href={`/members/${id}/edit`}>
            <Button variant="secondary">Edit</Button>
          </Link>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Contact" />
          <CardBody>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
              <div>
                <dt className="text-zinc-500">Email</dt>
                <dd className="text-zinc-900">{member.email}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">Phone</dt>
                <dd className="text-zinc-900">{member.phone ?? "—"}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-zinc-500">Address</dt>
                <dd className="whitespace-pre-line text-zinc-900">
                  {member.address ?? "—"}
                </dd>
              </div>
            </dl>
          </CardBody>
        </Card>

        {fine > 0n ? (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 shadow-sm">
            <p className="text-xs font-medium uppercase tracking-wide text-amber-700">
              Outstanding fines
            </p>
            <p className="mt-2 text-2xl font-semibold text-amber-900 tabular-nums">
              {formatCents(fine)}
            </p>
            <p className="mt-1 text-xs text-amber-800">
              Accumulated across overdue loans.
            </p>
          </div>
        ) : (
          <Card>
            <CardHeader title="Account" />
            <CardBody>
              <p className="text-sm text-zinc-600">No outstanding fines.</p>
            </CardBody>
          </Card>
        )}
      </div>

      <Card>
        <CardHeader title="Loan history" />
        <CardBody className="p-0">
          <div className="px-5 pt-3">
            <Tabs<TabKey>
              value={tab}
              onChange={setTab}
              options={[
                { value: "active", label: "Active" },
                { value: "returned", label: "Returned" },
                { value: "all", label: "All" },
              ]}
            />
          </div>
          <div className="px-2 pb-2 pt-3">
            <Table>
              <THead>
                <tr>
                  <TH>Book</TH>
                  <TH>Borrowed</TH>
                  <TH>Due</TH>
                  <TH>Returned</TH>
                  <TH className="text-right">Fine</TH>
                  <TH className="text-right">Action</TH>
                </tr>
              </THead>
              <TBody>
                {loansQ.isLoading ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-6">
                      <Skeleton width="40%" />
                    </td>
                  </tr>
                ) : loansQ.data?.loans.length === 0 ? (
                  <EmptyRow
                    cols={6}
                    message={
                      tab === "active"
                        ? "No active loans."
                        : tab === "returned"
                          ? "No returned loans."
                          : "No loans on record."
                    }
                  />
                ) : (
                  loansQ.data?.loans.map((l) => {
                    const f =
                      typeof l.fineCents === "bigint" ? l.fineCents : 0n;
                    return (
                      <TR
                        key={l.id.toString()}
                        highlighted={f > 0n}
                      >
                        <TD className="font-medium">{l.bookTitle}</TD>
                        <TD>{formatDate(l.borrowedAt)}</TD>
                        <TD
                          className={
                            l.overdue
                              ? "font-medium text-amber-700"
                              : ""
                          }
                        >
                          {formatDate(l.dueAt)}
                          {l.overdue ? " (overdue)" : ""}
                        </TD>
                        <TD>
                          {l.returnedAt ? formatDate(l.returnedAt) : "—"}
                        </TD>
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
                              memberId={BigInt(id)}
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
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
