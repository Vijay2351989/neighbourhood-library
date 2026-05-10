"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Code } from "@connectrpc/connect";
import { memberClient } from "@/lib/client";
import { memberKeys } from "@/lib/queryKeys";
import { MemberForm } from "@/components/MemberForm";
import type { MemberFormValues } from "@/components/MemberForm";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { toFriendlyError, toastMessage } from "@/lib/errors";

export default function NewMemberPage() {
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const [fieldError, setFieldError] = useState<{
    field: string;
    message: string;
  } | null>(null);
  const [emailConflict, setEmailConflict] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (v: MemberFormValues) =>
      memberClient.createMember({
        name: v.name.trim(),
        email: v.email.trim(),
        phone: v.phone.trim() ? v.phone.trim() : undefined,
        address: v.address.trim() ? v.address.trim() : undefined,
      }),
    onSuccess: (resp) => {
      toast.success("Member created.");
      qc.invalidateQueries({ queryKey: memberKeys.lists() });
      const id = resp.member?.id?.toString();
      if (id) router.push(`/members/${id}`);
      else router.push("/members");
    },
    onError: (err) => {
      const f = toFriendlyError(err);
      if (f.code === Code.AlreadyExists) {
        setEmailConflict("A member with that email already exists.");
        return;
      }
      if (f.code === Code.InvalidArgument && f.field) {
        setFieldError({ field: f.field, message: f.message });
        return;
      }
      toast.error(toastMessage(err));
    },
  });

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        title="New member"
        description="Add a new library patron."
      />
      <Card>
        <CardHeader title="Member info" />
        <CardBody>
          <MemberForm
            mode="create"
            loading={mutation.isPending}
            fieldError={fieldError}
            emailConflict={emailConflict}
            onSubmit={(v) => {
              setFieldError(null);
              setEmailConflict(null);
              mutation.mutate(v);
            }}
            onCancel={() => router.push("/members")}
          />
        </CardBody>
      </Card>
      <p className="text-sm text-zinc-500">
        <Link href="/members" className="hover:underline">
          ← Back to members
        </Link>
      </p>
    </div>
  );
}
