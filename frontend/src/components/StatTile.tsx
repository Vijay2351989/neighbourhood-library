import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/Skeleton";

export function StatTile({
  label,
  value,
  hint,
  loading,
  emphasize,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  loading?: boolean;
  /** Use amber theming for fines / overdue tiles. */
  emphasize?: "warning";
}) {
  return (
    <div
      className={`rounded-lg border bg-white p-4 shadow-sm ${
        emphasize === "warning" ? "border-amber-300" : "border-zinc-200"
      }`}
    >
      <p
        className={`text-xs font-medium uppercase tracking-wide ${
          emphasize === "warning" ? "text-amber-700" : "text-zinc-500"
        }`}
      >
        {label}
      </p>
      <div className="mt-2 text-2xl font-semibold text-zinc-900">
        {loading ? <Skeleton width={80} height={28} /> : value}
      </div>
      {hint ? (
        <p className="mt-1 text-xs text-zinc-500">{hint}</p>
      ) : null}
    </div>
  );
}
