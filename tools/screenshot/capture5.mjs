// 5장 스크린샷 캡처 — v4.2 시각 회귀 검증
// 사용: node capture5.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = 'http://localhost:4173';
const DISC_KEY = 'ftapp_v1_disclaimer_ok';   // constants.js에서 확인
const outDir = process.argv[2] || '.';
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage();
await page.setViewportSize({ width: 1440, height: 900 });

// localStorage를 빈 상태(incomes=[], expenses=[])로 세팅하되 모달은 동의 처리
async function resetStorageWithDisclaimer() {
  await page.evaluate((dk) => {
    localStorage.clear();
    // 면책 모달은 동의 완료로 처리해 화면을 가리지 않게
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

// ── Shot 1: Dashboard 초기 (상단) — 3단 sticky 스택 확인 ──
console.log('Shot 1: Dashboard 초기...');
await page.goto(BASE, { waitUntil: 'networkidle', timeout: 20000 });
await resetStorageWithDisclaimer();
await page.reload({ waitUntil: 'networkidle', timeout: 20000 });
await page.waitForTimeout(1000);
await page.evaluate(() => window.scrollTo(0, 0));
await shot('shot1_dashboard_top.png');

// ── Shot 2: Dashboard 스크롤 — 3단 헤더 고정 확인 ──
console.log('Shot 2: Dashboard 스크롤...');
await page.evaluate(() => window.scrollTo(0, 500));
await page.waitForTimeout(400);
await shot('shot2_dashboard_scrolled.png');

// ── Shot 3: Dashboard "다음 할 일" 가이드 카드 ──
// incomes=[], expenses=[] → 수입 카드 1개만 표시
console.log('Shot 3: 다음 할 일 가이드 카드...');
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await fullshot('shot3_dashboard_guide_fullpage.png');

// ── Shot 4: Income 스텝 ──
console.log('Shot 4: Income 스텝...');
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
  // fallback: StepRail nav의 두 번째 버튼
  try { await page.locator('nav button').nth(1).click(); } catch {}
}
await page.waitForTimeout(700);
await page.evaluate(() => window.scrollTo(0, 0));
await shot('shot4_income_step.png');

// ── Shot 5: TaxSim 스텝 ──
console.log('Shot 5: TaxSim 스텝...');
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
  // fallback: StepRail의 네 번째 버튼
  try { await page.locator('nav button').nth(3).click(); } catch {}
}
await page.waitForTimeout(700);
await page.evaluate(() => window.scrollTo(0, 0));
await shot('shot5_taxsim_step.png');

await browser.close();
console.log('\n5장 캡처 완료!');
