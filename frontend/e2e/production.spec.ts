import { expect, test } from '@playwright/test'

test('built UI uses authenticated same-origin API and hides experiment reset', async ({ page, playwright }, testInfo) => {
  const anonymous = await playwright.request.newContext({ httpCredentials: undefined })
  try {
    expect((await anonymous.get('http://127.0.0.1:4180/')).status()).toBe(401)
    expect((await anonymous.get('http://127.0.0.1:4180/api/cases')).status()).toBe(401)
    expect((await anonymous.get('http://127.0.0.1:4180/api/health')).status()).toBe(200)
  } finally {
    await anonymous.dispose()
  }
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByTestId('case-select')).toBeVisible()
  await expect(page.getByTestId('report-total-score')).toContainText('/3')
  await expect(page.getByTestId('debug-reset-experiment')).toHaveCount(0)
  expect((await page.request.get('/api/cases')).ok()).toBeTruthy()
  expect((await page.request.post('/api/cases/case%201/debug/reset-experiment', { data: {} })).status()).toBe(404)
  await page.screenshot({ path: testInfo.outputPath('production-desktop.png'), fullPage: true })
  expect(errors).toEqual([])
})
