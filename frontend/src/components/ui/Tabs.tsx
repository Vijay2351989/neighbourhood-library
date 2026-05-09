"use client";

import type { ReactNode } from "react";

export interface TabsProps<T extends string> {
  value: T;
  onChange: (next: T) => void;
  options: Array<{ value: T; label: string; badge?: ReactNode }>;
}

export function Tabs<T extends string>({
  value,
  onChange,
  options,
}: TabsProps<T>) {
  return (
    <div
      role="tablist"
      aria-label="View filter"
      className="flex gap-1 border-b border-zinc-200"
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={`relative -mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
              active
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-zinc-600 hover:text-zinc-900"
            }`}
          >
            {opt.label}
            {opt.badge != null ? (
              <span
                className={`rounded-full px-1.5 py-0.5 text-xs font-medium ${
                  active
                    ? "bg-blue-100 text-blue-700"
                    : "bg-zinc-100 text-zinc-600"
                }`}
              >
                {opt.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export interface ChipFilterProps<T extends string> {
  value: T;
  onChange: (next: T) => void;
  options: Array<{ value: T; label: string }>;
}

export function ChipFilter<T extends string>({
  value,
  onChange,
  options,
}: ChipFilterProps<T>) {
  return (
    <div role="group" className="flex flex-wrap items-center gap-1.5">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
              active
                ? "border-blue-600 bg-blue-600 text-white"
                : "border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-50"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
