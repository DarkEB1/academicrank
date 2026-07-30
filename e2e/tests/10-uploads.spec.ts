import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { API_BASE } from '../helpers/env';
import { EMPTY_STATE, collectConsole, gotoRoute, sessionFromPage, shot, waitForRankingRows } from '../helpers/app';

/**
 * THE UPLOAD JOURNEY (spec 2026-07-29): upload a PDF of your own paper,
 * review the parsed bibliography, import the accepted references as trust
 * seeds, land on recommendations, and — the load-bearing assertion — a SECOND
 * profile with `include_user_uploads` off never sees the uploaded paper until
 * it opts in.
 *
 * Nothing is stubbed. The PDF is a real file, parsed by the real pdfminer
 * worker inside the api container; the 8 references carry real corpus DOIs.
 */
test.use({ storageState: EMPTY_STATE });
test.describe.configure({ mode: 'serial' });

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PDF = path.join(here, '..', 'fixtures', 'upload-e2e.pdf');
// The fixture paper's own title — deliberately un-corpus-like so the only way
// anyone can find it is through the upload itself.
const UPLOAD_TITLE = 'Playwright Upload Odyssey Nonpareil Treatise';
const N_REFS = 8;

let page: Page;
const state = { profileId: '', token: '', ownWorkId: '' };

/** Token-aware raw API call: the visibility filter is per-profile, so the
 *  anonymous apiClient helper cannot exercise it. */
async function api<T>(pathname: string, token?: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${API_BASE}${pathname}`, { ...init, headers });
  if (!res.ok) throw new Error(`API ${pathname} -> ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return (await res.json()) as T;
}

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage({ storageState: EMPTY_STATE, colorScheme: 'light' });
  collectConsole(page);
});

test.afterAll(async () => {
  await page?.close();
});

test('step 1 — a PDF becomes a reviewable draft with DOI matches pre-ticked', async () => {
  await gotoRoute(page, '/uploads');
  await expect(page.getByRole('heading', { name: 'Uploads', exact: true })).toBeVisible();

  const session = await sessionFromPage(page);
  expect(session.token).toBeTruthy();
  state.profileId = session.profileId!;
  state.token = session.token!;

  await page.getByLabel('Upload a PDF of your paper').setInputFiles(FIXTURE_PDF);

  // Server-side parse + match: 10–60s, timed out generously. The review table
  // appearing IS the draft round-trip.
  await expect(page.getByTestId('uploads-review-table')).toBeVisible({ timeout: 240_000 });
  await shot(page, '23-upload-review-table.png');

  // The paper's own (editable) title was parsed from page one of the PDF.
  await expect(page.getByText(UPLOAD_TITLE).first()).toBeVisible();

  // All 8 references matched by DOI and arrive pre-ticked; the tick count is
  // in the import button's own label.
  const rows = page.getByTestId('uploads-review-table').locator('li');
  await expect(rows).toHaveCount(N_REFS);
  for (let i = 0; i < N_REFS; i++) {
    await expect(
      page.getByLabel(`Trust reference ${i + 1}`),
      `reference ${i + 1} should be pre-ticked (DOI match)`,
    ).toBeChecked();
  }
  await expect(page.getByTestId('uploads-import-button')).toHaveText(
    new RegExp(`Import ${N_REFS} seeds at 3/5`),
  );
});

test('step 2 — import seeds the trust set and lands on recommendations', async () => {
  await page.getByTestId('uploads-import-button').click();

  // The spec sends the user to recommendations with the diversity dial raised:
  // post-upload /rankings degenerates to "the references of my references".
  await page.waitForURL(/#\/recommendations\?diversity=/, { timeout: 240_000 });
  await shot(page, '24-upload-landed-on-recommendations.png');

  const trust = await api<{ items: { work: { id: string }; strength: number }[] }>(
    `/profiles/${state.profileId}/trust`,
    state.token,
  );
  expect(trust.items.length, 'every accepted reference became a seed').toBe(N_REFS);
  for (const item of trust.items) {
    expect(item.strength, 'the default upload seed strength is 3/5').toBe(3);
  }

  // The upload is listed as imported, with undo offered.
  await gotoRoute(page, '/uploads');
  await expect(page.getByText('imported', { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('button', { name: 'Undo' })).toBeVisible();
});

test('step 3 — the seeded profile now has rankings where it had none', async () => {
  await gotoRoute(page, '/');
  // Cold first ranking for a fresh ego: 40–90s legitimately, more under load.
  await waitForRankingRows(page);
  await shot(page, '25-upload-rankings-exist.png');
});

test('step 4 — the uploader can find their own paper; nobody else can until they opt in', async ({
  browser,
}) => {
  // The uploader sees their own UL paper in search (visibility: uploader
  // always sees their own).
  const mine = await api<{ total: number; items: { id: string; title: string | null }[] }>(
    `/papers/search?q=${encodeURIComponent('Playwright Upload Odyssey')}&limit=10`,
    state.token,
  );
  const own = mine.items.find((p) => (p.title ?? '').includes('Playwright Upload Odyssey'));
  expect(own, 'the uploader must see their own paper in search').toBeTruthy();
  expect(own!.id.startsWith('L'), 'the paper is a UL-labelled local work').toBe(true);
  state.ownWorkId = own!.id;

  // A SECOND profile, fresh browser context, include_user_uploads at its
  // default (off): the paper must be invisible in the UI search.
  const watcher = await browser.newPage({ storageState: EMPTY_STATE, colorScheme: 'light' });
  collectConsole(watcher);
  await watcher.goto('/#/trust', { waitUntil: 'domcontentloaded' });
  await expect(watcher.getByRole('heading', { name: 'Trust set', exact: true })).toBeVisible();

  const searchBox = watcher.getByLabel('Search papers');
  await searchBox.fill('Playwright Upload Odyssey');
  // The search settling is observed in the DOM, not via waitForResponse (which
  // proved racy): with the toggle off the ONLY correct outcome is the corpus
  // empty state, since nothing else matches this deliberately absurd title.
  await expect(watcher.getByText('Nothing matched')).toBeVisible({ timeout: 90_000 });
  await expect(
    watcher.getByText('Playwright Upload Odyssey Nonpareil Treatise'),
    'a second profile must not see the uploaded paper',
  ).toHaveCount(0);
  await shot(watcher, '26-upload-invisible-to-second-profile.png');

  // The same watcher opts in -> the paper appears. Display-level, live.
  const watcherSession = await watcher.evaluate(() => ({
    profileId: localStorage.getItem('provenance.profileId'),
    token: localStorage.getItem('provenance.token'),
  }));
  expect(watcherSession.token).toBeTruthy();
  await api(`/profiles/${watcherSession.profileId}/params`, watcherSession.token!, {
    method: 'POST',
    body: JSON.stringify({ include_user_uploads: true }),
  });

  await searchBox.fill('');
  await searchBox.fill('Playwright Upload Odyssey Nonpareil');
  await expect(
    watcher.getByText('Playwright Upload Odyssey Nonpareil Treatise').first(),
    'after opting in, the same search finds the paper',
  ).toBeVisible({ timeout: 90_000 });
  await shot(watcher, '27-upload-visible-after-opt-in.png');

  await watcher.close();
});
