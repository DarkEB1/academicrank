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
  const panel = page.getByRole('dialog', { name: 'Explanation' });
  await expect(panel).toBeVisible();
  // Wait for the explanation itself, not just the empty drawer, so the
  // screenshot shows something worth looking at.
  await expect(panel.getByRole('heading', { name: 'How the trust arrives' })).toBeVisible();
  await shot(page, '22-keyboard-explain-via-keyboard.png');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Explanation' })).toHaveCount(0);
});

/**
 * Regression test for the skip link. It used to be a bare `href="#main"`,
 * which under HashRouter is read as the route "main" and falls through the
 * catch-all to "/" — so on any sub-route the skip link threw you back to the
 * rankings screen and left focus on <body>. See E2E_NOTES.md.
 */
test('the skip link moves focus to main without changing route', async ({ page }) => {
  await gotoRoute(page, '/trust');
  await expect(page.getByRole('heading', { name: 'Your trust set (5)' })).toBeVisible();

  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  // sr-only until focused, then visible: that is the whole point of a skip link.
  await expect(skip).toBeVisible();

  await page.keyboard.press('Enter');

  await expect(page, 'the skip link must not navigate away from the current screen').toHaveURL(
    /#\/trust$/,
  );
  await expect(page.getByRole('heading', { name: 'Your trust set (5)' })).toBeVisible();
  expect(
    await page.evaluate(() => document.activeElement?.id),
    'focus must land on the main landmark',
  ).toBe('main');
});
