import { expect, test, type Page } from "@playwright/test";

const SHOTS = process.env.E2E_SHOTS || "e2e-screenshots";
const email = `e2e-${Date.now()}@example.com`;
const password = "E2eStr0ngPassw0rd";

async function noErrors(page: Page, errors: string[]) {
  await page.waitForLoadState("networkidle");
  expect(errors, errors.join("\n")).toEqual([]);
}

test.describe.serial("Investment Intelligence end-to-end", () => {
  let page: Page;
  const errors: string[] = [];

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("401")) errors.push(`console: ${m.text()}`); });
  });

  test("register with demo data", async () => {
    await page.goto("/");
    await page.getByText("No account yet? Create one").click();
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Display name").fill("E2E Tester");
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByRole("heading", { name: "Executive dashboard" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Sample mode.")).toBeVisible();
    await expect(page.getByText("Demo paper portfolio (SAMPLE) vs SMBR")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/01-dashboard.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("agent answers in deterministic mode with tool calls", async () => {
    await page.getByRole("link", { name: "AI Agent" }).click();
    await page.getByLabel("Message").fill("compare NWND vs LITW");
    await page.keyboard.press("Enter");
    await expect(page.getByText("Deterministic mode", { exact: false }).first()).toBeVisible();
    await expect(page.locator("table").first()).toBeVisible();
    await page.getByText(/tool call\(s\)/).first().click();
    await expect(page.getByText("compare_securities", { exact: false })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/02-agent.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("markets overview and explorer", async () => {
    await page.getByRole("link", { name: "Markets" }).click();
    await expect(page.getByRole("heading", { name: "Indices" })).toBeVisible();
    await expect(page.getByText("SMPL500").first()).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/03-markets.png`, fullPage: true });
    await page.getByRole("button", { name: "Securities explorer" }).click();
    await expect(page.getByText("NWND").first()).toBeVisible();
    await noErrors(page, errors);
  });

  test("discovery screen with explanations", async () => {
    await page.getByRole("link", { name: "Discovery" }).click();
    await expect(page.getByText(/candidates$/).first()).toBeVisible();
    await page.locator("button", { hasText: "#1" }).first().click();
    await expect(page.getByText("5. What would invalidate the thesis")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/04-discovery.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("company workspace: overview, financials, valuation", async () => {
    await page.goto("/research/NWND");
    await expect(page.getByRole("heading", { name: /NWND/ })).toBeVisible();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${SHOTS}/05-security.png`, fullPage: true });
    await page.getByRole("button", { name: "Financials" }).click();
    await expect(page.getByText("Ratios & quality (derived in code)")).toBeVisible();
    await page.getByRole("button", { name: "Valuation" }).click();
    await expect(page.getByText("Sensitivity: value per share")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/06-valuation.png`, fullPage: true });
    await page.getByLabel("Discount rate").fill("0.12");
    await expect(page.getByText("user override")).toBeVisible();
    await noErrors(page, errors);
  });

  test("generate research report", async () => {
    await page.goto("/research/GRPH");
    await page.getByRole("button", { name: "Long-term report" }).click();
    await expect(page.getByRole("heading", { name: "Executive summary" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("heading", { name: "Counterarguments (bear case)" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sources" })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/07-report.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("portfolio: performance, transaction, risk", async () => {
    await page.getByRole("link", { name: "Portfolios" }).click();
    await page.getByText("Demo paper portfolio (SAMPLE)").first().click();
    await expect(page.getByText("Cumulative time-weighted return")).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: `${SHOTS}/08-portfolio.png`, fullPage: true });
    await page.getByRole("button", { name: /^Transactions/ }).click();
    await page.getByLabel("Type").selectOption("deposit");
    await page.getByLabel("Amount").fill("2500");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.locator("td", { hasText: "2,500.00" }).first()).toBeVisible();
    await page.getByRole("button", { name: "Risk" }).click();
    await expect(page.getByText("Limit checks")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/09-risk.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("short-term setups page", async () => {
    await page.getByRole("link", { name: "Short-term" }).click();
    await expect(page.getByText(/Scanned \d+ instruments/)).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: `${SHOTS}/10-trading.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("agent control: worker verified, queue research cycle", async () => {
    await page.getByRole("link", { name: "Agent Control" }).click();
    await expect(page.getByText("alive").first()).toBeVisible();
    await page.getByRole("button", { name: "Jobs" }).click();
    await page.getByRole("button", { name: "Queue" }).click();
    await expect(page.getByText("research_cycle").first()).toBeVisible();
    await expect(page.locator("text=succeeded").first()).toBeVisible({ timeout: 110_000 });
    await page.screenshot({ path: `${SHOTS}/11-control.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("learning lab, alerts, settings, light theme", async () => {
    await page.getByRole("link", { name: "Learning Lab" }).click();
    await expect(page.getByText("Calibration")).toBeVisible();
    await page.getByRole("link", { name: "Alerts" }).click();
    await expect(page.getByText("Create alert")).toBeVisible();
    await page.getByRole("link", { name: "Settings" }).click();
    await expect(page.getByText("SEC EDGAR (official filings, XBRL company facts)")).toBeVisible();
    await page.getByLabel("Toggle theme").click();
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page.getByRole("heading", { name: "Executive dashboard" })).toBeVisible();
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${SHOTS}/12-dashboard-light.png`, fullPage: true });
    await noErrors(page, errors);
  });

  test("mobile layout", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, storageState: await page.context().storageState() });
    const m = await ctx.newPage();
    await m.goto("/");
    await expect(m.getByRole("heading", { name: "Executive dashboard" })).toBeVisible();
    await m.getByLabel("Open menu").click();
    await expect(m.getByRole("link", { name: "Portfolios" })).toBeVisible();
    await m.waitForTimeout(400);
    await m.screenshot({ path: `${SHOTS}/13-mobile.png`, fullPage: false });
    const overflow = await m.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(overflow).toBe(false);
    await ctx.close();
  });
});
