import { expect, test } from '@playwright/test';
import { gotoRoute, shot } from '../helpers/app';

test('the dark mode toggle switches themes and persists the choice', async ({ page }) => {
  await gotoRoute(page, '/');
  await expect(page.getByRole('button', { name: /^Explain the score for/ }).first()).toBeVisible();

  const html = page.locator('html');
  await expect(html).not.toHaveClass(/dark/);
  await shot(page, '13-theme-light.png');

  const toDark = page.getByRole('button', { name: 'Switch to dark theme' });
  await expect(toDark).toBeVisible();
  await toDark.click();

  await expect(html).toHaveClass(/dark/);
  await expect(page.getByRole('button', { name: 'Switch to light theme' })).toBeVisible();

  // The theme must actually change the painted colours, not just a class name.
  const darkBg = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor || getComputedStyle(document.documentElement).backgroundColor,
  );
  await shot(page, '14-theme-dark.png');

  // Persisted, so a reload does not flash back to light.
  expect(await page.evaluate(() => localStorage.getItem('provenance.theme'))).toBe('dark');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(html).toHaveClass(/dark/);

  await page.getByRole('button', { name: 'Switch to light theme' }).click();
  await expect(html).not.toHaveClass(/dark/);
  const lightBg = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor || getComputedStyle(document.documentElement).backgroundColor,
  );
  expect(lightBg, 'light and dark must paint different backgrounds').not.toBe(darkBg);
});
