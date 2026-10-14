import { expect, test, type Page, type TestInfo } from '@playwright/test'

const apiBase = process.env.GRADING_E2E_API_BASE || 'http://127.0.0.1:8000'

const fixture = {
  current: { caseId: 'case 1', criteriaFile: '01-current.json' },
  alternative: { caseId: 'case 2', criteriaFile: '02-alternative.json' },
  confirm: { caseId: 'case 3', criteriaFile: '03-update-confirm.json' },
  reject: { caseId: 'case 4', criteriaFile: '04-update-reject.json' },
  negative: { caseId: 'case 5', criteriaFile: '05-negative.json' },
} as const

const openFixture = async (page: Page, item: (typeof fixture)[keyof typeof fixture]) => {
  await page.goto('/')
  await page.getByTestId('case-select').selectOption(item.caseId)
  // The case catalog now selects its sole rubric; the selector is disabled
  // when there is no alternative. Verify that binding instead of changing it.
  await expect(page.getByTestId('criteria-file-select')).toHaveValue(item.criteriaFile)
  await expect(page.getByTestId('criteria-list')).toBeVisible()
}

const alignment = async (page: Page, item: (typeof fixture)[keyof typeof fixture]) => {
  const response = await page.request.get(
    `${apiBase}/cases/${encodeURIComponent(item.caseId)}/alignment?criteria_file_name=${encodeURIComponent(item.criteriaFile)}`,
  )
  expect(response.ok()).toBeTruthy()
  return (await response.json()).data
}

const report = async (
  page: Page,
  item: (typeof fixture)[keyof typeof fixture],
  reportName: string,
) => {
  const response = await page.request.get(
    `${apiBase}/cases/${encodeURIComponent(item.caseId)}/reports/${encodeURIComponent(reportName)}?criteria_file_name=${encodeURIComponent(item.criteriaFile)}`,
  )
  expect(response.ok()).toBeTruthy()
  return (await response.json()).data.result
}

const proposals = async (page: Page, item: (typeof fixture)[keyof typeof fixture]) => {
  const response = await page.request.get(
    `${apiBase}/cases/${encodeURIComponent(item.caseId)}/criterion-proposals?criteria_file_name=${encodeURIComponent(item.criteriaFile)}`,
  )
  expect(response.ok()).toBeTruthy()
  return (await response.json()).data.items
}

const screenshot = async (page: Page, testInfo: TestInfo, name: string) => {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true })
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('response', (response) => {
    if (response.url().startsWith(apiBase) && response.status() >= 400) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })
  ;(page as Page & { __boundaryErrors?: string[] }).__boundaryErrors = errors
})

test.afterEach(async ({ page }) => {
  expect((page as Page & { __boundaryErrors?: string[] }).__boundaryErrors || []).toEqual([])
})

test('Browser TT current: direct teacher adjudication without rubric revision', async ({
  page,
}, testInfo) => {
  const item = fixture.current
  await openFixture(page, item)
  const before = await alignment(page, item)
  expect(before.open_conflicts).toHaveLength(1)
  expect(before.open_conflicts[0].teacher_event_ids).toHaveLength(2)
  expect(before.open_conflicts[0].current_pattern.grader_type).toBe('teacher')
  expect(before.open_conflicts[0].alternative_pattern.grader_type).toBe('teacher')
  const rubricVersion = before.open_conflicts[0].audited_rubric_version

  await page.locator('summary').filter({ hasText: 'Cross-report conflict tasks' }).click()
  await expect(page.getByTestId('conflict-pattern-current')).toContainText('report-a')
  await expect(page.getByTestId('conflict-pattern-alternative')).toContainText('report-b')
  await page.getByTestId('select-current-pattern').click()
  await expect(page.getByTestId('teacher-conflict-card')).toHaveCount(0)

  const after = await alignment(page, item)
  const corrected = await report(page, item, 'report-b')
  const grade = corrected.criteria.find(
    (entry: { criteria_id: string }) => entry.criteria_id === '1',
  )
  expect(after.open_conflicts).toEqual([])
  expect(after.criterion_revisions).toEqual([])
  expect(after.auto_grade_batches).toEqual([])
  expect(grade.criteria_score).toBe(2)
  expect(grade.provenance.grader_type).toBe('teacher')
  expect(grade.provenance.rubric_version).toBe(rubricVersion)
  await expect(page.getByTestId('proposal-review-modal')).toHaveCount(0)
  await screenshot(page, testInfo, 'tt-current-final')
})

