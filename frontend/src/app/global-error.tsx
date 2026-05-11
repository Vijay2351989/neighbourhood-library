"use client";

import { useEffect } from "react";

// Last-resort boundary that fires when the root layout itself crashes.
// Because it replaces the root layout, it must render its own <html> and
// <body>. Keep this dependency-free and styled inline — if the root layout
// failed, Providers / TopNav / Tailwind may also be unavailable.

export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error("GlobalError boundary caught:", error, {
      digest: error.digest,
    });
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily:
            "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
          backgroundColor: "#fafafa",
          color: "#18181b",
        }}
      >
        <div
          style={{
            maxWidth: "32rem",
            padding: "2rem",
            textAlign: "center",
          }}
        >
          <h1 style={{ fontSize: "1.5rem", fontWeight: 600, margin: 0 }}>
            The application crashed
          </h1>
          <p
            style={{
              marginTop: "0.75rem",
              color: "#52525b",
              fontSize: "0.95rem",
            }}
          >
            Something went wrong loading the app shell. Try again, or reload
            the page.
          </p>
          <div
            style={{
              marginTop: "1.5rem",
              display: "flex",
              gap: "0.5rem",
              justifyContent: "center",
            }}
          >
            <button
              type="button"
              onClick={() => unstable_retry()}
              style={{
                padding: "0.5rem 1rem",
                borderRadius: "0.375rem",
                border: "1px solid #18181b",
                background: "#18181b",
                color: "white",
                fontSize: "0.875rem",
                cursor: "pointer",
              }}
            >
              Try again
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                padding: "0.5rem 1rem",
                borderRadius: "0.375rem",
                border: "1px solid #d4d4d8",
                background: "white",
                color: "#18181b",
                fontSize: "0.875rem",
                cursor: "pointer",
              }}
            >
              Reload page
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
