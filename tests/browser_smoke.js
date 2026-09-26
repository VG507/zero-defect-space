const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'], ...(process.env.QC_BROWSER_PATH ? { executablePath: process.env.QC_BROWSER_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1365, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.QC_BASE_URL || 'http://127.0.0.1:8765', { waitUntil: 'networkidle' });
    await page.locator('#token').fill(process.env.QC_DEMO_VIEWER_TOKEN);
    await page.getByRole('button', { name: 'Открыть данные' }).click();
    await page.locator('#items').getByRole('button', { name: /I-003/ }).click();
    await page.locator('#detail-title').getByText('I-003').waitFor();
    assert.equal(await page.locator('#checked').innerText(), '3');
    assert.equal(await page.locator('#confirmed').innerText(), '2');
    assert.equal(await page.locator('#line .line-row').count(), 4);
    assert.match(await page.locator('#line').innerText(), /Нет пригодного контроля в срок/);
    assert.match(await page.locator('#detail').innerText(), /weld_anomaly/);
    await page.getByRole('button', { name: /I-004: Нет пригодного контроля/ }).click();
    await page.locator('#detail-title').getByText('I-004').waitFor();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#detail').innerText(), /Контрольные точки|Ожидаемые контрольные точки/);
    assert.match(await page.locator('#detail').innerText(), /Нет пригодного контроля в срок/);
    await page.locator('#items').getByRole('button', { name: /I-003/ }).click();
    await page.locator('#detail-title').getByText('I-003').waitFor();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#outbox .message').count(), 2);
    assert.equal(await page.locator('#outbox .state-acknowledged').count(), 2);
    if (process.env.QC_DEMO_CONTROLLER_TOKEN) {
      await page.locator('#token').fill(process.env.QC_DEMO_CONTROLLER_TOKEN);
      await page.getByRole('button', { name: 'Открыть данные' }).click();
      await page.locator('.decision-form select').selectOption('rejected');
      await page.getByRole('textbox', { name: 'Идентификатор контролёра' }).fill('browser-controller');
      await page.getByRole('textbox', { name: 'Основание решения' }).fill('Повторная проверка отвергла признак');
      await page.getByRole('button', { name: 'Сохранить решение' }).click();
      await page.waitForFunction(() => document.getElementById('confirmed').textContent === '1');
      assert.equal(await page.locator('#outbox .message').count(), 3);
    }
    await page.setViewportSize({ width: 375, height: 812 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: process.env.QC_SCREENSHOT || 'dashboard-smoke.png', fullPage: true });
    console.log('Browser smoke passed: metrics, selection, history, no page errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