test('Browser TT alternative: one selection updates rubric and regrades only the criterion', async ({
  page,
}, testInfo) => {
  const item = fixture.alternative
  await openFixture(page, item)
  const before = await alignment(page, item)
  const rubricVersion = before.open_conflicts[0].audited_rubric_version

  await page.locator('summary').filter({ hasText: 'Cross-report conflict tasks' }).click()
  await page.getByTestId('select-alternative-pattern').click()
  await expect(page.getByTestId('proposal-review-modal')).toHaveCount(0)
  await expect
    .poll(
      async () => {
        const state = await alignment(page, item)
        return state.criterion_revisions[0]?.status
      },
      { timeout: 60_000 },
    )
    .toBe('completed')

  const after = await alignment(page, item)
  expect(after.open_conflicts).toEqual([])
  expect(after.criterion_revisions[0].target_rubric_version).not.toBe(rubricVersion)
  expect(after.criterion_revisions[0].report_names).toEqual(['report-a', 'report-b'])
  const targets = after.auto_grade_batches.flatMap(
    (batch: { targets?: Array<{ criterion_id: string }> }) => batch.targets || [],
  )
  expect(targets).toHaveLength(2)
  expect(new Set(targets.map((target: { criterion_id: string }) => target.criterion_id))).toEqual(
    new Set(['1']),
  )
  for (const reportName of ['report-a', 'report-b']) {
    const result = await report(page, item, reportName)
    const grade = result.criteria.find(
      (entry: { criteria_id: string }) => entry.criteria_id === '1',
    )
    expect(grade.criteria_score).toBe(1)
    expect(grade.provenance.grader_type).toBe('model')
    expect(grade.provenance.rubric_version).toBe(after.criterion_revisions[0].target_rubric_version)
    expect(
      result.criteria.some((entry: { criteria_id: string }) => entry.criteria_id === '2'),
    ).toBe(false)
  }
  await screenshot(page, testInfo, 'tt-alternative-final')
})

test('Browser TM confirm Update: proposal diff confirms and triggers replacement batch', async ({
  page,
}, testInfo) => {
  const item = fixture.confirm
  await openFixture(page, item)
  const before = await alignment(page, item)
  const pending = await proposals(page, item)
  expect(before.open_conflicts).toEqual([])
  expect(pending).toHaveLength(1)
  expect(pending[0].source_event_ids).toHaveLength(2)

  await page.getByTestId('review-criterion-proposal-1').click()
  await expect(page.getByTestId('proposal-review-modal')).toBeVisible()
  await expect(page.getByTestId('proposal-review-modal')).toContainText('Reason for update')
  await page.getByRole('button', { name: 'Confirm standard' }).click()
  await expect(page.getByTestId('proposal-review-modal')).toHaveCount(0)
  await expect
    .poll(async () => (await alignment(page, item)).criterion_revisions[0]?.status, {
      timeout: 60_000,
    })
    .toBe('completed')

  const after = await alignment(page, item)
  const result = await report(page, item, 'report-a')
  const grade = result.criteria.find((entry: { criteria_id: string }) => entry.criteria_id === '1')
  expect(after.open_conflicts).toEqual([])
  expect(after.criterion_revisions[0].target_rubric_version).not.toBe(
    pending[0].criteria_file_version_at_proposal,
  )
  expect(grade.criteria_score).toBe(1)
  expect(grade.provenance.grader_type).toBe('model')
  await screenshot(page, testInfo, 'tm-confirm-final')
})

test('Browser TM reject Update: teacher score remains and no conflict or regrade appears', async ({
  page,
}, testInfo) => {
  const item = fixture.reject
  await openFixture(page, item)
  const rubricBefore = await page.request.get(`${apiBase}/criteria/${item.criteriaFile}`)
  const beforeJson = await rubricBefore.json()

  await page.getByTestId('review-criterion-proposal-1').click()
  await page.getByRole('button', { name: 'Reject', exact: true }).click()
  await expect(page.getByTestId('proposal-review-modal')).toHaveCount(0)
  await expect.poll(async () => (await proposals(page, item))[0]?.status).toBe('rejected')

  const after = await alignment(page, item)
  const result = await report(page, item, 'report-a')
  const grade = result.criteria.find((entry: { criteria_id: string }) => entry.criteria_id === '1')
  const rubricAfter = await page.request.get(`${apiBase}/criteria/${item.criteriaFile}`)
  expect(await rubricAfter.json()).toEqual(beforeJson)
  expect(after.open_conflicts).toEqual([])
  expect(after.criterion_revisions).toEqual([])
  expect(after.auto_grade_batches).toEqual([])
  expect(grade.criteria_score).toBe(1)
  expect(grade.provenance.grader_type).toBe('teacher')
  await screenshot(page, testInfo, 'tm-reject-final')
})

test('Browser negative boundary: replacement, one teacher, and migration state stay out of Conflict', async ({
  page,
}, testInfo) => {
  const item = fixture.negative
  await openFixture(page, item)
  const state = await alignment(page, item)
  const pending = await proposals(page, item)
  const result = await report(page, item, 'report-a')
  const grade = result.criteria.find((entry: { criteria_id: string }) => entry.criteria_id === '1')

  expect(state.open_conflicts).toEqual([])
  expect(pending).toEqual([])
  expect(await page.getByText('Cross-report conflict tasks').count()).toBe(0)
  expect(grade.criteria_score).toBe(1)
  expect(grade.provenance.grader_type).toBe('teacher')
  await screenshot(page, testInfo, 'negative-boundary-final')
})
