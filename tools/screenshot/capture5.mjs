// Capture 5 screenshots — v4.2 visual regression check
// Usage: node capture5.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = 'http://localhost:4173';
const DISC_KEY = 'ftapp_v1_disclaimer_ok';   // see constants.js
const outDir = process.argv[2] || '.';
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage();
await page.setViewportSize({ width: 1440, height: 900 });

// Set localStorage to an empty state (incomes=[], expenses=[]) but mark the modal as accepted
async function resetStorageWithDisclaimer() {
  await page.evaluate((dk) => {
    localStorage.clear();
    // Mark the disclaimer modal as accepted so it does not cover the screen
    localStorage.setItem(dk, '1');
  }, DISC_KEY);
}

async function shot(filename) {
  await page.screenshot({ path: `${outDir}/${filename}`, fullPage: false });
  console.log('captured:', filename);
}

async function fullshot(filename) {
  await page.screenshot({ path: `${outDir}/${filename}`, fullPage: true });
  console.log('captured (full):', filename);
}

// ── Shot 1: Dashboard initial (top) — verify 3-tier sticky stack ──
console.log('Shot 1: Dashboard initial...');
await page.goto(BASE, { waitUntil: 'networkidle', timeout: 20000 });
await resetStorageWithDisclaimer();
await page.reload({ waitUntil: 'networkidle', timeout: 20000 });
await page.waitForTimeout(1000);
await page.evaluate(() => window.scrollTo(0, 0));
await shot('shot1_dashboard_top.png');

// ── Shot 2: Dashboard scrolled — verify 3-tier header stays pinned ──
console.log('Shot 2: Dashboard scrolled...');
await page.evaluate(() => window.scrollTo(0, 500));
await page.waitForTimeout(400);
await shot('shot2_dashboard_scrolled.png');

// ── Shot 3: Dashboard "next steps" guide card ──
// incomes=[], expenses=[] → only a single income card is shown
console.log('Shot 3: next-steps guide card...');
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await fullshot('shot3_dashboard_guide_fullpage.png');

// ── Shot 4: Income step ──
console.log('Shot 4: Income step...');
await page.evaluate(() => window.scrollTo(0, 0));
let clicked = false;
for (const sel of [
  'button:has-text("수입 입력")',
  'button:has-text("수입")',
  '[data-step="income"]',
]) {
  try {
    const btn = page.locator(sel).first();
    if ((await btn.count()) > 0) {
      await btn.click();
      clicked = true;
      break;
    }
  } catch {}
}
if (!clicked) {
  // fallback: the second button in the StepRail nav
  try { await page.locator('nav button').nth(1).click(); } catch {}
}
await page.waitForTimeout(700);
await page.evaluate(() => window.scrollTo(0, 0));
await shot('shot4_income_step.png');

// ── Shot 5: TaxSim step ──
console.log('Shot 5: TaxSim step...');
await page.evaluate(() => window.scrollTo(0, 0));
let clicked2 = false;
for (const sel of [
  'button:has-text("세금 시뮬")',
  'button:has-text("세금")',
  '[data-step="tax"]',
]) {
  try {
    const btn = page.locator(sel).first();
    if ((await btn.count()) > 0) {
      await btn.click();
      clicked2 = true;
      break;
    }
  } catch {}
}
if (!clicked2) {
  // fallback: the fourth button in the StepRail
  try { await page.locator('nav button').nth(3).click(); } catch {}
}
await page.waitForTimeout(700);
await page.evaluate(() => window.scrollTo(0, 0));
await shot('shot5_taxsim_step.png');

await browser.close();
console.log('\nCaptured 5 shots!');
