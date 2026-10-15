import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8000",
    screenshot: "only-on-failure",
    launchOptions: { executablePath: process.env.PW_CHROMIUM || "/opt/pw-browsers/chromium" },
    viewport: { width: 1440, height: 900 },
  },
});
