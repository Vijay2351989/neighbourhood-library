"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

type Variant = "info" | "success" | "error" | "warning";

interface Toast {
  id: number;
  message: string;
  variant: Variant;
}

interface ToastContextValue {
  push: (message: string, variant?: Variant) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within <ToastProvider>");
  }
  return ctx;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const push = useCallback((message: string, variant: Variant = "info") => {
    const id = ++idRef.current;
    setToasts((cur) => [...cur, { id, message, variant }]);
    // Auto-dismiss
    setTimeout(() => {
      setToasts((cur) => cur.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      push,
      success: (m) => push(m, "success"),
      error: (m) => push(m, "error"),
      info: (m) => push(m, "info"),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastTray
        toasts={toasts}
        onDismiss={(id) =>
          setToasts((cur) => cur.filter((t) => t.id !== id))
        }
      />
    </ToastContext.Provider>
  );
}

function ToastTray({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  // Render to a fixed top-right tray; no portal needed since the provider sits
  // at the layout root.
  return (
    <div
      className="pointer-events-none fixed inset-x-0 top-4 z-50 flex flex-col items-center gap-2 px-4 sm:items-end sm:right-4 sm:left-auto"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: () => void;
}) {
  const colors: Record<Variant, string> = {
    info: "border-zinc-300 bg-white text-zinc-800",
    success: "border-emerald-300 bg-emerald-50 text-emerald-900",
    error: "border-red-300 bg-red-50 text-red-900",
    warning: "border-amber-300 bg-amber-50 text-amber-900",
  };
  // Tiny entrance animation that respects reduced motion (no animation if
  // motion is reduced — we just show it).
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const t = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(t);
  }, []);

  return (
    <div
      role="status"
      className={`pointer-events-auto w-full max-w-sm rounded-md border px-4 py-3 text-sm shadow-md transition-all motion-reduce:transition-none ${
        colors[toast.variant]
      } ${mounted ? "translate-y-0 opacity-100" : "-translate-y-2 opacity-0"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="leading-snug">{toast.message}</p>
        <button
          aria-label="Dismiss"
          onClick={onDismiss}
          className="text-zinc-400 hover:text-zinc-700"
        >
          ×
        </button>
      </div>
    </div>
  );
}
