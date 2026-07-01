// Multi-view screenshot tool — captures the landing page plus each named tab/view of an SPA.
// Usage: node shoot.mjs <baseURL> <outDir> <label> [tab1,tab2,...]
//   - tab labels can be passed as the 4th arg or via SHOT_TABS env (comma-separated).
//   - intro-modal dismiss labels can be overridden via SHOT_DISMISS (comma-separated).
// With no tabs given, it just captures the landing page. Nothing app-specific is hardcoded.
import { chromium } from 'playwright';
import fs from 'node:fs';

const [, , baseURL, outDir, label, tabsArg] = process.argv;
if (!baseURL || !outDir || !label) {
  console.error('usage: node shoot.mjs <baseURL> <outDir> <label> [tab1,tab2,...]');
  process.exit(1);
}
fs.mkdirSync(outDir, { recursive: true });

const TABS = (tabsArg || process.env.SHOT_TABS || '')
  .split(',').map(s => s.trim()).filter(Boolean);
const DISMISS = (process.env.SHOT_DISMISS || 'Accept,OK,Agree,Continue,Got it,Start,Close,Dismiss,Skip')
  .split(',').map(s => s.trim()).filter(Boolean);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });

async function shot(name) {
  const p = `${outDir}/${label}-${name}.png`;
  await page.screenshot({ path: p, fullPage: true });
  console.log('shot', p);
}

try {
  await page.goto(baseURL, { waitUntil: 'networkidle', timeout: 30000 });
} catch { /* keep going even if networkidle fails */ }
await page.waitForTimeout(1800);
for (const t of DISMISS) {
  try {
    const b = page.locator(`button:has-text("${t}")`).first();
    if (await b.count()) { await b.click({ timeout: 1500 }); await page.waitForTimeout(500); break; }
  } catch { /* ignore */ }
}
await shot('00-landing');

let n = 1;
for (const t of TABS) {
  try {
    const el = page.locator(
      `button:has-text("${t}"), a:has-text("${t}"), [role="tab"]:has-text("${t}"), nav :text("${t}")`
    ).first();
    if (await el.count()) {
      await el.click({ timeout: 4000 });
      await page.waitForTimeout(900);
      await shot(`${String(n).padStart(2, '0')}-${t}`);
      n++;
    }
  } catch (e) { console.log('skip', t, e.message); }
}
await browser.close();
console.log('DONE', label, '→', n - 1, 'tab shots');
