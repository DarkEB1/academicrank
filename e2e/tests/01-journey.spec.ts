import { expect, test, type Page } from '@playwright/test';
import { apiClient, SEED_QUERIES } from '../helpers/api';
import {
  EMPTY_STATE,
  collectConsole,
  gotoRoute,
  paramsTopTwenty,
  sessionFromPage,
  shot,
  waitForRankingRows,
} from '../helpers/app';

/**
 * THE ACCEPTANCE GATE.
 *
 * One browser context, one brand-new anonymous profile, five real seeds, and
 * the whole journey in order. Nothing is stubbed; every number on screen came
 * out of the MeritRank engine during this run.
 *
 * This spec deliberately does NOT reuse the warm profile from global-setup:
 * step 1 is "the app creates a profile on first load", which can only be
 * observed from a genuinely empty browser.
 */
test.use({ storageState: EMPTY_STATE });
test.describe.configure({ mode: 'serial' });

type JourneyState = {
  profileId: string;
  token: string;
  seedIds: string[];
  seedTitles: string[];
  topPaperId: string;
};

let page: Page;
const state: JourneyState = {
  profileId: '',
  token: '',
  seedIds: [],
  seedTitles: [],
  topPaperId: '',
};

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage({ storageState: EMPTY_STATE, colorScheme: 'light' });
  collectConsole(page);
});

test.afterAll(async () => {
  await page?.close();
});

test('step 1 — the app creates an anonymous profile on first load', async () => {
  await gotoRoute(page, '/');

  // The masthead statement is the app's own proof it has booted.
  await expect(
    page.getByText(
      'Provenance measures proximity in a weighted trust graph. It does not measure quality, correctness, or importance.',
    ),
  ).toBeVisible();

  // With no seeds the rankings screen must say so rather than invent an order.
  await expect(page.getByRole('heading', { name: 'Nothing to rank yet' })).toBeVisible();

  const session = await sessionFromPage(page);
  expect(session.profileId, 'the app should have stored a profile id').toBeTruthy();
  expect(session.token, 'the app should have stored a bearer token').toBeTruthy();
  state.profileId = session.profileId!;
  state.token = session.token!;

  // ...and that identity must be real on the server, not a client-side fiction.
  const me = await apiClient.me(state.token);
  expect(me.id).toBe(state.profileId);
  expect(me.trust_count).toBe(0);

  await shot(page, '00-profile-created.png');
});

test('step 2 — add five trusted papers through the trust set builder', async () => {
  await gotoRoute(page, '/trust');

  await expect(page.getByRole('heading', { name: 'Trust set', exact: true })).toBeVisible();
  await expect(page.getByText('No seeds yet — nothing can be ranked')).toBeVisible();

  const searchCard = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Find papers to seed' }) });
  const searchBox = page.getByLabel('Search papers');

  for (let i = 0; i < SEED_QUERIES.length; i++) {
    const query = SEED_QUERIES[i];

    await searchBox.fill(query);
    // Real corpus results, not fixtures: wait for the match count line the app
    // only prints once the API has answered.
    await expect(searchCard.getByText(/\d[\d,]* match(es)?/)).toBeVisible();

    const addable = searchCard.getByRole('button', { name: 'Add' });
    await expect(addable.first()).toBeVisible();
    await addable.first().click();

    await expect(
      page.getByRole('heading', { name: `Your trust set (${i + 1})` }),
      `trust set should hold ${i + 1} papers after adding from "${query}"`,
    ).toBeVisible();
  }

  // Verify against the server rather than against the DOM we just drove.
  const trust = await apiClient.listTrust(state.profileId, state.token);
  const trusted = trust.items.filter((entry) => !entry.is_distrust);
  expect(trusted, 'five trusted papers must exist server-side').toHaveLength(5);
  state.seedIds = trusted.map((entry) => entry.work.id);
  state.seedTitles = trusted.map((entry) => entry.work.title ?? '');

  // The cold-start notice must be gone at five seeds.
  await expect(page.getByText(/rankings are unreliable/)).toHaveCount(0);
  await expect(page.getByText('With 5 seeds the ranking is usable.')).toBeVisible();

  await shot(page, '01-trust-set.png');
});

