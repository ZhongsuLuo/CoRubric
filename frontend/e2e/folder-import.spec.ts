import { expect, test } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'

// Minimal real PDF: the backend validates bytes and page count before accepting it.
const pdf = Buffer.from(
  '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 300]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF',
)

test('folder upload → durable progress → case → criteria creation, with images', async ({
  page,
}, testInfo) => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'folder-import-e2e-'))
  const folder = path.join(temp, '实验报告')
  await fs.mkdir(path.join(folder, '第二组'), { recursive: true })
  await fs.writeFile(path.join(folder, '学生.pdf'), pdf)
  await fs.writeFile(path.join(folder, '第二组', '学生.PDF'), pdf)
  await fs.writeFile(path.join(folder, '备注.txt'), 'ignore')
  try {
    await page.goto('/')
    await page.getByTestId('folder-import-open').click()
    await page.getByTestId('folder-input').setInputFiles(folder)
    await expect(page.getByTestId('import-name')).toHaveValue('实验报告')
    await page.getByTestId('import-name').fill('上传验收case')
    await page.getByTestId('import-submit').click()
    await expect(page.getByRole('button', { name: 'Use this case' })).toBeVisible()
    await expect(page.getByRole('dialog')).toContainText('Completed 2/2')
    await expect(page.getByTestId('folder-input')).toHaveValue('')
    await page.screenshot({ path: testInfo.outputPath('folder-progress.png'), fullPage: true })
    await page.reload()
    await page.getByTestId('folder-import-open').click()
    await expect(page.getByRole('dialog')).toContainText('上传验收case')
    await page.getByRole('button', { name: 'Use this case' }).click()
    await expect(page.getByTestId('case-select')).toHaveValue('上传验收case')
    await expect(
      page.getByText('Evidence from the imported PDF.', { exact: true }).first(),
    ).toBeVisible()
    const image = page.locator('img[alt="Source chart"]').first()
    await expect(image).toBeVisible()
    await expect
      .poll(() => image.evaluate((el) => (el as HTMLImageElement).naturalWidth))
      .toBeGreaterThan(0)
    await page.getByRole('button', { name: 'Create criteria', exact: true }).click()
    await page.getByPlaceholder('e.g. Physics Lab Grading').fill('上传专属评分标准')
    await page.getByRole('button', { name: 'Create Criteria', exact: true }).click()
    const criteria = await page.request.get(
      `${process.env.GRADING_E2E_API_BASE || 'http://127.0.0.1:8000'}/criteria?case_id=` + encodeURIComponent('上传验收case'),
    )
    expect((await criteria.json()).data.items).toHaveLength(2)
    await expect(page.getByRole('combobox').filter({ has: page.locator('option:checked', { hasText: '上传专属评分标准' }) })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('imported-case.png'), fullPage: true })
  } finally {
    await fs.rm(temp, { recursive: true, force: true })
  }
})
