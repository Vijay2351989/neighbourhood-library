"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Code } from "@connectrpc/connect";
import { bookClient, loanClient, memberClient } from "@/lib/client";
import { bookKeys, loanKeys, memberKeys } from "@/lib/queryKeys";
import type { Book } from "@/generated/library/v1/book_pb";
import type { Member } from "@/generated/library/v1/member_pb";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { EntityPicker } from "@/components/EntityPicker";
import { BorrowDialog } from "@/components/BorrowDialog";
import { useToast } from "@/components/ui/Toast";
import { dateInputToTimestamp } from "@/lib/format";
import { toFriendlyError, toastMessage } from "@/lib/errors";

export function NewLoanForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const toast = useToast();
  const qc = useQueryClient();

  const [member, setMember] = useState<Member | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [dueDate, setDueDate] = useState<string>(""); // YYYY-MM-DD
  const [precondition, setPrecondition] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Allow deep-linking, e.g. /loans/new?memberId=42 prefills the member.
  // Effect runs once on mount; subsequent edits drive state directly.
  useEffect(() => {
    const memberId = sp.get("memberId");
    if (memberId) {
      memberClient
        .getMember({ id: BigInt(memberId) })
        .then((r) => r.member && setMember(r.member))
        .catch(() => {
          /* ignore — picker still works */
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mutation = useMutation({
    mutationFn: () => {
      if (!member || !book) throw new Error("Member and book are required.");
      return loanClient.borrowBook({
        memberId: member.id,
        bookId: book.id,
        dueAt: dateInputToTimestamp(dueDate),
      });
    },
    onSuccess: (resp) => {
      toast.success("Borrow recorded.");
      qc.invalidateQueries({ queryKey: loanKeys.lists() });
      qc.invalidateQueries({ queryKey: bookKeys.all });
      qc.invalidateQueries({ queryKey: memberKeys.all });
      setConfirmOpen(false);
      const memberId = resp.loan?.memberId?.toString();
      if (memberId) router.push(`/members/${memberId}`);
      else router.push("/loans");
    },
    onError: (err) => {
      const f = toFriendlyError(err);
      if (f.code === Code.FailedPrecondition) {
        setPrecondition(f.message);
        return;
      }
      toast.error(toastMessage(err));
    },
  });

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        title="New loan"
        description="Record a member borrowing a book."
      />
      <Card>
        <CardHeader title="Borrow flow" />
        <CardBody>
          <form
            className="space-y-5"
            onSubmit={(e) => {
              e.preventDefault();
              if (!member || !book) return;
              setPrecondition(null);
              // Open the confirmation dialog before firing the RPC.
              setConfirmOpen(true);
            }}
          >
            <EntityPicker<Member>
              label="Member"
              placeholder="Search members by name or email..."
              keyOf={(m) => m.id.toString()}
              labelOf={(m) => `${m.name} — ${m.email}`}
              renderItem={(m) => (
                <>
                  <div className="font-medium text-zinc-900">{m.name}</div>
                  <div className="text-xs text-zinc-500">{m.email}</div>
                </>
              )}
              search={async (q) => {
                const r = await memberClient.listMembers({
                  search: q || undefined,
                  pageSize: 10,
                });
                return r.members;
              }}
              selected={member}
              onSelect={setMember}
            />

            <EntityPicker<Book>
              label="Book"
              placeholder="Search books by title or author..."
              keyOf={(b) => b.id.toString()}
              labelOf={(b) => `${b.title} — ${b.author}`}
              renderItem={(b) => (
                <>
                  <div className="font-medium text-zinc-900">{b.title}</div>
                  <div className="text-xs text-zinc-500">
                    {b.author} · {b.availableCopies} of {b.totalCopies} available
                  </div>
                </>
              )}
              search={async (q) => {
                const r = await bookClient.listBooks({
                  search: q || undefined,
                  pageSize: 10,
                });
                return r.books;
              }}
              isSelectable={(b) => b.availableCopies > 0}
              unselectableHint="(no copies available)"
              selected={book}
              onSelect={setBook}
            />

            <Input
              id="dueDate"
              label="Due date"
              type="date"
              hint="Optional — defaults to 14 days from today on the server."
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
            />

            <div className="flex justify-end gap-2 pt-1">
              <Button
                type="button"
                variant="secondary"
                onClick={() => router.push("/loans")}
                disabled={mutation.isPending}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={!member || !book}
              >
                Review borrow
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
      <p className="text-sm text-zinc-500">
        <Link href="/loans" className="hover:underline">
          ← Back to loans
        </Link>
      </p>
      {member && book ? (
        <BorrowDialog
          open={confirmOpen}
          member={member}
          book={book}
          dueLabel={dueDate || undefined}
          loading={mutation.isPending}
          inlineError={precondition}
          onClose={() => {
            if (mutation.isPending) return;
            setConfirmOpen(false);
            setPrecondition(null);
          }}
          onConfirm={() => {
            setPrecondition(null);
            mutation.mutate();
          }}
        />
      ) : null}
    </div>
  );
}
