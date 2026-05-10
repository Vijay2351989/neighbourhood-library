"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Code } from "@connectrpc/connect";
import { loanClient } from "@/lib/client";
import { bookKeys, loanKeys, memberKeys } from "@/lib/queryKeys";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { toFriendlyError, toastMessage } from "@/lib/errors";

export interface ReturnButtonProps {
  loanId: bigint;
  memberId?: bigint;
  bookTitle: string;
  /** Compact rendering for table rows. */
  size?: "sm" | "md";
}

export function ReturnButton({
  loanId,
  memberId,
  bookTitle,
  size = "sm",
}: ReturnButtonProps) {
  const [open, setOpen] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const toast = useToast();
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => loanClient.returnBook({ loanId }),
    onSuccess: () => {
      toast.success("Returned.");
      qc.invalidateQueries({ queryKey: loanKeys.lists() });
      qc.invalidateQueries({ queryKey: bookKeys.all });
      if (memberId) {
        qc.invalidateQueries({ queryKey: memberKeys.detail(memberId.toString()) });
      } else {
        qc.invalidateQueries({ queryKey: memberKeys.all });
      }
      setOpen(false);
      setInlineError(null);
    },
    onError: (err) => {
      const f = toFriendlyError(err);
      if (f.code === Code.FailedPrecondition) {
        setInlineError(f.message);
        return;
      }
      toast.error(toastMessage(err));
    },
  });

  return (
    <>
      <Button
        size={size}
        variant="secondary"
        onClick={(e) => {
          e.stopPropagation();
          setInlineError(null);
          setOpen(true);
        }}
      >
        Return
      </Button>
      <Dialog
        open={open}
        title="Return this loan?"
        description={
          <span>
            Mark <strong>{bookTitle}</strong> as returned.
          </span>
        }
        confirmLabel="Return"
        loading={mutation.isPending}
        onClose={() => {
          setOpen(false);
          setInlineError(null);
        }}
        onConfirm={() => mutation.mutate()}
      >
        {inlineError ? (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {inlineError}
          </div>
        ) : null}
      </Dialog>
    </>
  );
}
