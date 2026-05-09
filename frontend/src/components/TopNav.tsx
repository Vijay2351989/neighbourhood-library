"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Dashboard", match: (p: string) => p === "/" },
  { href: "/books", label: "Books", match: (p: string) => p.startsWith("/books") },
  {
    href: "/members",
    label: "Members",
    match: (p: string) => p.startsWith("/members"),
  },
  { href: "/loans", label: "Loans", match: (p: string) => p.startsWith("/loans") },
];

export function TopNav() {
  const pathname = usePathname() ?? "/";
  return (
    <header className="sticky top-0 z-30 border-b border-zinc-200 bg-white/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-6">
        <Link
          href="/"
          className="flex items-center gap-2 text-sm font-semibold text-zinc-900"
        >
          <span
            aria-hidden
            className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-blue-600 text-white"
          >
            <svg
              viewBox="0 0 16 16"
              className="h-3.5 w-3.5"
              fill="currentColor"
              aria-hidden
            >
              <path d="M2 2h5a2 2 0 012 2v9.5a.5.5 0 01-.8.4A4 4 0 006.5 13H2V2zm12 0H9a2 2 0 00-2 2v9.5a.5.5 0 00.8.4A4 4 0 019.5 13H14V2z" />
            </svg>
          </span>
          Neighborhood Library
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {links.map((l) => {
            const active = l.match(pathname);
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                  active
                    ? "bg-zinc-100 text-zinc-900"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto text-xs text-zinc-500">Staff workspace</div>
      </div>
    </header>
  );
}
