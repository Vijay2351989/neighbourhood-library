"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

export interface BookFormValues {
  title: string;
  author: string;
  isbn: string;
  publishedYear: string; // bound as text; parsed on submit
  numberOfCopies: string;
}

export interface BookFormProps {
  mode: "create" | "edit";
  initial?: Partial<BookFormValues>;
  /** True in edit mode if the server reports this many currently-borrowed copies. */
  borrowedCopies?: number;
  /** Server-side field error keyed by camelCase field name. */
  fieldError?: { field: string; message: string } | null;
  /** Failed-precondition message for copies reconciliation (edit mode). */
  copiesPrecondition?: string | null;
  loading?: boolean;
  submitLabel?: string;
  onSubmit: (values: BookFormValues) => void;
  onCancel?: () => void;
}

const empty: BookFormValues = {
  title: "",
  author: "",
  isbn: "",
  publishedYear: "",
  numberOfCopies: "1",
};

export function BookForm({
  mode,
  initial,
  borrowedCopies,
  fieldError,
  copiesPrecondition,
  loading,
  submitLabel,
  onSubmit,
  onCancel,
}: BookFormProps) {
  const [values, setValues] = useState<BookFormValues>({
    ...empty,
    ...initial,
  });
  const [localErrors, setLocalErrors] = useState<Partial<Record<keyof BookFormValues, string>>>(
    {},
  );

  const update = <K extends keyof BookFormValues>(key: K, v: BookFormValues[K]) => {
    setValues((cur) => ({ ...cur, [key]: v }));
    setLocalErrors((cur) => ({ ...cur, [key]: undefined }));
  };

  function validate(): boolean {
    const next: Partial<Record<keyof BookFormValues, string>> = {};
    if (!values.title.trim()) next.title = "Title is required";
    if (!values.author.trim()) next.author = "Author is required";
    const copies = Number(values.numberOfCopies);
    if (!Number.isFinite(copies) || copies < 1) {
      next.numberOfCopies = "At least 1 copy";
    }
    if (values.publishedYear) {
      const y = Number(values.publishedYear);
      if (!Number.isInteger(y) || y < 0 || y > 9999) {
        next.publishedYear = "Enter a valid year";
      }
    }
    setLocalErrors(next);
    return Object.keys(next).length === 0;
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    onSubmit(values);
  }

  const fieldErr = (name: keyof BookFormValues): string | undefined => {
    if (localErrors[name]) return localErrors[name];
    if (fieldError && fieldError.field === name) return fieldError.message;
    return undefined;
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <Input
        id="title"
        label="Title"
        required
        value={values.title}
        onChange={(e) => update("title", e.target.value)}
        error={fieldErr("title")}
        disabled={loading}
      />
      <Input
        id="author"
        label="Author"
        required
        value={values.author}
        onChange={(e) => update("author", e.target.value)}
        error={fieldErr("author")}
        disabled={loading}
      />
      <div className="grid gap-5 md:grid-cols-2">
        <Input
          id="isbn"
          label="ISBN"
          hint="Optional"
          value={values.isbn}
          onChange={(e) => update("isbn", e.target.value)}
          error={fieldErr("isbn")}
          disabled={loading}
        />
        <Input
          id="publishedYear"
          label="Published year"
          hint="Optional"
          inputMode="numeric"
          value={values.publishedYear}
          onChange={(e) => update("publishedYear", e.target.value)}
          error={fieldErr("publishedYear")}
          disabled={loading}
        />
      </div>
      <Input
        id="numberOfCopies"
        label="Number of copies"
        required
        type="number"
        min={1}
        value={values.numberOfCopies}
        onChange={(e) => update("numberOfCopies", e.target.value)}
        error={fieldErr("numberOfCopies")}
        disabled={loading}
        hint={
          mode === "edit" && borrowedCopies != null && borrowedCopies > 0
            ? `${borrowedCopies} copy${borrowedCopies === 1 ? "" : "ies"} currently borrowed — cannot drop below this.`
            : undefined
        }
        inlineWarning={
          copiesPrecondition ? (
            <p className="text-xs text-amber-700" role="alert">
              {copiesPrecondition}
            </p>
          ) : null
        }
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
          {submitLabel ?? (mode === "create" ? "Create book" : "Save changes")}
        </Button>
      </div>
    </form>
  );
}
