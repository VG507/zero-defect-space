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
    await page.locator('#items').getByRole('button', { name: /I-002/ }).waitFor();
    await page.getByRole('button', { name: /Входной дефект · I-002/ }).click();
    await page.locator('#detail-title').getByText('I-002').waitFor();
    assert.match(await page.locator('#scenario-explanation').innerText(), /до последующих операций/);
    assert.match(await page.locator('.cost-comparison').innerText(), /Разница в этой модели: 25\s*000 ₽/);
    await page.locator('.cost-field input').nth(1).fill('10000');
    assert.match(await page.locator('.cost-comparison').innerText(), /Разница в этой модели: 23\s*000 ₽/);
    await page.getByRole('button', { name: /Сигнал после операции · I-003/ }).click();
    await page.locator('#detail-title').getByText('I-003').waitFor();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#print-report').isEnabled(), true);
    assert.match(await page.locator('#system-status').innerText(), /эмулятором/);
    await page.emulateMedia({ media: 'print' });
    assert.equal(await page.locator('.detail-pane').isVisible(), true);
    assert.equal(await page.locator('.topbar').isVisible(), false);
    await page.emulateMedia({ media: 'screen' });
    await page.locator('#items').getByRole('button', { name: /I-003/ }).click();
    await page.locator('#detail-title').getByText('I-003').waitFor();
    assert.equal(await page.locator('#checked').innerText(), '5');
    assert.equal(await page.locator('#confirmed').innerText(), '3');
    assert.equal(await page.locator('#line .line-row').count(), 6);
    assert.match(await page.locator('#line').innerText(), /Нет пригодного контроля в срок/);
    assert.match(await page.locator('#detail').innerText(), /weld_anomaly/);
    assert.match(await page.locator('.investigation').innerText(), /Сигнал оборудования/);
    assert.match(await page.locator('.investigation').innerText(), /Времени|События сопоставлены по времени/);
    await page.getByRole('button', { name: /I-004: Нет пригодного контроля/ }).click();
    await page.locator('#detail-title').getByText('I-004').waitFor();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#detail').innerText(), /Контрольные точки|Ожидаемые контрольные точки/);
    assert.match(await page.locator('#detail').innerText(), /Нет пригодного контроля в срок/);
    await page.getByRole('button', { name: /Повторная обработка · I-005/ }).click();
    await page.locator('#detail-title').getByText('I-005').waitFor();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#detail').innerText(), /Повтор после R-005-1/);
    assert.match(await page.locator('#detail').innerText(), /Дополнительная проверка/);
    await page.getByRole('button', { name: /Позднее событие · I-006/ }).click();
    await page.locator('#detail-title').getByText('I-006').waitFor();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#detail').innerText(), /После решения пришло более раннее событие/);
    assert.match(await page.locator('#detail').innerText(), /Признак обнаружен на входном контроле/);
    await page.locator('#items').getByRole('button', { name: /I-003/ }).click();
    await page.locator('#detail-title').getByText('I-003').waitFor();
    await page.locator('#detail').getByText('Загружаем историю…').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#outbox .message').count(), 5);
    assert.equal(await page.locator('#outbox .state-acknowledged').count(), 5);
    if (process.env.QC_DEMO_CONTROLLER_TOKEN) {
      await page.locator('#token').fill(process.env.QC_DEMO_CONTROLLER_TOKEN);
      await page.getByRole('button', { name: 'Открыть данные' }).click();
      await page.locator('.decision-form select').selectOption('rejected');
      await page.getByRole('textbox', { name: 'Идентификатор контролёра' }).fill('browser-controller');
      await page.getByRole('textbox', { name: 'Основание решения' }).fill('Повторная проверка отвергла признак');
      await page.getByRole('button', { name: 'Сохранить решение' }).click();
      await page.waitForFunction(() => document.getElementById('confirmed').textContent === '2');
      assert.equal(await page.locator('#outbox .message').count(), 6);
    }
    if (process.env.QC_DEMO_ADMIN_TOKEN) {
      await page.locator('#token').fill(process.env.QC_DEMO_ADMIN_TOKEN);
      await page.getByRole('button', { name: 'Открыть данные' }).click();
      await page.getByRole('button', { name: 'Проверить журнал' }).click();
      await page.getByText('Проверено исходных событий: 31').waitFor();
      assert.equal(await page.locator('#integrity-result .integrity-step').count(), 3);
      assert.match(await page.locator('#integrity-result').innerText(), /локальным HMAC-якорем/);
      const image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR7sAAAAASUVORK5CYII=';
      const response = await page.evaluate(async data => fetch('/api/events', {
        method: 'POST', headers: { Authorization: `Bearer ${data.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ schema_version: 1, source_id: 'browser-test', event_id: 'photo-smoke',
          item_id: 'PHOTO-SMOKE', event_type: 'InspectionReported', occurred_at: '2026-09-25T10:00:00+03:00',
          payload: { inspection_result: 'unable_to_assess', observation_quality: 'poor', defects: [],
            evidence_image: { mime_type: 'image/png', data_base64: data.image } } })
      }).then(r => r.json()), { token: process.env.QC_DEMO_ADMIN_TOKEN, image });
      assert.equal(response.state, 'applied');
      await page.getByRole('button', { name: 'Обновить данные' }).click();
      await page.locator('#items').getByRole('button', { name: /PHOTO-SMOKE/ }).click();
      await page.locator('#detail img[alt*="photo-smoke"]').waitFor();
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
