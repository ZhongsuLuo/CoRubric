import { defineConfig, devices } from '@playwright/test'

process.env.GRADING_E2E_API_BASE = 'http://127.0.0.1:4180/api'

export default defineConfig({
  testDir: './e2e',
  testMatch: ['production.spec.ts', 'folder-import.spec.ts', 'conflict-update-boundary.spec.ts'],
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: 'test-results/production',
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4180',
    httpCredentials: { username: 'acceptance', password: 'acceptance-test-only-password' },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: '.venv/bin/uvicorn tests.e2e_production_app:app --host 127.0.0.1 --port 4180',
    cwd: '..',
    url: 'http://127.0.0.1:4180/api/health',
    reuseExistingServer: false,
    env: {
      GRADING_E2E_RESET: '1',
      GRADING_AUTH_USER: 'acceptance',
      GRADING_AUTH_PASSWORD: 'acceptance-test-only-password',
      GRADING_PARSER_CONFIG: 'backend/ocr/config.example.toml',
    },
  },
  projects: [{ name: 'chromium' }],
})
