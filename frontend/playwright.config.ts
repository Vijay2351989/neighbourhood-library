import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the Phase 7 happy-path test.
 *
 * The test assumes the full stack is already running:
 *   - Backend: `docker compose up` (gRPC + Envoy bridge on :8080, Postgres)
 *   - Frontend: `npm run dev` (Next on :3000)
 *
 * We deliberately don't auto-start the dev server here — the README points
 * the reviewer at the docker-compose flow, and stand-alone Next dev is the
 * correct local idiom. This keeps the e2e config small and predictable.
 *
 * One-time browser setup: `npm run test:e2e:install`
 * Run: `npm run test:e2e`
 */
export default defineConfig({
  testDir: "./e2e",
  // One happy-path test; no need for parallelism, and serial keeps the
  // shared-DB story unambiguous if more tests are added later.
  fullyParallel: false,
  workers: 1,
  // 30s per assertion is plenty for a localhost stack; the borrow flow
  // hits 4 RPCs end-to-end and the picker has a 250ms debounce.
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  // One retry tolerates a single transient gRPC-Web hiccup without
  // failing the whole run. Trace is captured on the retry attempt only,
  // so the success path stays fast.
  retries: 1,
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    // Default targets the dev stack on :3000. The repo's `./test.sh` runs
    // an isolated test stack on :3001 and overrides this via env var.
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    video: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
