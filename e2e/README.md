# Provenance — end-to-end suite

Playwright tests that run against the **live docker-compose stack**. Nothing is
mocked: every ranking, explanation and score in these tests came out of the
MeritRank engine during the run.

## Prerequisites

The stack must already be up and healthy:

```bash
docker compose up -d
curl http://127.0.0.1:5173/api/health   # -> {"ok":true, ... ,"graph_loaded":true}
```

| Service | URL |
| --- | --- |
| Web (nginx, built React app) | http://127.0.0.1:5173 |
| API (FastAPI, OpenAPI at /docs) | http://127.0.0.1:8000 |
| Postgres | `localhost:55432` — postgres/postgres/provenance |

> **Why `127.0.0.1` and not `localhost`.** On Windows `localhost` resolves to
> `::1` first. The compose `web` service publishes port 5173 on IPv4 only, so if
> anything else is listening on `::1:5173` — a leftover `npm run dev` in `web/`,
> for example — then `http://localhost:5173` quietly serves *that* instead of the
> containerised build, and the suite ends up testing a different artefact. This
> actually happened during development. See `helpers/env.ts`.

## Install

```bash
cd e2e
npm install
npx playwright install chromium
```

## Run

```bash
cd e2e
npx playwright test              # everything
npx playwright test --reporter=list
npm run test:journey             # just the acceptance journey
npx playwright show-report       # HTML report of the last run
```

Useful switches:

```bash
E2E_FRESH_PROFILE=1 npx playwright test   # rebuild the shared warm profile
PROVENANCE_WEB=http://localhost:5173 npx playwright test   # target a dev server
npx playwright test --headed --project=chromium
```

## What is here

| Spec | Covers |
| --- | --- |
| `tests/01-journey.spec.ts` | **The acceptance gate.** Fresh profile → 5 seeds → rankings → explain → parameter playground, in order, in one browser context. |
| `tests/02-screens.spec.ts` | All six routes render with zero console errors / uncaught exceptions. |
| `tests/03-graph-webgl.spec.ts` | The graph explorer creates a real WebGL2 context and paints nodes into it. |
| `tests/04-theme.spec.ts` | Dark mode toggles, repaints, and persists across reload. |
| `tests/05-command-palette.spec.ts` | ⌘K / Ctrl+K opens the palette, lists commands, searches the corpus. |
| `tests/06-cold-start.spec.ts` | Under 5 seeds the UI says the rankings are unreliable; at 5 it stops. |
| `tests/07-disclaimer.spec.ts` | The API's `disclaimer` string is rendered verbatim, byte for byte. |
| `tests/08-keyboard.spec.ts` | Tab reaches every primary control on the rankings screen; skip link works. |
| `tests/09-no-horizontal-overflow.spec.ts` | No document-level sideways scroll at 1280px on any route. |

## Screenshots

Every step writes a full-page screenshot to `e2e/screenshots/`, numbered in
journey order. They are a deliverable, not a debugging artefact — look at them
first.

```
00-profile-created.png              05-params-after.png            12-graph-webgl-canvas.png
01-trust-set.png                    06-screen-rankings.png         13-theme-light.png
02-rankings.png                     07-screen-trust.png            14-theme-dark.png
03-explain.png                      08-screen-recommendations.png  15-command-palette-open.png
04-params-before.png                09-screen-graph.png            16-command-palette-search.png
                                    10-screen-params.png           17-cold-start-zero-seeds.png
                                    11-screen-paper-detail.png     18-cold-start-two-seeds.png
                                                                   19-cold-start-two-seeds-rankings.png
                                                                   20-cold-start-five-seeds-reliable.png
                                                                   21-api-disclaimer-verbatim.png
                                                                   22-keyboard-explain-via-keyboard.png
```

Failure screenshots and traces land in `e2e/test-results/`.

## How it is wired

* **`global-setup.ts`** builds one warm, five-seed profile via the API and writes
  its bearer token into `.auth/warm.json` (a Playwright `storageState`). Every
  spec except the journey and the cold-start half reuses it.

  The reason is cost: **the first ranking for a brand-new profile builds random
  walks on the engine and takes 40–90 seconds.** Paying that once, rather than
  once per spec file, keeps assertions about *rendering* from turning into
  assertions about engine warm-up. Re-runs reuse the profile entirely
  (`E2E_FRESH_PROFILE=1` forces a rebuild).

* **`workers: 1`, `fullyParallel: false`.** The engine is one shared stateful
  process. Concurrent profiles warming walks turn 40s into minutes.

* **Timeouts are deliberately generous**: 240s per test, 120s per action and
  navigation. That is not padding — a cold ranking really does take that long.

* **Selectors are role- and text-based.** The one `data-testid` used is
  `disclaimer`, which already existed in the application source.

* Trust seeds are chosen by running **real corpus searches** (`algebraic
  geometry`, `number theory`, `optimization`, `topology`, `graph theory`) and
  taking whatever the corpus returns. No paper id is hardcoded anywhere.

See `E2E_NOTES.md` for findings, caveats and the one application bug this suite
uncovered.
