"use client";

import type { Book } from "@/generated/library/v1/book_pb";
import type { Member } from "@/generated/library/v1/member_pb";
import { Dialog } from "@/components/ui/Dialog";

export interface BorrowDialogProps {
  open: boolean;
  member: Member;
  book: Book;
  /** Optional pretty due date string for the summary. */
  dueLabel?: string;
  loading?: boolean;
  /** Inline error rendered inside the dialog (e.g. FAILED_PRECONDITION). */
  inlineError?: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

/**
 * Confirmation step for the borrow flow. Renders a summary of who's borrowing
 * what and surfaces any FAILED_PRECONDITION error from the server inline so
 * the user can adjust without losing the picker selections.
 */
export function BorrowDialog({
  open,
  member,
  book,
  dueLabel,
  loading,
  inlineError,
  onClose,
  onConfirm,
}: BorrowDialogProps) {
  return (
    <Dialog
      open={open}
      title="Confirm borrow"
      description="Once confirmed, the loan is recorded and a copy is held."
      confirmLabel="Confirm borrow"
      loading={loading}
      onClose={onClose}
      onConfirm={onConfirm}
    >
      <dl className="grid grid-cols-3 gap-y-2 text-sm">
        <dt className="text-zinc-500">Member</dt>
        <dd className="col-span-2 font-medium text-zinc-900">{member.name}</dd>
        <dt className="text-zinc-500">Book</dt>
        <dd className="col-span-2 font-medium text-zinc-900">
          {book.title}
          <span className="font-normal text-zinc-500"> — {book.author}</span>
        </dd>
        <dt className="text-zinc-500">Due</dt>
        <dd className="col-span-2 text-zinc-900">
          {dueLabel ?? "14 days from today (server default)"}
        </dd>
      </dl>
      {inlineError ? (
        <div
          role="alert"
          className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          {inlineError}
        </div>
      ) : null}
    </Dialog>
  );
}
