import { defineConfig } from "@playwright/test";

// Run against a live `python -m run_comfy serve` in demo mode (see README > Development).
export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  use: {
    baseURL: process.env.RUN_COMFY_URL ?? "http://127.0.0.1:8000",
    viewport: { width: 1440, height: 900 },
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
    screenshot: "only-on-failure",
  },
  outputDir: "test-results",
});
