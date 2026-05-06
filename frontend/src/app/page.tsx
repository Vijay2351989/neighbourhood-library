// Phase 1 placeholder home page. The real dashboard ships in Phase 6
// (see docs/design/04-frontend.md and docs/phases/phase-6-frontend-mvp.md).
export default function Home() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center px-6 text-center">
      <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
        Neighborhood Library
      </h1>
      <p className="mt-4 max-w-md text-base text-zinc-600 dark:text-zinc-400">
        Phase 1 scaffold — full UI lands in Phase 6.
      </p>
    </main>
  );
}
