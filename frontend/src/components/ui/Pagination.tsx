"use client";

import { Button } from "./Button";

export interface PaginationProps {
  /** zero-indexed offset */
  offset: number;
  pageSize: number;
  totalCount: number;
  onChange: (nextOffset: number) => void;
}

export function Pagination({
  offset,
  pageSize,
  totalCount,
  onChange,
}: PaginationProps) {
  const start = totalCount === 0 ? 0 : offset + 1;
  const end = Math.min(offset + pageSize, totalCount);
  const canPrev = offset > 0;
  const canNext = offset + pageSize < totalCount;

  return (
    <div className="flex items-center justify-between px-1 py-3 text-sm text-zinc-600">
      <p>
        {totalCount === 0
          ? "No results"
          : `Showing ${start}–${end} of ${totalCount.toLocaleString()}`}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={!canPrev}
          onClick={() => onChange(Math.max(0, offset - pageSize))}
        >
          Previous
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={!canNext}
          onClick={() => onChange(offset + pageSize)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
