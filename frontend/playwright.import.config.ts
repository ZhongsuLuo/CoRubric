import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  testMatch: 'folder-import.spec.ts',
  outputDir: 'test-results/folder-import',
  webServer: [
    {
      command: '.venv/bin/uvicorn tests.e2e_import_app:app --host 127.0.0.1 --port 8000',
      cwd: '..',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: false,
      env: { GRADING_E2E_RESET: '1', GRADING_PARSER_CONFIG: 'backend/ocr/config.example.toml' },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4173',
      cwd: '.',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
    },
  ],
})
