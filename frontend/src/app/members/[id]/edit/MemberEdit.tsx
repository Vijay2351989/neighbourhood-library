"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Code } from "@connectrpc/connect";
import { memberClient } from "@/lib/client";
import { memberKeys } from "@/lib/queryKeys";
import { MemberForm } from "@/components/MemberForm";
import type { MemberFormValues } from "@/components/MemberForm";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { toFriendlyError, toastMessage } from "@/lib/errors";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/Button";

export function MemberEdit({ id }: { id: string }) {
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const [fieldError, setFieldError] = useState<{
    field: string;
    message: string;
  } | null>(null);
  const [emailConflict, setEmailConflict] = useState<string | null>(null);

  const memberQ = useQuery({
    queryKey: memberKeys.detail(id),
    queryFn: () => memberClient.getMember({ id: BigInt(id) }),
  });

  const mutation = useMutation({
    mutationFn: (v: MemberFormValues) =>
      memberClient.updateMember({
        id: BigInt(id),
        name: v.name.trim(),
        email: v.email.trim(),
        phone: v.phone.trim() ? v.phone.trim() : undefined,
        address: v.address.trim() ? v.address.trim() : undefined,
      }),
    onSuccess: () => {
      toast.success("Member updated.");
      qc.invalidateQueries({ queryKey: memberKeys.detail(id) });
      qc.invalidateQueries({ queryKey: memberKeys.lists() });
      router.push(`/members/${id}`);
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

  if (memberQ.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton width={200} height={28} />
        <Card>
          <CardBody>
            <Skeleton width="60%" />
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

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader title={`Edit ${member.name}`} />
      <Card>
        <CardHeader title="Member info" />
        <CardBody>
          <MemberForm
            mode="edit"
            initial={{
              name: member.name,
              email: member.email,
              phone: member.phone ?? "",
              address: member.address ?? "",
            }}
            fieldError={fieldError}
            emailConflict={emailConflict}
            loading={mutation.isPending}
            onSubmit={(v) => {
              setFieldError(null);
              setEmailConflict(null);
              mutation.mutate(v);
            }}
            onCancel={() => router.push(`/members/${id}`)}
          />
        </CardBody>
      </Card>
    </div>
  );
}
