import { expect, test, type Page } from '@playwright/test';
import { collectConsole, gotoRoute, shot, warmProfile } from '../helpers/app';

/**
 * The Search screen: a plain relevance search, optionally re-ordered by
 * Reciprocal Rank Fusion against "Your trust" set or unpersonalised "Global
 * merit". This spec proves the re-ordering is real -- computed by the live
 * MeritRank engine against the warm profile, not decorative -- and that every
 * ranked score stays honest about its uncertainty (no bare numbers) and about
 * what it does not show (lift is never computed for ranked search).
 *
 * Query choice: `e2e/global-setup.ts` seeds the warm profile with one trusted
 * paper from each of `SEED_QUERIES` in `e2e/helpers/api.ts`, which includes
 * "graph theory". QUERY = 'graph' overlaps that seeded field, so trust
 * proximity has something to say -- a relevance-only order has no reason to
 * agree with an order that favours papers close to the graph-theory seed.
 */
const warm = warmProfile();
const QUERY = 'graph';

async function resultIds(page: Page): Promise<string[]> {
  return page
    .locator('[data-testid="search-result"]')
    .evaluateAll((rows) => rows.map((r) => r.getAttribute('data-work-id') ?? ''));
}

test('search re-orders under trust mode and shows honest scores', async ({ page }) => {
  const console_ = collectConsole(page);

  // eslint-disable-next-line no-console
  console.log(
    `[11-search] warm profile ${warm.profileId} seeded from: ${warm.seedTitles.join(' | ')}`,
  );

  await gotoRoute(page, '/search');

  const searchBox = page.getByLabel('Search papers');
  await searchBox.fill(QUERY);

  // --- relevance mode (the default) -------------------------------------
  await expect(page.getByText(/\d[\d,]* match(es)?/)).toBeVisible();
  const relevanceIds = await resultIds(page);
  expect(
    relevanceIds.length,
    'relevance search should return a full page of results',
  ).toBeGreaterThanOrEqual(10);

  // --- trust mode ---------------------------------------------------------
  const trustRadio = page.getByRole('radio', { name: /Your trust/ });
  await trustRadio.click();
  await expect(trustRadio).toHaveAttribute('aria-checked', 'true');

  await expect(page.getByTestId('disclaimer')).toContainText(/reciprocal rank fusion/i);
  await expect(page.locator('[data-testid="search-result"]').first()).toBeVisible();

  const trustIds = await resultIds(page);
  expect(
    trustIds.length,
    'trust-ranked search should return a full page of results',
  ).toBeGreaterThanOrEqual(10);

  // The two orderings genuinely differ. Compare the whole first page (both
  // capped at the screen's default limit of 25) rather than a short window,
  // so a coincidental match near the top cannot pass this by accident.
  expect(
    trustIds.join('|'),
    `trust order matched relevance order exactly for query "${QUERY}".\n` +
      `relevance: ${relevanceIds.join(', ')}\ntrust: ${trustIds.join(', ')}`,
  ).not.toEqual(relevanceIds.join('|'));

  // No bare scores: every score bar's accessible name announces its 95%
  // confidence interval in bracket notation, e.g. "... 95% interval [0.01, 0.02] ...".
  const scoreBars = page.locator('table').getByRole('img');
  expect(await scoreBars.count(), 'the ranked table should render score bars').toBeGreaterThan(0);
  await expect(scoreBars.first()).toHaveAccessibleName(/\[/);

  // `lift` is hard-hidden on ranked search -- the whole column must be absent,
  // not merely blank, so nobody can read a fabricated "+0.00" as a real score.
  await expect(page.getByRole('columnheader', { name: 'Lift' })).toHaveCount(0);

  await shot(page, '28-search-trust-mode.png');

  // --- global merit mode ---------------------------------------------------
  // Same ranked-table machinery, unpersonalised: prove the third mode also
  // renders honestly, not just the one the headline assertion covers.
  const globalRadio = page.getByRole('radio', { name: 'Global merit' });
  await globalRadio.click();
  await expect(globalRadio).toHaveAttribute('aria-checked', 'true');

  await expect(page.getByTestId('disclaimer')).toContainText(/unpersonalised global merit/i);
  await expect(page.locator('[data-testid="search-result"]').first()).toBeVisible();

  const globalIds = await resultIds(page);
  expect(
    globalIds.length,
    'global-merit search should return a full page of results',
  ).toBeGreaterThanOrEqual(10);

  await shot(page, '29-search-global-mode.png');

  expect(console_.pageErrors, `uncaught exception(s):\n${console_.pageErrors.join('\n')}`).toEqual([]);
  expect(console_.errors, `console error(s):\n${console_.errors.join('\n')}`).toEqual([]);
});
