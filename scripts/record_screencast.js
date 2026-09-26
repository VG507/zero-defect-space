const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const output = path.resolve(process.env.QC_RECORDING_DIR || 'demo/recordings');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'], ...(process.env.QC_BROWSER_PATH ? { executablePath: process.env.QC_BROWSER_PATH } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 }, recordVideo: { dir: output, size: { width: 1365, height: 900 } } });
    const page = await context.newPage();
    page.on('pageerror', error => console.error('Browser error:', error.message));
    const video = page.video();
    await page.goto(process.env.QC_BASE_URL || 'http://127.0.0.1:8765', { waitUntil: 'networkidle' });
    await page.locator('#token').fill(process.env.QC_DEMO_VIEWER_TOKEN);
    await page.getByRole('button', { name: 'Открыть данные' }).click();
    await page.locator('#items button').first().waitFor({ timeout: 10000 }).catch(async error => {
      console.error('UI notice:', await page.locator('#notice').innerText());
      throw error;
    });
    await page.locator('#items').getByRole('button', { name: /I-002/ }).click();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    await page.waitForTimeout(1500);
    await page.locator('#items').getByRole('button', { name: /I-003/ }).click();
    await page.locator('#detail').getByText('weld_anomaly', { exact: false }).first().waitFor();
    await page.waitForTimeout(1500);
    await page.locator('#items').getByRole('button', { name: /I-004/ }).click();
    await page.locator('#detail').getByText('Нет пригодного контроля в срок').first().waitFor();
    await page.waitForTimeout(1500);
    await context.close();
    console.log(`Recording saved: ${await video.path()}`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
