import { expect, test } from '@playwright/test';
import { gotoRoute, shot } from '../helpers/app';

test('Ctrl+K opens the command palette and searches the corpus', async ({ page }) => {
  await gotoRoute(page, '/');
  await expect(page.getByRole('button', { name: /^Explain the score for/ }).first()).toBeVisible();

  await expect(page.getByRole('dialog', { name: 'Command palette' })).toHaveCount(0);

  // ⌘K on macOS, Ctrl+K everywhere else — the handler accepts either modifier.
  await page.keyboard.press('Control+k');

  const palette = page.getByRole('dialog', { name: 'Command palette' });
  await expect(palette).toBeVisible();

  const input = palette.getByRole('combobox');
  await expect(input).toBeFocused();

  // Navigation commands are listed before anything is typed.
  const options = palette.getByRole('option');
  expect(await options.count(), 'the palette should list navigation commands').toBeGreaterThanOrEqual(
    5,
  );
  await expect(palette.getByRole('option', { name: /Parameter playground/ })).toBeVisible();
  await shot(page, '15-command-palette-open.png');

  // Now search the real corpus.
  await input.fill('algebraic geometry');
  await expect(
    palette.getByRole('option', { name: /geometry|algebra/i }).first(),
  ).toBeVisible();

  const labels = await options.evaluateAll((els) => els.map((el) => el.textContent ?? ''));
  const paperHits = labels.filter((t) => /\d{4}$|·/.test(t));
  expect(
    paperHits.length,
    `the palette should return corpus papers, got:\n${labels.join('\n')}`,
  ).toBeGreaterThan(0);
  await shot(page, '16-command-palette-search.png');

  // Filtering also narrows the navigation commands.
  await input.fill('graph explorer');
  await expect(palette.getByRole('option', { name: /Graph explorer/ })).toBeVisible();

  // Escape dismisses.
  await page.keyboard.press('Escape');
  await expect(palette).toHaveCount(0);
});
