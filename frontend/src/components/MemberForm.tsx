"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { Input, Textarea } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

export interface MemberFormValues {
  name: string;
  email: string;
  phone: string;
  address: string;
}

export interface MemberFormProps {
  mode: "create" | "edit";
  initial?: Partial<MemberFormValues>;
  fieldError?: { field: string; message: string } | null;
  /** Already-exists message displayed inline on email. */
  emailConflict?: string | null;
  loading?: boolean;
  onSubmit: (values: MemberFormValues) => void;
  onCancel?: () => void;
}

const empty: MemberFormValues = {
  name: "",
  email: "",
  phone: "",
  address: "",
};

export function MemberForm({
  mode,
  initial,
  fieldError,
  emailConflict,
  loading,
  onSubmit,
  onCancel,
}: MemberFormProps) {
  const [values, setValues] = useState<MemberFormValues>({
    ...empty,
    ...initial,
  });
  const [localErrors, setLocalErrors] = useState<Partial<Record<keyof MemberFormValues, string>>>(
    {},
  );

  const update = <K extends keyof MemberFormValues>(key: K, v: MemberFormValues[K]) => {
    setValues((cur) => ({ ...cur, [key]: v }));
    setLocalErrors((cur) => ({ ...cur, [key]: undefined }));
  };

  function validate(): boolean {
    const next: Partial<Record<keyof MemberFormValues, string>> = {};
    if (!values.name.trim()) next.name = "Name is required";
    if (!values.email.trim()) next.email = "Email is required";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email))
      next.email = "Enter a valid email";
    setLocalErrors(next);
    return Object.keys(next).length === 0;
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    onSubmit(values);
  }

  const fieldErr = (name: keyof MemberFormValues): string | undefined => {
    if (localErrors[name]) return localErrors[name];
    if (name === "email" && emailConflict) return emailConflict;
    if (fieldError && fieldError.field === name) return fieldError.message;
    return undefined;
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <Input
        id="name"
        label="Name"
        required
        value={values.name}
        onChange={(e) => update("name", e.target.value)}
        error={fieldErr("name")}
        disabled={loading}
      />
      <Input
        id="email"
        label="Email"
        required
        type="email"
        value={values.email}
        onChange={(e) => update("email", e.target.value)}
        error={fieldErr("email")}
        disabled={loading}
      />
      <Input
        id="phone"
        label="Phone"
        hint="Optional"
        value={values.phone}
        onChange={(e) => update("phone", e.target.value)}
        error={fieldErr("phone")}
        disabled={loading}
      />
      <Textarea
        id="address"
        label="Address"
        hint="Optional"
        rows={3}
        value={values.address}
        onChange={(e) => update("address", e.target.value)}
        error={fieldErr("address")}
        disabled={loading}
      />

      <div className="flex items-center justify-end gap-2 pt-2">
        {onCancel ? (
          <Button
            type="button"
            variant="secondary"
            onClick={onCancel}
            disabled={loading}
          >
            Cancel
          </Button>
        ) : null}
        <Button type="submit" loading={loading}>
          {mode === "create" ? "Create member" : "Save changes"}
        </Button>
      </div>
    </form>
  );
}
