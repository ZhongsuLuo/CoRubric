import { defineConfig, devices } from '@playwright/test'

const e2eRoot = '/tmp/grading-conflict-update-e2e'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'conflict-update-boundary.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: 'test-results/conflict-update-boundary',
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: [
    {
      command: '.venv/bin/uvicorn tests.e2e_app:app --host 127.0.0.1 --port 8000',
      cwd: '..',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        GRADING_E2E_ROOT: e2eRoot,
        GRADING_E2E_RESET: '1',
        GRADING_CRITERIA_DIR: `${e2eRoot}/criteria`,
        GRADING_CASE_ROOT: `${e2eRoot}/case`,
        GRADING_DATA_DIR: `${e2eRoot}/api`,
        GRADING_CRITERIA_BASELINE_DIR: `${e2eRoot}/baselines`,
        AUTO_GRADE_MIN_TEACHER_SAMPLES: '0',
      },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4173',
      cwd: '.',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
