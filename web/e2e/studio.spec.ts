import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const shots = process.env.SHOTS_DIR;
const snap = async (page: import("@playwright/test").Page, name: string) => {
  if (shots) await page.screenshot({ path: join(shots, `${name}.png`), fullPage: true });
};

test("brief to approved export, end to end in demo mode", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "rc-"));
  const product = join(dir, "product.png");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x7c3aed:s=800x800", "-frames:v", "1", product]);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Batches" })).toBeVisible();
  await page.getByRole("button", { name: "New batch" }).click();

  // 1. brief
  await page.getByRole("button", { name: /UGC talking-head ad/ }).click();
  await page.getByPlaceholder("Spring launch - serum").fill("E2E serum launch");
  await page.getByPlaceholder(/Launch our vitamin C serum/).fill("Launch Glow serum to busy people. Lead with speed.");
  await page.getByLabel("Brand kit").selectOption("new");
  await page.getByPlaceholder("Brand name").fill("Glow");
  await page.getByPlaceholder("Product (one line)").fill("vitamin C serum");
  await page.getByTestId("upload-product").setInputFiles(product);
  await expect(page.getByText("product.png")).toBeVisible();
  await page.getByRole("button", { name: "9:16" }).click(); // keep only 1:1 to render fast
  await snap(page, "01-new-batch");
  await page.getByRole("button", { name: "Create and write plan" }).click();

  // 2. plan (written by the demo writer), edit a hook, save
  await expect(page.getByRole("textbox", { name: "Hooks 1" })).toBeVisible();
  await page.getByRole("textbox", { name: "Hooks 1" }).fill("Your skin, but faster");
  await snap(page, "02-plan");
  await page.getByRole("button", { name: "Save plan" }).click();

  // 3. matrix: narrow to 2 hooks x 1 script x 1 cta x 2 presenters x 1 voice
  await expect(page.getByText("How to combine")).toBeVisible();
  const uncheck = async (axis: string, keep: number) => {
    const card = page.locator("div.rounded-xl", { has: page.getByText(axis, { exact: true }) }).last();
    const boxes = card.getByRole("checkbox");
    const n = await boxes.count();
    for (let i = keep; i < n; i++) await boxes.nth(i).uncheck();
  };
  await uncheck("Hook", 2);
  await uncheck("Script", 1);
  await uncheck("CTA", 1);
  await uncheck("Voice", 1);
  await page.getByRole("button", { name: "Every combination" }).click();
  await expect(page.getByRole("button", { name: "Render 4 variants" })).toBeEnabled();
  await expect(page.getByText(/Stage reuse saves/)).toBeVisible();
  await snap(page, "03-matrix");
  await page.getByRole("button", { name: "Render 4 variants" }).click();

  // 4. watch it render, then review
  await expect(page.getByText("4/4", { exact: true })).toBeVisible({ timeout: 150_000 });
  await expect(page.locator("[data-testid^=variant-] video").first()).toBeVisible();
  await snap(page, "04-variants");
  const first = page.locator("[data-testid^=variant-]").first();
  await first.getByRole("button", { name: /^Approve/ }).click();
  await expect(first.getByRole("button", { name: /^Approve/ })).toHaveText(/Approved/);

  // open the player
  await first.locator("video").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await snap(page, "05-player");
  await page.getByRole("button", { name: "Close" }).click();

  // 5. export the approved variant
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Export 1 approved/ }).click();
  const d = await download;
  expect(d.suggestedFilename()).toMatch(/^e2e-serum-launch_b\d+_.*\.zip$/);
});

test("brand kit editor and library", async ({ page }) => {
  await page.goto("/brands");
  await page.getByRole("button", { name: "New kit" }).click();
  const brand = `Acme Coffee ${Date.now()}`;
  await page.getByLabel("Brand name").fill(brand);
  await page.getByLabel("Banned words & claims").fill("cure\nguaranteed");
  await page.getByRole("button", { name: "Save brand kit" }).click();
  await expect(page.getByText("Brand kit saved")).toBeVisible();
  await expect(page.getByRole("button", { name: brand })).toBeVisible();
  await snap(page, "06-brand");
  await page.goto("/workflows");
  await expect(page.getByText("wan22_s2v")).toBeVisible();
  await snap(page, "07-workflows");
});
