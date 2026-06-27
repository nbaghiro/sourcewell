import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const OUT = process.env.SHOTS || new URL("./screens", import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });

const errors = [];
let shotN = 0;
const log = (...a) => console.log("•", ...a);

async function shot(page, name) {
  shotN++;
  const file = `${OUT}/${String(shotN).padStart(2, "0")}-${name}.png`;
  await page.screenshot({ path: file });
  log("shot", file);
}

// resilient step: never abort the whole tour
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    log(`STEP FAILED [${name}]:`, e.message.split("\n")[0]);
  }
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();

page.on("console", (m) => { if (m.type() === "error") errors.push(`[console] ${m.text()}`); });
page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));

const BASE = "http://localhost:8900";
const go = (p) => page.goto(BASE + p, { waitUntil: "networkidle" });

// ---- login ----
await step("login", async () => {
  await go("/login");
  await shot(page, "login");
  await page.fill("#email", "demo@sourcewell.ai");
  await page.fill("#password", "testpass");
  await page.getByRole("button", { name: /continue with email/i }).click();
  // authed → redirect to "/"; wait for the sidebar nav
  await page.getByRole("link", { name: "Campaigns" }).first().waitFor({ timeout: 20000 });
  log("logged in ✓");
});

await step("home", async () => { await go("/"); await page.waitForTimeout(900); await shot(page, "home"); });

await step("inbox", async () => {
  await go("/inbox"); await page.waitForTimeout(1000); await shot(page, "inbox");
  // click a filter chip if present (Awaiting approval / Awaiting reply)
  const chip = page.getByRole("button", { name: /awaiting approval/i }).first();
  if (await chip.count()) { await chip.click(); await page.waitForTimeout(700); await shot(page, "inbox-approvals"); }
});

await step("people", async () => {
  await go("/people"); await page.waitForTimeout(900); await shot(page, "people");
  const find = page.getByRole("button", { name: /find people/i }).first();
  if (await find.count()) { await find.click(); await page.waitForTimeout(1200); await shot(page, "people-find"); }
});

await step("campaigns-list", async () => { await go("/campaigns"); await page.waitForTimeout(900); await shot(page, "campaigns-list"); });

// ---- campaign console (open the first campaign) ----
await step("campaign-console", async () => {
  await go("/campaigns");
  await page.waitForTimeout(600);
  const row = page.locator("table tbody tr").first();
  await row.click();
  await page.waitForTimeout(1400);
  await shot(page, "console");

  // autonomy: click Autopilot
  await step("autonomy", async () => {
    await page.getByRole("button", { name: "Autopilot" }).first().click();
    await page.waitForTimeout(700);
    await shot(page, "console-autopilot");
  });

  // the ⋯ menu (dropdown fix) — last header button (More)
  await step("dots-menu", async () => {
    const dots = page.locator("header button, .flex button").filter({ has: page.locator("svg") });
    // try the button right after Setup
    await page.locator('button:has-text("Setup")').first().waitFor({ timeout: 4000 });
    const more = page.locator('button:has-text("Setup")').first().locator("xpath=following-sibling::button[1]");
    await more.click();
    await page.waitForTimeout(500);
    await shot(page, "console-dots-menu");
    await page.keyboard.press("Escape");
  });

  // Setup drawer
  await step("setup", async () => {
    await page.locator('button:has-text("Setup")').first().click();
    await page.waitForTimeout(1100);
    await shot(page, "console-setup");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  });

  // Activity drawer
  await step("activity", async () => {
    await page.locator('button:has-text("Activity")').first().click();
    await page.waitForTimeout(1200);
    await shot(page, "console-activity");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  });

  // candidate peek — click first candidate name button in the table
  await step("candidate-peek", async () => {
    await page.locator("table tbody tr td button").first().click();
    await page.waitForTimeout(1100);
    await shot(page, "console-candidate-peek");
    await page.keyboard.press("Escape");
  });
});

await step("pipeline", async () => { await go("/pipeline"); await page.waitForTimeout(1000); await shot(page, "pipeline"); });

await step("settings", async () => {
  await go("/settings"); await page.waitForTimeout(900); await shot(page, "settings");
  for (const tab of ["Reporting", "Audit", "Members"]) {
    const t = page.getByRole("button", { name: tab }).first();
    if (await t.count()) { await t.click(); await page.waitForTimeout(700); await shot(page, "settings-" + tab.toLowerCase()); }
  }
});

await step("campaign-create", async () => { await go("/campaigns/new"); await page.waitForTimeout(1000); await shot(page, "campaign-create"); });

await browser.close();

console.log("\n========== CONSOLE/PAGE ERRORS (" + errors.length + ") ==========");
for (const e of [...new Set(errors)]) console.log("  ✗", e);
console.log("========== " + shotN + " screenshots written ==========");
