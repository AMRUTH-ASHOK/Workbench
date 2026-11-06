import { test, expect } from "@playwright/test";

test("queue keeps unscored cases and scores 8 as good", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /Example unscored account/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /00000001.*Example account/ }).click();
  await expect(
    page.locator(".score-cell.good").filter({ hasText: "AI SAT" }),
  ).toContainText("8");
  await expect(
    page.getByRole("button", { name: "Escalation judgment N/A" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Score history", exact: true })
    .click();
  await expect(
    page.getByRole("img", { name: /score history, 2 evaluations/ }),
  ).toBeVisible();
  await page.getByLabel("Filter cases").selectOption("unscored");
  await expect(page.locator(".case-row")).toHaveCount(1);
});

test("draft edits and review state persist without sending", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: /00000001.*Example account/ }).click();
  await page.getByRole("button", { name: "Drafts (1)", exact: true }).click();
  await page
    .getByLabel("Validation follow-up", { exact: true })
    .fill("Please validate the documented workaround on your next test.");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Draft saved");
  await page
    .getByRole("button", { name: "Mark reviewed", exact: true })
    .click();
  await page.reload();
  await page.getByRole("button", { name: /00000001.*Example account/ }).click();
  await page.getByRole("button", { name: "Drafts (1)", exact: true }).click();
  await expect(
    page.getByLabel("Validation follow-up", { exact: true }),
  ).toHaveValue("Please validate the documented workaround on your next test.");
  await expect(page.locator(".draft-editor .pill")).toHaveText("reviewed");
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toHaveCount(0);
});

test("navigation and compact layout work without horizontal overflow", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Operations", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Operations", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("16:15", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Learning", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Learning desk" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Cases", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: /00000001.*Example account/ }).click();
  await expect(
    page.getByRole("button", { name: "All cases", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "test-results/mobile.png" });
  await page.getByRole("button", { name: "All cases", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your queue" })).toBeVisible();
});
