import { expect, test } from "@playwright/test";

/**
 * Phase 7 — single happy-path e2e.
 *
 * Mirrors the Phase 6 acceptance demo: create a book, create a member,
 * borrow, verify on the member's Active tab, return, verify on Returned,
 * confirm the book's available count rebounds. Each run uses unique
 * timestamped strings so re-runs don't collide on the unique-email
 * constraint or pollute prior data.
 *
 * Prerequisites (must already be running locally):
 *   - `docker compose up`        — backend gRPC + Envoy bridge
 *   - `npm run dev` (frontend/)  — Next dev server on :3000
 *   - DEMO_MODE off (or empty DB) — this test creates its own data
 *
 * Run: `npm run test:e2e`  (after `npm run test:e2e:install` once)
 */

test.describe("happy path: create → borrow → return", () => {
  test("staff can borrow and return a book end-to-end", async ({ page }) => {
    // Unique-per-run identifiers. The backend search is prefix-only
    // (`LIKE 'foo%'` against the lower(name)/lower(title) functional
    // indexes), so the unique token MUST be at the *start* of each
    // searchable field — otherwise the picker's prefix search misses
    // the just-created records.
    const stamp = Date.now();
    const bookTitle = `${stamp} E2E Test Book`;
    const bookAuthor = `${stamp} E2E Author`;
    const memberName = `${stamp} E2E Tester`;
    const memberEmail = `${stamp}-e2e@example.com`;

    // -----------------------------------------------------------------
    // 1. Dashboard renders the five tiles.
    // -----------------------------------------------------------------
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Dashboard" }),
    ).toBeVisible();

    const counts = page.getByLabel("Counts");
    await expect(counts.getByText("Total books", { exact: true })).toBeVisible();
    await expect(
      counts.getByText("Total members", { exact: true }),
    ).toBeVisible();
    await expect(counts.getByText("Active loans", { exact: true })).toBeVisible();
    await expect(counts.getByText("Overdue", { exact: true })).toBeVisible();
    await expect(
      counts.getByText("Outstanding fines", { exact: true }),
    ).toBeVisible();

    // -----------------------------------------------------------------
    // 2. Create a book. The form lives at /books/new and redirects to
    //    /books/[id] on success — we capture that id from the URL for the
    //    final inventory assertion.
    // -----------------------------------------------------------------
    await page.goto("/books/new");
    await expect(
      page.getByRole("heading", { name: "New book" }),
    ).toBeVisible();

    // Note: required-field labels render as e.g. "Title*" (asterisk via a
    // sibling span inside the <label>). The asterisk becomes part of the
    // accessible name, so we omit `exact: true` and let prefix matching find
    // the field. Optional fields render without an asterisk.
    await page.getByLabel("Title").fill(bookTitle);
    await page.getByLabel("Author").fill(bookAuthor);
    // Default copies=1; explicitly set to 2 so we can prove availability
    // returns to 2 after the return step.
    await page.getByLabel("Number of copies").fill("2");
    await page.getByRole("button", { name: "Create book" }).click();

    await page.waitForURL(/\/books\/\d+$/);
    const bookUrl = page.url();
    const bookId = bookUrl.match(/\/books\/(\d+)$/)?.[1];
    expect(bookId, "book id should be in redirect URL").toBeTruthy();
    await expect(
      page.getByRole("heading", { name: bookTitle }),
    ).toBeVisible();

    // -----------------------------------------------------------------
    // 3. Create a member. Same pattern — redirect to /members/[id].
    // -----------------------------------------------------------------
    await page.goto("/members/new");
    await expect(
      page.getByRole("heading", { name: "New member" }),
    ).toBeVisible();

    await page.getByLabel("Name").fill(memberName);
    await page.getByLabel("Email").fill(memberEmail);
    await page.getByRole("button", { name: "Create member" }).click();

    await page.waitForURL(/\/members\/\d+$/);
    const memberUrl = page.url();
    const memberId = memberUrl.match(/\/members\/(\d+)$/)?.[1];
    expect(memberId, "member id should be in redirect URL").toBeTruthy();
    await expect(
      page.getByRole("heading", { name: memberName }),
    ).toBeVisible();

    // -----------------------------------------------------------------
    // 4. Borrow. The picker is a debounced async search — type the unique
    //    suffix from our new records so the dropdown narrows to one row,
    //    then click it. BorrowDialog asks for one more confirmation.
    // -----------------------------------------------------------------
    await page.goto("/loans/new");
    await expect(
      page.getByRole("heading", { name: "New loan" }),
    ).toBeVisible();

    // Member picker: search by the unique stamp, click the first option.
    await page
      .getByPlaceholder("Search members by name or email...")
      .fill(String(stamp));
    const memberOption = page
      .getByRole("listbox")
      .getByRole("option")
      .filter({ hasText: memberName })
      .first();
    await memberOption.waitFor({ state: "visible" });
    await memberOption.click();

    // Book picker: same drill.
    await page
      .getByPlaceholder("Search books by title or author...")
      .fill(String(stamp));
    const bookOption = page
      .getByRole("listbox")
      .getByRole("option")
      .filter({ hasText: bookTitle })
      .first();
    await bookOption.waitFor({ state: "visible" });
    await bookOption.click();

    await page.getByRole("button", { name: "Review borrow" }).click();

    // BorrowDialog (role=dialog, title "Confirm borrow"). The Confirm
    // button uses the same label, so scope it to the dialog to disambiguate.
    const borrowDialog = page.getByRole("dialog", { name: "Confirm borrow" });
    await expect(borrowDialog).toBeVisible();
    await borrowDialog
      .getByRole("button", { name: "Confirm borrow" })
      .click();

    // After success the form redirects to the member detail page.
    await page.waitForURL(new RegExp(`/members/${memberId}$`));

    // -----------------------------------------------------------------
    // 5. Verify the loan shows up in the member's Active tab.
    // -----------------------------------------------------------------
    await expect(page.getByRole("tab", { name: "Active" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const activeRow = page.getByRole("row").filter({ hasText: bookTitle });
    await expect(activeRow).toBeVisible();
    await expect(activeRow.getByRole("button", { name: "Return" })).toBeVisible();

    // -----------------------------------------------------------------
    // 6. Return the loan via the row's Return button + confirmation.
    // -----------------------------------------------------------------
    await activeRow.getByRole("button", { name: "Return" }).click();

    const returnDialog = page.getByRole("dialog", { name: "Return this loan?" });
    await expect(returnDialog).toBeVisible();
    await returnDialog.getByRole("button", { name: "Return" }).click();
    await expect(returnDialog).toBeHidden();

    // -----------------------------------------------------------------
    // 7. Active tab should no longer show the row; Returned tab should.
    // -----------------------------------------------------------------
    await expect(
      page.getByRole("row").filter({ hasText: bookTitle }),
    ).toHaveCount(0);

    await page.getByRole("tab", { name: "Returned" }).click();
    await expect(
      page.getByRole("row").filter({ hasText: bookTitle }).first(),
    ).toBeVisible();

    // -----------------------------------------------------------------
    // 8. Book inventory should be back to 2 of 2 available.
    // -----------------------------------------------------------------
    await page.goto(`/books/${bookId}`);
    await expect(
      page.getByRole("heading", { name: bookTitle }),
    ).toBeVisible();
    // The inventory card renders e.g. "2 / 2 available".
    await expect(page.getByText(/\b2\b\s*\/\s*2 available/)).toBeVisible();
  });
});
