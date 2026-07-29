import { expect, test } from '@playwright/test';
import { SEED_QUERIES } from '../helpers/api';
import { EMPTY_STATE, WARM_STATE, gotoRoute, shot } from '../helpers/app';

/**
 * Cold-start honesty. Below five seeds the product must say, in the interface,
 * that the ranking is not reliable — and it must stop saying it once there are
 * five. This is a claim about the app's integrity, so both halves are asserted.
 */

test.describe('below five seeds', () => {
  test.use({ storageState: EMPTY_STATE });

  test('the UI says the rankings are unreliable', async ({ page }) => {
    await gotoRoute(page, '/trust');

    // Zero seeds: the strongest form of the notice.
    await expect(page.getByRole('note').filter({ hasText: 'No seeds yet — nothing can be ranked' })).toBeVisible();
    await expect(
      page.getByText('This notice stays until you have five.', { exact: false }),
    ).toBeVisible();
    await shot(page, '17-cold-start-zero-seeds.png');

    // Add two seeds through the UI and the notice must change, not vanish.
    const searchCard = page
      .locator('section')
      .filter({ has: page.getByRole('heading', { name: 'Find papers to seed' }) });
    const searchBox = page.getByLabel('Search papers');

    for (let i = 0; i < 2; i++) {
      await searchBox.fill(SEED_QUERIES[i]);
      await expect(searchCard.getByText(/\d[\d,]* match(es)?/)).toBeVisible();
      await searchCard.getByRole('button', { name: 'Add' }).first().click();
      await expect(page.getByRole('heading', { name: `Your trust set (${i + 1})` })).toBeVisible();
    }

    await expect(
      page.getByRole('note').filter({ hasText: '2 seeds: rankings are unreliable' }),
    ).toBeVisible();
    await shot(page, '18-cold-start-two-seeds.png');

    // The same honesty on the rankings screen itself. The notice is part of the
    // header and does not wait for the (slow) first ranking to arrive.
    await gotoRoute(page, '/');
    await expect(
      page.getByRole('note').filter({ hasText: '2 seeds: this ranking is not reliable' }),
    ).toBeVisible();
    await expect(page.getByRole('link', { name: 'Add more seeds' })).toBeVisible();
    await shot(page, '19-cold-start-two-seeds-rankings.png');
  });
});

test.describe('at five or more seeds', () => {
  test.use({ storageState: WARM_STATE });

  test('the unreliability notice is gone', async ({ page }) => {
    await gotoRoute(page, '/trust');
    await expect(page.getByRole('heading', { name: 'Your trust set (5)' })).toBeVisible();

    await expect(page.getByText('No seeds yet — nothing can be ranked')).toHaveCount(0);
    await expect(page.getByText(/seeds?: rankings are unreliable/)).toHaveCount(0);
    await expect(page.getByText('With 5 seeds the ranking is usable.')).toBeVisible();

    await gotoRoute(page, '/');
    await expect(page.getByRole('button', { name: /^Explain the score for/ }).first()).toBeVisible();
    await expect(page.getByText(/this ranking is not reliable/)).toHaveCount(0);
    await expect(page.getByText('The server flags this ranking as unreliable')).toHaveCount(0);

    await shot(page, '20-cold-start-five-seeds-reliable.png');
  });
});
