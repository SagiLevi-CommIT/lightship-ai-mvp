import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for the Lightship UI.
 *
 * The tests hit a locally-running `next dev` (or `next start`) server and
 * stub the backend fetches at the network layer — no AWS dependencies, no
 * real pipeline runs. See `tests/e2e/*.spec.ts` for the individual flows.
 *
 * Usage:
 *   npx playwright test
 *
 * CI sets `CI=true` which tightens retries/workers and disables the
 * auto-start webserver to let the workflow manage it explicitly.
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:3000',
    testIdAttribute: 'data-test-id',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    headless: true,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: 'npx next dev -p 3000',
        port: 3000,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        env: {
          NEXT_PUBLIC_API_BASE: '',
        },
      },
});
