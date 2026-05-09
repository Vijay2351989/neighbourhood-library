"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ui/Toast";

/**
 * Client-side providers. Lives at the layout level so server components
 * can wrap children without losing SSR. The QueryClient is created lazily
 * (per-render) per TanStack's React 19 / Next.js guidance.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Library data isn't real-time-critical; a 30s freshness window
            // makes the dashboard feel snappy when navigating between pages.
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}
