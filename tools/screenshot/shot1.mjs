// Single screenshot — records one full-page shot of the landing screen (used by build_loop per round).
// Usage: node shot1.mjs <url> <outDir> <label>
// Optional: SHOT_DISMISS="Accept,OK,..." to override the intro-modal dismiss labels.
import { chromium } from 'playwright';
import fs from 'node:fs';

const [, , url, outDir, label] = process.argv;
if (!url || !outDir || !label) process.exit(1);
fs.mkdirSync(outDir, { recursive: true });

// Generic labels for auto-dismissing a first-entry modal. Override via SHOT_DISMISS for
// non-English apps (e.g. SHOT_DISMISS="동의,확인,닫기").
const DISMISS = (process.env.SHOT_DISMISS || 'Accept,OK,Agree,Continue,Got it,Start,Close,Dismiss,Skip')
  .split(',').map(s => s.trim()).filter(Boolean);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
try {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 25000 });
} catch { /* keep going */ }
await page.waitForTimeout(1500);
for (const t of DISMISS) {
  try {
    const b = page.locator(`button:has-text("${t}")`).first();
    if (await b.count()) { await b.click({ timeout: 1200 }); await page.waitForTimeout(400); break; }
  } catch { /* ignore */ }
}
await page.screenshot({ path: `${outDir}/${label}.png`, fullPage: true });
await browser.close();
console.log('shot', label);
