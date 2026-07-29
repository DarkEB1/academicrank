import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { WEB_ORIGIN } from './helpers/env';

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

  // 7 minutes per test. A cold ranking is 40-90s on an idle engine and can be
  // several minutes when the engine has just restarted or is under load, and
  // some steps pay for more than one. Individual slow steps raise this further
  // with test.setTimeout().
  timeout: 420_000,
  expect: { timeout: 180_000 },

  reporter: [['list'], ['html', { outputFolder: path.join(here, 'playwright-report'), open: 'never' }]],

  use: {
    // http://127.0.0.1:5173 — the docker `web` container (nginx, built app).
    // See helpers/env.ts for why this is not spelled `localhost`.
    baseURL: WEB_ORIGIN,
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