test('step 3 — rankings render real results with error bars and a tie group', async () => {
  await gotoRoute(page, '/');

  // The first ranking for a cold profile builds random walks on the engine.
  const explainButtons = await waitForRankingRows(page);
  const rowCount = await explainButtons.count();
  expect(rowCount, 'the ranking should return a full page of results').toBeGreaterThanOrEqual(10);

  // --- error bars -------------------------------------------------------
  // Every score is drawn as an interval with a point estimate; the accessible
  // name carries the 95% interval, so its presence is the error bar's presence.
  const errorBars = page.getByRole('img', { name: /95% interval/ });
  expect(await errorBars.count(), 'every scored row must carry an interval').toBe(rowCount);
  await expect(errorBars.first()).toHaveAttribute(
    'aria-label',
    /Trust .* plus or minus .*, 95% interval .* over \d+ samples/,
  );

  // --- tie groups -------------------------------------------------------
  const tieHeaders = page.getByText(/^\d+ statistically tied — order below is arbitrary$/);
  expect(
    await tieHeaders.count(),
    'at least one group of statistically indistinguishable papers must be marked',
  ).toBeGreaterThanOrEqual(1);

  await expect(page.getByText(/statistically distinguishable position/)).toBeVisible();

  // Cross-check the UI against what the server actually said.
  const server = await apiClient.rankings(state.profileId, state.token, { limit: 25 });
  const groups = server.items.map((item) => item.uncertainty.tie_group);
  const grouped = groups.filter((g, i) => groups.indexOf(g) !== i);
  expect(grouped.length, 'server must report at least one shared tie group').toBeGreaterThan(0);
  state.topPaperId = server.items[0].id;

  await shot(page, '02-rankings.png');
});

test('step 4 — the explain panel shows a path from a paper we trusted', async () => {
  await gotoRoute(page, '/');
  const explainButtons = await waitForRankingRows(page);
  await explainButtons.first().click();

  const panel = page.getByRole('dialog', { name: 'Explanation' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('heading', { name: 'How the trust arrives' })).toBeVisible();

  // Path count the app prints from the server payload.
  await expect(panel.getByText(/\d+ paths? shown/)).toBeVisible();

  const seedLinks = panel.getByRole('link', { name: 'from seed' });
  const pathCount = await seedLinks.count();
  expect(pathCount, 'at least one contributing path must be reconstructed').toBeGreaterThanOrEqual(
    1,
  );

  const hrefs = await seedLinks.evaluateAll((els) =>
    els.map((el) => (el as HTMLAnchorElement).getAttribute('href') ?? ''),
  );
  const referenced = hrefs.map((href) => href.split('#/paper/')[1] ?? '');
  const intersection = referenced.filter((id) => state.seedIds.includes(id));
  expect(
    intersection,
    `no contributing path referenced one of the papers we trusted.\n` +
      `paths referenced: ${referenced.join(', ')}\ntrusted: ${state.seedIds.join(', ')}`,
  ).not.toHaveLength(0);

  // The seed's title must be printed in the path card too, not just linked.
  const trustedTitle = state.seedTitles[state.seedIds.indexOf(intersection[0])];
  if (trustedTitle) {
    await expect(panel.getByText(trustedTitle.slice(0, 40), { exact: false }).first()).toBeVisible();
  }

  await shot(page, '03-explain.png');
});

test('step 5 — moving a context weight slider changes the top-20 ranking', async () => {
  await gotoRoute(page, '/params');

  await expect(page.getByRole('heading', { name: 'Parameter playground' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Context weights' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Top twenty, live' })).toBeVisible();

  // Wait for the live top-20 to have settled (the screen re-ranks once on mount
  // when it writes the current parameters back).
  await expect(page.getByText(/computed in /)).toBeVisible();
  await expect(page.getByText('re-ranking…')).toHaveCount(0);

  const before = await paramsTopTwenty(page);
  expect(before.length, 'the playground should list a top twenty').toBeGreaterThanOrEqual(10);
  await shot(page, '04-params-before.png');

  // The `author` weight is the one the engine is most sensitive to for a fresh
  // trust set: authorship edges are how trust reaches a paper's other work.
  const slider = page.locator('#w-author');
  await expect(slider).toBeVisible();
  await expect(slider).toHaveValue('1');

  // Home on a native range input is the keyboard route to its minimum. This is
  // a real user gesture, not a synthetic value assignment.
  await slider.focus();
  await slider.press('Home');
  await expect(slider).toHaveValue('0');
  await expect(page.locator('#w-author').locator('xpath=../div/span')).toHaveText('0.00');

  // Debounce (350ms) -> POST /params -> invalidate -> refetch. Poll the DOM.
  await expect
    .poll(async () => (await paramsTopTwenty(page)).join('|'), {
      message: 'the top-20 ranking did not change after adjusting the author context weight',
      timeout: 120_000,
      intervals: [500, 1000, 2000],
    })
    .not.toBe(before.join('|'));

  await expect(page.getByText('re-ranking…')).toHaveCount(0);
  const after = await paramsTopTwenty(page);

  // THE assertion this suite exists for: personalisation is live, not decorative.
  expect(after, 'top-20 must be a real list').not.toHaveLength(0);
  expect(after.join('|'), 'the ordered top-20 must differ after moving a weight').not.toBe(
    before.join('|'),
  );

  const moved = after.filter((id, i) => before[i] !== id).length;
  // eslint-disable-next-line no-console
  console.log(`[journey] ${moved}/${after.length} top-20 positions changed after author -> 0.00`);
  expect(moved, 'a meaningful part of the ordering should move, not one row').toBeGreaterThanOrEqual(
    2,
  );

  // At least one paper should be flagged as new relative to the baseline the
  // screen captured when it opened.
  await expect(page.getByText('new', { exact: true }).first()).toBeVisible();

  await shot(page, '05-params-after.png');
});
