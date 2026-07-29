import { expect, test, type Page } from '@playwright/test';
import { gotoRoute, hasHorizontalOverflow, warmProfile } from '../helpers/app';

/**
 * No route may scroll the document sideways at 1280px. Deliberate inner scroll
 * containers (the ranking table is `min-w-[64rem]` inside `overflow-x-auto`)
 * are fine and are excluded from the culprit report.
 */
const warm = warmProfile();

const ROUTES: { route: string; label: string; ready: (page: Page) => Promise<void> }[] = [
  {
    route: '/',
    label: 'rankings',
    ready: async (page) =>
      void (await expect(
        page.getByRole('button', { name: /^Explain the score for/ }).first(),
      ).toBeVisible()),
  },
  {
    route: '/trust',
    label: 'trust set',
    ready: async (page) =>
      void (await expect(page.getByRole('heading', { name: 'Your trust set (5)' })).toBeVisible()),
  },
  {
    route: '/recommendations',
    label: 'recommendations',
    ready: async (page) =>
      void (await expect(page.getByRole('heading', { name: 'Diversity dial' })).toBeVisible()),
  },
  {
    route: '/graph',
    label: 'graph explorer',
    ready: async (page) =>
      void (await expect(
        page.getByRole('application', { name: /Trust neighbourhood graph/ }),
      ).toBeVisible()),
  },
  {
    route: '/params',
    label: 'parameter playground',
    ready: async (page) =>
      void (await expect(page.getByText(/computed in /)).toBeVisible()),
  },
  {
    route: `/paper/${warm.topPaperId}`,
    label: 'paper detail',
    ready: async (page) =>
      void (await expect(page.getByRole('heading', { level: 1 })).toBeVisible()),
  },
];

for (const { route, label, ready } of ROUTES) {
  test(`${label} (${route}) does not overflow horizontally at 1280px`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await gotoRoute(page, route);
    await ready(page);
    await page.waitForTimeout(500);

    const result = await hasHorizontalOverflow(page);
    expect(
      result.overflow,
      `${route} scrolls horizontally: scrollWidth=${result.scrollWidth} clientWidth=${result.clientWidth}\n` +
        `culprits:\n  ${result.culprits.join('\n  ')}`,
    ).toBe(false);
  });
}
