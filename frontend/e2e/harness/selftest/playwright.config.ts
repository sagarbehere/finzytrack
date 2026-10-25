import { defineConfig, devices } from '@playwright/test'

// A nested run that e2e/harness.spec.ts starts to prove that a problem left at the end of a test fails it (S0030).
// Its tests leak on purpose, so they live outside the main run's test match (`*.spec.ts`).
export default defineConfig({
  testDir: '.',
  testMatch: '*.selftest.ts',
  retries: 0,
  reporter: 'line',
  outputDir: process.env['FT_SELFTEST_OUT'] ?? 'test-results',
  use: { baseURL: 'http://localhost:4173' }, // no server: each test routes what it loads
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], timezoneId: 'America/Los_Angeles' } }],
})
