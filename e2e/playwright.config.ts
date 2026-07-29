import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * These tests run against the LIVE docker-compose stack (web on :5173, API on
 * :8000, Postgres on :55432). Nothing is mocked and nothing is started here on
 * purpose: the acceptance gate is "does the real system work", so a webServer
 * block that could quietly boot a different build would defeat the point.
 *
 * Timeouts are deliberately generous. The first ranking for a *new* profile
 * builds random walks on the MeritRank engine and legitimately takes 40-90s;
 * an explanation reconstructs contributing paths and takes several seconds.
 */
export default defineConfig({
  testDir: path.join(here, 'tests'),
  outputDir: path.join(here, 'test-results'),
  globalSetup: path.join(here, 'global-setup.ts'),

  // One worker: the engine is a shared, single stateful process. Parallel
  // profiles warming walks at the same time turn 40s into minutes and make
  // timing-sensitive assertions flaky for reasons that have nothing to do with
  // the code under test.
  fullyParallel: false,
  workers: 1,

  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,

  // 4 minutes per test: cold ranking (~90s) + explain (~10s) + screenshots.
  timeout: 240_000,
  expect: { timeout: 120_000 },

  reporter: [['list'], ['html', { outputFolder: path.join(here, 'playwright-report'), open: 'never' }]],

  use: {
    baseURL: 'http://localhost:5173',
    actionTimeout: 120_000,
    navigationTimeout: 120_000,
    screenshot: 'only-on-failure',
    video: 'off',
    trace: 'on-first-retry',
    viewport: { width: 1280, height: 900 },
    colorScheme: 'light',
    // Reuse the warm, 5-seed profile created in global-setup so only the tests
    // that are *about* cold start pay for a cold start. Specs that need a fresh
    // identity opt out with test.use({ storageState: { cookies: [], origins: [] } }).
    storageState: path.join(here, '.auth', 'warm.json'),
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 900 },
        colorScheme: 'light',
        // Full Chromium (not the headless shell) with SwiftShader so the sigma.js
        // WebGL graph renderer actually produces a context in headless CI.
        channel: 'chromium',
        launchOptions: {
          args: [
            '--use-gl=angle',
            '--use-angle=swiftshader',
            '--enable-unsafe-swiftshader',
            '--ignore-gpu-blocklist',
          ],
        },
      },
    },
  ],
});
