import { defineConfig, devices } from '@playwright/test'

// Acceptance flows run in Chromium and WebKit (PROCESS.md §4, UI testing) against the built bundle, each worker with
// its own harness run (e2e/harness/fixtures.ts; S0030): every project is west of UTC, and one Chromium project is
// east of UTC for the after-midnight case (review:27 §1(d); ENGINEERING.md EP-10).
process.env['TZ'] = 'America/Los_Angeles'
const west = { timezoneId: 'America/Los_Angeles', locale: 'en-US' }

export default defineConfig({
  testDir: 'e2e',
  forbidOnly: true,
  retries: 0,
  use: { baseURL: 'http://localhost:4173' },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], ...west } },
    { name: 'webkit', use: { ...devices['Desktop Safari'], ...west } },
    { name: 'chromium-kolkata', use: { ...devices['Desktop Chrome'], timezoneId: 'Asia/Kolkata', locale: 'en-US' } },
  ],
  webServer: { command: 'pnpm build && pnpm preview', url: 'http://localhost:4173', reuseExistingServer: false },
})
