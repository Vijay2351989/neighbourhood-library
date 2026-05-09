"use client";

import type { ReactNode } from "react";

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-sm">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="bg-zinc-50 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500">
      {children}
    </thead>
  );
}

export function TH({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={`border-b border-zinc-200 px-4 py-2.5 font-semibold ${className}`}
    >
      {children}
    </th>
  );
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-zinc-100">{children}</tbody>;
}

export function TR({
  children,
  onClick,
  highlighted,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  highlighted?: boolean;
  className?: string;
}) {
  const clickable = onClick != null;
  return (
    <tr
      onClick={onClick}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
      tabIndex={clickable ? 0 : undefined}
      role={clickable ? "button" : undefined}
      className={`${
        clickable ? "cursor-pointer hover:bg-zinc-50 focus:bg-zinc-50 focus:outline-none" : ""
      } ${highlighted ? "bg-amber-50/70" : ""} ${className}`}
    >
      {children}
    </tr>
  );
}

export function TD({
  children,
  className = "",
  colSpan,
}: {
  children: ReactNode;
  className?: string;
  colSpan?: number;
}) {
  return (
    <td className={`px-4 py-2.5 align-middle text-zinc-800 ${className}`} colSpan={colSpan}>
      {children}
    </td>
  );
}

/** Inline empty-state for table bodies. */
export function EmptyRow({
  message,
  cols,
}: {
  message: string;
  cols: number;
}) {
  return (
    <tr>
      <td
        colSpan={cols}
        className="px-4 py-12 text-center text-sm text-zinc-500"
      >
        {message}
      </td>
    </tr>
  );
}
