import { expect, test } from '@playwright/test';
import { apiClient } from '../helpers/api';
import { gotoRoute, shot, warmProfile } from '../helpers/app';

/**
 * The API ships a `disclaimer` string with every ranking. The UI must print it
 * verbatim — not paraphrased, not truncated, not tucked behind a tooltip.
 */
test('the API disclaimer is rendered verbatim on the rankings screen', async ({ page }) => {
  const warm = warmProfile();
  const server = await apiClient.rankings(warm.profileId, warm.token, { limit: 25 });
  const expected = server.disclaimer;

  expect(expected, 'the API must supply a disclaimer to render').toBeTruthy();
  expect(expected.length).toBeGreaterThan(50);

  await gotoRoute(page, '/');
  await expect(page.getByRole('button', { name: /^Explain the score for/ }).first()).toBeVisible();

  const disclaimer = page.getByTestId('disclaimer');
  await expect(disclaimer).toBeVisible();

  // textContent, not innerText: verbatim means byte-for-byte, including the
  // double hyphen the server uses.
  const rendered = await disclaimer.textContent();
  expect(rendered, 'the disclaimer must match the server string exactly').toBe(expected);

  // Also present on the parameter playground, which shows scores too.
  await gotoRoute(page, '/params');
  await expect(page.getByText(/computed in /)).toBeVisible();
  expect(await page.getByTestId('disclaimer').textContent()).toBe(expected);

  await gotoRoute(page, '/');
  await expect(page.getByTestId('disclaimer')).toBeVisible();
  await disclaimer.scrollIntoViewIfNeeded();
  await shot(page, '21-api-disclaimer-verbatim.png');
});
