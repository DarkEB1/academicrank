import { expect, test, type Page } from '@playwright/test';
import { collectConsole, gotoRoute, shot, warmProfile } from '../helpers/app';

/**
 * Every screen renders, against the warm five-seed profile, without a single
 * console error or uncaught exception. Each one is screenshotted.
 */
const warm = warmProfile();

type Screen = {
  file: string;
  route: string;
  label: string;
  ready: (page: Page) => Promise<void>;
};

const SCREENS: Screen[] = [
  {
    file: '06-screen-rankings.png',
    route: '/',
    label: 'rankings',
    ready: async (page) => {
      await expect(page.getByRole('heading', { name: 'Rankings', level: 1 })).toBeVisible();
      await expect(page.getByRole('button', { name: /^Explain the score for/ }).first()).toBeVisible();
    },
  },
  {
    file: '07-screen-trust.png',
    route: '/trust',
    label: 'trust set',
    ready: async (page) => {
      await expect(page.getByRole('heading', { name: 'Trust set', exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Your trust set (5)' })).toBeVisible();
    },
  },
  {
    file: '08-screen-recommendations.png',
    route: '/recommendations',
    label: 'recommendations',
    ready: async (page) => {
      await expect(page.getByRole('heading', { name: 'Recommendations', level: 1 })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Diversity dial' })).toBeVisible();
      await expect(page.getByRole('img', { name: /95% interval/ }).first()).toBeVisible();
    },
  },
  {
    file: '09-screen-graph.png',
    route: '/graph',
    label: 'graph explorer',
    ready: async (page) => {
      await expect(page.getByRole('heading', { name: 'Graph explorer' })).toBeVisible();
      await expect(page.getByRole('application', { name: /Trust neighbourhood graph/ })).toBeVisible();
      // Let ForceAtlas2 settle so the screenshot is of a laid-out graph.
      await expect(page.getByText('settling layout…')).toHaveCount(0, { timeout: 120_000 });
    },
  },
  {
    file: '10-screen-params.png',
    route: '/params',
    label: 'parameter playground',
    ready: async (page) => {
      await expect(page.getByRole('heading', { name: 'Parameter playground' })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Top twenty, live' })).toBeVisible();
      await expect(page.getByText(/computed in /)).toBeVisible();
    },
  },
  {
    file: '11-screen-paper-detail.png',
    route: `/paper/${warm.topPaperId}`,
    label: 'paper detail',
    ready: async (page) => {
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.getByText(/proximity in a weighted trust graph/).first()).toBeVisible();
    },
  },
];

for (const screen of SCREENS) {
  test(`${screen.label} (${screen.route}) renders with no console errors`, async ({ page }) => {
    const console_ = collectConsole(page);
    await gotoRoute(page, screen.route);
    await screen.ready(page);
    await shot(page, screen.file);

    expect(
      console_.pageErrors,
      `uncaught exception(s) on ${screen.route}:\n${console_.pageErrors.join('\n')}`,
    ).toEqual([]);
    expect(
      console_.errors,
      `console error(s) on ${screen.route}:\n${console_.errors.join('\n')}`,
    ).toEqual([]);
  });
}
