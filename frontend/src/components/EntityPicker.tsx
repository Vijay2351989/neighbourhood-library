"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";

export interface EntityPickerProps<T> {
  label: string;
  placeholder: string;
  /** Renders a result row's display content. */
  renderItem: (item: T) => ReactNode;
  /** Stable key for an item. */
  keyOf: (item: T) => string;
  /** Returns the chosen item's display label after selection. */
  labelOf: (item: T) => string;
  /** Async fetch — debounced calls only. */
  search: (query: string) => Promise<T[]>;
  /** Optional filter; e.g. only books with available copies. */
  isSelectable?: (item: T) => boolean;
  unselectableHint?: string;
  selected: T | null;
  onSelect: (item: T | null) => void;
}

export function EntityPicker<T>({
  label,
  placeholder,
  renderItem,
  keyOf,
  labelOf,
  search,
  isSelectable,
  unselectableHint,
  selected,
  onSelect,
}: EntityPickerProps<T>) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Click outside to close.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, []);

  // Debounced fetch on query change.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!open) return;
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await search(query);
        setItems(r);
      } catch {
        setItems([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, open]);

  if (selected) {
    return (
      <div>
        <label className="text-sm font-medium text-zinc-700">{label}</label>
        <div className="mt-1.5 flex items-center justify-between rounded-md border border-blue-300 bg-blue-50 px-3 py-2">
          <span className="text-sm font-medium text-blue-900">
            {labelOf(selected)}
          </span>
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="text-xs font-medium text-blue-700 hover:underline"
          >
            Change
          </button>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <Input
        label={label}
        placeholder={placeholder}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      {open ? (
        <div className="absolute left-0 right-0 z-10 mt-1 max-h-72 overflow-auto rounded-md border border-zinc-200 bg-white shadow-lg">
          {loading ? (
            <div className="space-y-2 px-3 py-3">
              <Skeleton width="60%" />
              <Skeleton width="40%" />
              <Skeleton width="50%" />
            </div>
          ) : items.length === 0 ? (
            <p className="px-3 py-3 text-sm text-zinc-500">
              {query.trim() ? "No matches." : "Start typing to search."}
            </p>
          ) : (
            <ul role="listbox">
              {items.map((it) => {
                const selectable = isSelectable ? isSelectable(it) : true;
                return (
                  <li key={keyOf(it)}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={false}
                      disabled={!selectable}
                      onClick={() => {
                        if (!selectable) return;
                        onSelect(it);
                        setOpen(false);
                        setQuery("");
                      }}
                      className={`block w-full px-3 py-2 text-left text-sm transition-colors ${
                        selectable
                          ? "hover:bg-zinc-50"
                          : "cursor-not-allowed text-zinc-400"
                      }`}
                    >
                      {renderItem(it)}
                      {!selectable && unselectableHint ? (
                        <span className="ml-2 text-xs italic text-zinc-400">
                          {unselectableHint}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
