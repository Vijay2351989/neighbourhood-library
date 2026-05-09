"use client";

import { forwardRef } from "react";
import type {
  InputHTMLAttributes,
  TextareaHTMLAttributes,
  SelectHTMLAttributes,
  ReactNode,
} from "react";

const fieldBase =
  "block w-full rounded-md border bg-white px-3 py-2 text-sm text-zinc-900 " +
  "shadow-sm placeholder-zinc-400 transition-colors " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-0 " +
  "disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-500";

const fieldOk =
  "border-zinc-300 focus:border-blue-500 focus-visible:ring-blue-500";

const fieldErr =
  "border-red-400 focus:border-red-500 focus-visible:ring-red-400";

export interface FieldShellProps {
  label?: string;
  hint?: string;
  error?: string;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
  /** Inline message rendered next to the field, e.g. server precondition errors. */
  inlineWarning?: ReactNode;
}

export function FieldShell({
  label,
  hint,
  error,
  required,
  htmlFor,
  children,
  inlineWarning,
}: FieldShellProps) {
  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label
          htmlFor={htmlFor}
          className="text-sm font-medium text-zinc-700"
        >
          {label}
          {required ? <span className="ml-0.5 text-red-500">*</span> : null}
        </label>
      ) : null}
      {children}
      {error ? (
        <p className="text-xs text-red-600" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-zinc-500">{hint}</p>
      ) : null}
      {inlineWarning}
    </div>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  inlineWarning?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, required, id, className = "", inlineWarning, ...rest },
  ref,
) {
  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      required={required}
      htmlFor={id}
      inlineWarning={inlineWarning}
    >
      <input
        ref={ref}
        id={id}
        required={required}
        aria-invalid={error ? "true" : undefined}
        className={`${fieldBase} ${error ? fieldErr : fieldOk} ${className}`}
        {...rest}
      />
    </FieldShell>
  );
});

export interface TextareaProps
  extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea(
    { label, hint, error, required, id, className = "", ...rest },
    ref,
  ) {
    return (
      <FieldShell
        label={label}
        hint={hint}
        error={error}
        required={required}
        htmlFor={id}
      >
        <textarea
          ref={ref}
          id={id}
          required={required}
          aria-invalid={error ? "true" : undefined}
          className={`${fieldBase} ${error ? fieldErr : fieldOk} ${className}`}
          {...rest}
        />
      </FieldShell>
    );
  },
);

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  function Select(
    { label, hint, error, required, id, className = "", children, ...rest },
    ref,
  ) {
    return (
      <FieldShell
        label={label}
        hint={hint}
        error={error}
        required={required}
        htmlFor={id}
      >
        <select
          ref={ref}
          id={id}
          required={required}
          aria-invalid={error ? "true" : undefined}
          className={`${fieldBase} ${error ? fieldErr : fieldOk} ${className}`}
          {...rest}
        >
          {children}
        </select>
      </FieldShell>
    );
  },
);
