"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { memberClient } from "@/lib/client";
import { memberKeys } from "@/lib/queryKeys";
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
import { formatCents } from "@/lib/format";

const PAGE_SIZE = 25;

export function MembersList() {
  const router = useRouter();
  const sp = useSearchParams();
  const toast = useToast();

  const initialQ = sp.get("q") ?? "";
  const page = Math.max(1, Number(sp.get("page") ?? 1) || 1);
  const offset = (page - 1) * PAGE_SIZE;
  const search = initialQ.trim();

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
      if (q !== initialQ) params.set("page", "1");
      const next = `/members${
        params.toString() ? `?${params.toString()}` : ""
      }`;
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
    queryKey: memberKeys.list(queryParams),
    queryFn: () =>
      memberClient.listMembers({
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
    router.replace(`/members?${params.toString()}`, { scroll: false });
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Members"
        description="Library patrons. Manage their contact info and review their loan history."
        actions={
          <Link href="/members/new">
            <Button>+ New member</Button>
          </Link>
        }
      />

      <div className="max-w-md">
        <Input
          aria-label="Search members"
          placeholder="Search by name or email..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
      </div>

      <Table>
        <THead>
          <tr>
            <TH>Name</TH>
            <TH>Email</TH>
            <TH>Phone</TH>
            <TH className="text-right">Outstanding fines</TH>
          </tr>
        </THead>
        <TBody>
          {isLoading ? (
            Array.from({ length: 6 }).map((_, i) => (
              <SkeletonRow key={i} cols={4} />
            ))
          ) : data?.members.length === 0 ? (
            <EmptyRow
              cols={4}
              message={
                search
                  ? `No members match "${search}".`
                  : "No members yet. Create your first one."
              }
            />
          ) : (
            data?.members.map((m) => {
              const fine =
                typeof m.outstandingFinesCents === "bigint"
                  ? m.outstandingFinesCents
                  : 0n;
              return (
                <TR
                  key={m.id.toString()}
                  onClick={() => router.push(`/members/${m.id.toString()}`)}
                  highlighted={fine > 0n}
                >
                  <TD className="font-medium">{m.name}</TD>
                  <TD className="text-zinc-500">{m.email}</TD>
                  <TD className="text-zinc-500">{m.phone ?? "—"}</TD>
                  <TD className="text-right tabular-nums">
                    {fine > 0n ? (
                      <span className="font-medium text-amber-700">
                        {formatCents(fine)}
                      </span>
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
