// Single screenshot — for recording per-round progress. Captures just one full-page shot of the landing screen.
// Usage: node shot1.mjs <url> <outDir> <label>
import { chromium } from 'playwright';
import fs from 'node:fs';

const [, , url, outDir, label] = process.argv;
if (!url || !outDir || !label) process.exit(1);
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
try {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 25000 });
} catch { /* keep going */ }
await page.waitForTimeout(1500);
// Try to close the first-entry modal
for (const t of ['동의', '확인', '시작', '닫기', 'Accept', 'OK']) {
  try {
    const b = page.locator(`button:has-text("${t}")`).first();
    if (await b.count()) { await b.click({ timeout: 1200 }); await page.waitForTimeout(400); break; }
  } catch { /* ignore */ }
}
await page.screenshot({ path: `${outDir}/${label}.png`, fullPage: true });
await browser.close();
console.log('shot', label);
