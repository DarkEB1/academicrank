import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { apiClient, SEED_QUERIES } from './helpers/api';
import { WEB_ORIGIN } from './helpers/env';

const here = path.dirname(fileURLToPath(import.meta.url));
export const AUTH_DIR = path.join(here, '.auth');
export const WARM_STATE = path.join(AUTH_DIR, 'warm.json');
export const WARM_PROFILE = path.join(AUTH_DIR, 'warm-profile.json');
export const SHOTS_DIR = path.join(here, 'screenshots');

export type WarmProfile = {
  profileId: string;
  token: string;
  seedIds: string[];
  seedTitles: string[];
  topPaperId: string;
  disclaimer: string;
};

/**
 * Builds one warm, five-seed profile that the whole suite (except the specs
 * that are specifically about first-run behaviour) reuses.
 *
 * Why: the first ranking for a brand new profile builds random walks on the
 * engine and takes 40-90 seconds. Paying that once here, rather than once per
 * spec file, is the difference between a suite that runs in minutes and one
 * that runs in half an hour — and it keeps every assertion about *rendering*
 * from being an assertion about engine warm-up time.
 */
async function globalSetup(): Promise<void> {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.mkdirSync(SHOTS_DIR, { recursive: true });

  const health = await apiClient.health();
  if (!health.ok || !health.graph_loaded) {
    throw new Error(
      `The Provenance stack is not ready: ${JSON.stringify(health)}. ` +
        'Start it with `docker compose up -d` and wait for /api/health to report ok:true.',
    );
  }
  // eslint-disable-next-line no-console
  console.log(
    `[global-setup] stack healthy: ${health.nodes} nodes, ${health.edges} edges in the graph`,
  );

  // Reuse a previously built warm profile when it is still valid. The engine
  // keeps its walks warm, so a repeat run costs seconds instead of a minute.
  // `E2E_FRESH_PROFILE=1` forces a rebuild.
  if (!process.env.E2E_FRESH_PROFILE && fs.existsSync(WARM_PROFILE) && fs.existsSync(WARM_STATE)) {
    try {
      const cached = JSON.parse(fs.readFileSync(WARM_PROFILE, 'utf8')) as WarmProfile;
      const me = await apiClient.me(cached.token);
      if (me.id === cached.profileId && me.trust_count >= 5) {
        // Reset the parameters in case an earlier run left a weight moved.
        await apiClient.setParams(cached.profileId, cached.token, {
          context_weights: { citation: 1, author: 1, topic: 1, venue: 1, institution: 1 },
        });
        await apiClient.rankings(cached.profileId, cached.token, { limit: 25 });
        // eslint-disable-next-line no-console
        console.log(`[global-setup] reusing warm profile ${cached.profileId} (5 seeds)`);
        return;
      }
    } catch {
      // Stale token or a rebuilt database: fall through and mint a new one.
    }
  }

  const profile = await apiClient.createProfile();
  const seedIds: string[] = [];
  const seedTitles: string[] = [];

  for (const q of SEED_QUERIES) {
    const results = await apiClient.search(q, 10);
    const pick = results.items.find((p) => !seedIds.includes(p.id));
    if (!pick) throw new Error(`No corpus results for seed query "${q}"`);
    await apiClient.setTrust(profile.id, profile.token, pick.id, 3);
    seedIds.push(pick.id);
    seedTitles.push(pick.title ?? '');
  }
  // eslint-disable-next-line no-console
  console.log(`[global-setup] warm profile ${profile.id} seeded with ${seedIds.length} papers`);

  // Warm every query the UI issues on load, so screen-level specs measure
  // rendering rather than engine cold start.
  const started = Date.now();
  const ranking = await apiClient.rankings(profile.id, profile.token, { limit: 25 });
  // eslint-disable-next-line no-console
  console.log(`[global-setup] first (cold) ranking took ${Date.now() - started}ms`);

  await apiClient.rankings(profile.id, profile.token, { limit: 20 }); // parameter playground
  await apiClient.recommendations(profile.id, profile.token, 0.35, 30);
  await apiClient.subgraph(profile.id, profile.token, 1000);

  const topPaperId = ranking.items[0]?.id;
  if (!topPaperId) throw new Error('The warm profile produced an empty ranking.');
  await apiClient.paper(profile.id, profile.token, topPaperId);
  await apiClient.subgraph(profile.id, profile.token, 500, topPaperId);
  await apiClient.explain(profile.id, profile.token, topPaperId);

  const warm: WarmProfile = {
    profileId: profile.id,
    token: profile.token,
    seedIds,
    seedTitles,
    topPaperId,
    disclaimer: ranking.disclaimer,
  };
  fs.writeFileSync(WARM_PROFILE, JSON.stringify(warm, null, 2));

  // Playwright storage state. The app reads its bearer token out of
  // localStorage, so injecting it here is exactly what a returning visitor has.
  fs.writeFileSync(
    WARM_STATE,
    JSON.stringify(
      {
        cookies: [],
        origins: [
          {
            origin: WEB_ORIGIN,
            localStorage: [
              { name: 'provenance.token', value: profile.token },
              { name: 'provenance.profileId', value: profile.id },
              { name: 'provenance.theme', value: 'light' },
            ],
          },
        ],
      },
      null,
      2,
    ),
  );
  // eslint-disable-next-line no-console
  console.log(`[global-setup] warm state written to ${WARM_STATE}`);
}

export default globalSetup;
