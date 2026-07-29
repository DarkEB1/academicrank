import { expect, test } from '@playwright/test';
import { gotoRoute, shot } from '../helpers/app';

/**
 * Keyboard navigation on the rankings screen. Tab from the top of the document
 * must reach the primary controls: the skip link, the primary nav, the command
 * palette, the theme toggle, the filters, and the per-row Explain action.
 */
test('Tab reaches the primary controls on the rankings screen', async ({ page }) => {
  await gotoRoute(page, '/');
  await expect(page.getByRole('button', { name: /^Explain the score for/ }).first()).toBeVisible();

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  const seen: string[] = [];
  for (let i = 0; i < 45; i++) {
    await page.keyboard.press('Tab');
    const descriptor = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return 'BODY';
      const label =
        el.getAttribute('aria-label') ??
        el.getAttribute('title') ??
        (el.id ? document.querySelector(`label[for="${el.id}"]`)?.textContent : null) ??
        el.textContent ??
        '';
      return `${el.tagName.toLowerCase()}|${label.trim().replace(/\s+/g, ' ').slice(0, 60)}`;
    });
    seen.push(descriptor);
    if (/^button\|Explain the score for/.test(descriptor)) break;
  }

  // eslint-disable-next-line no-console
  console.log('[keyboard] tab order:\n' + seen.map((s, i) => `  ${i + 1}. ${s}`).join('\n'));

  const joined = seen.join('\n');
  const required: [string, RegExp][] = [
    ['skip to content link', /^a\|Skip to content$/m],
    ['primary nav — Rankings', /^a\|Rankings$/m],
    ['primary nav — Trust set', /^a\|Trust set$/m],
    ['primary nav — Graph', /^a\|Graph$/m],
    ['primary nav — Parameters', /^a\|Parameters$/m],
    ['command palette button', /^button\|Open command palette$/m],
    ['theme toggle', /^button\|Switch to (dark|light) theme$/m],
    ['context filter', /^select\|Context$/m],
    ['rows per page filter', /^select\|Rows per page$/m],
    ['hide-my-seeds checkbox', /^input\|/m],
    ['a column sort control', /^button\|(Rank|Year|Cited|Trust|Disagree)/m],
    ['a row Explain button', /^button\|Explain the score for/m],
  ];

  const missing = required.filter(([, re]) => !re.test(joined)).map(([name]) => name);
  expect(missing, `Tab never reached: ${missing.join(', ')}\n\nTab order was:\n${joined}`).toEqual(
    [],
  );

  // The focused Explain button must be operable from the keyboard.
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Explanation' })).toBeVisible();
  await shot(page, '22-keyboard-explain-via-keyboard.png');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Explanation' })).toHaveCount(0);
});

test('the skip link jumps to main content', async ({ page }) => {
  await gotoRoute(page, '/');
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#main$|#\/.*$/);
});
