import type { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-zinc-300 bg-white px-6 py-16 text-center">
      <h2 className="text-base font-semibold text-zinc-900">{title}</h2>
      {description ? (
        <p className="mx-auto mt-1 max-w-md text-sm text-zinc-600">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}
