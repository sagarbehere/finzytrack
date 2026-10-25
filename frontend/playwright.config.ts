import { defineConfig, devices } from '@playwright/test'

// Acceptance flows run in Chromium and WebKit (PROCESS.md §4, UI testing) against the built bundle.
export default defineConfig({
  testDir: 'e2e',
  forbidOnly: true,
  retries: 0,
  use: { baseURL: 'http://localhost:4173' },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
  webServer: { command: 'pnpm build && pnpm preview', url: 'http://localhost:4173', reuseExistingServer: false },
})
