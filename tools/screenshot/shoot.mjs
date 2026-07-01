// Screen screenshot tool — captures the landing page plus each tab/view of a single SPA.
// Usage: node shoot.mjs <baseURL> <outDir> <label>
import { chromium } from 'playwright';
import fs from 'node:fs';

const [, , baseURL, outDir, label] = process.argv;
if (!baseURL || !outDir || !label) {
  console.error('usage: node shoot.mjs <baseURL> <outDir> <label>');
  process.exit(1);
}
fs.mkdirSync(outDir, { recursive: true });

// Candidate tab/sidebar item labels (tried across both the old and new layouts)
const TABS = ['대시보드', '소득', '경비', '세금', '데이터', '설정'];

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
// If a first-entry modal (disclaimer consent, etc.) appears, try to close it
for (const t of ['동의', '확인', '시작', '닫기', 'Accept', 'OK']) {
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
