# E2E notes — findings, decisions, caveats

Everything in this file was learned by running the suite against the live stack.
Nothing here is speculative.

---

## 1. Application bug found and fixed: the skip link broke navigation

**Where:** `web/src/components/AppShell.tsx`

**Symptom.** The "Skip to content" link was a plain `<a href="#main">`. The app
uses `HashRouter`, so the location *is* the hash. Activating the skip link set
`location.hash = "#main"`, which react-router parsed as the route `main`, which
fell through `<Route path="*" element={<Navigate to="/" replace />} />`.

Measured, on `/#/trust`:

```
URL before: http://127.0.0.1:5173/#/trust
URL after : http://127.0.0.1:5173/#/          <- thrown back to Rankings
focused   : BODY                              <- focus never reached <main>
```

So the one control specifically intended for keyboard and screen-reader users
destroyed their position in the app and did not move focus. On the rankings
screen it looked like it worked, because "/" is where the redirect lands.

**Fix (minimal, 2 places in 1 file):**

1. Added an `onClick` that calls `preventDefault()` and focuses `#main`
   directly, so the route is left alone.
2. Added `tabIndex={-1}` (plus `focus:outline-none`) to `<main id="main">` so it
   can receive programmatic focus without joining the tab order.

The `href="#main"` is kept so the link still announces as a skip link.

**Verified by:** `tests/08-keyboard.spec.ts` → *"the skip link moves focus to
main without changing route"*, which asserts the URL still ends `#/trust`, the
trust-set screen is still on screen, and `document.activeElement.id === 'main'`.

The `web` container was rebuilt (`docker compose up -d --build web`) so the
running build contains the fix. Note that this recreated the sibling containers
too; the stack returned healthy (`graph_loaded: true`, ~111.5k nodes) about
20 seconds later, and the whole suite was re-run green afterwards.

---

## 2. A stray `vite dev` server was shadowing the stack under test

Not an application bug, but it invalidates results silently, so it is worth
recording.

`docker compose` publishes the `web` container on `0.0.0.0:5173` — **IPv4 only**.
A leftover `npm run dev` in `web/` was listening on `[::1]:5173`. On Windows
`localhost` resolves to `::1` first, so `http://localhost:5173` served the *Vite
dev server* — a different artefact from the nginx-served production build the
acceptance gate is supposed to cover.

The tell was a console error the production build does not produce, plus
`/@vite/client` in the served HTML.

**Decision:** `baseURL` is `http://127.0.0.1:5173` (see `helpers/env.ts`), which
is unambiguous. Override with `PROVENANCE_WEB` to point somewhere else on
purpose.

---

## 3. Ignored console noise

`tests/02-screens.spec.ts` fails on *any* console error or uncaught exception,
with two documented exceptions (`helpers/app.ts` → `CONSOLE_NOISE`):

| Pattern | Why it is not an app fault |
| --- | --- |
| SwiftShader / software WebGL / GLES3 context messages | Headless Chromium renders WebGL in software. Emitted by the browser, not the page. |
| `favicon.ico` 404 | Chrome requests `/favicon.ico` unprompted; the app ships no favicon so nginx answers 404. The request is the browser's, not the application's. |

Everything else fails the test. Console messages are recorded with their
`location().url` appended, because "Failed to load resource: 404" on its own is
undiagnosable.

**Result: all six routes are clean.** No React warnings, no key warnings, no
unhandled rejections on `/#/`, `/#/trust`, `/#/recommendations`, `/#/graph`,
`/#/params` or `/#/paper/:id`.

---

## 4. Proving the WebGL graph is not blank

`preserveDrawingBuffer` defaults to `false`, so a WebGL canvas cannot normally be
read back with `drawImage`/`getImageData` after its frame is composited — the
pixel check would always come back zero and prove nothing.

`tests/03-graph-webgl.spec.ts` therefore uses `page.addInitScript` to wrap
`HTMLCanvasElement.prototype.getContext` and force
`preserveDrawingBuffer: true` on webgl/webgl2 contexts. This is test
instrumentation injected before any application script runs; **application
source is untouched.**

Measured output of a passing run:

```
sigma-edges       890x556  webgl2  19351 painted px
sigma-edgeLabels  890x556  2d          0
sigma-nodes       890x556  webgl2  34774 painted px
sigma-labels      890x556  2d        374
sigma-hovers      890x556  2d          0
sigma-hoverNodes  890x556  webgl2      0
sigma-mouse       890x556  2d          0
```

So the assertion is genuine: a real WebGL2 context, at real dimensions, with
~35k non-transparent pixels of nodes drawn into it.

---

## 5. Why the suite shares one warm profile

**The first ranking for a new profile takes 40–90 seconds** (measured: 40.6s,
52.4s, 56.7s, 71.1s across runs). That is the engine building random walks for
the new ego node, and it is legitimate behaviour, not a bug.

`global-setup.ts` pays it once, then hands every spec the same bearer token via
`storageState`. Only the two specs that are *about* first-run behaviour —
`01-journey.spec.ts` and the "below five seeds" half of `06-cold-start.spec.ts` —
start from an empty browser and mint their own profile.

Re-runs reuse the profile if its token still resolves and it still has ≥5 seeds,
and reset its context weights to 1.0 first (so a previous run's slider move
cannot leak into the next run). `E2E_FRESH_PROFILE=1` forces a rebuild.

### Timeouts had to be raised beyond the original 240s

An early full run was green end to end. A later run failed two tests — journey
step 5 and the `/params` screen check — both at exactly the 120s `expect`
timeout, with no assertion actually disproved. The cause was environmental: the
containers had been recreated (see §1) and other work was hitting the same
engine, so re-rankings that normally take 50-90s were taking minutes.

Timeouts are therefore:

| Setting | Value | Why |
| --- | --- | --- |
| `timeout` (per test) | 420s | A test may pay for more than one cold ranking. |
| `expect.timeout` | 180s | One cold ranking, with headroom. |
| `actionTimeout` / `navigationTimeout` | 120s | |
| journey step 5 | 600s via `test.setTimeout` | Pays for **two** full re-rankings: one when the screen writes current params back on mount, one for the weight being moved. |
| step 5's ranking-changed poll | 420s | |

Step 5 also now asserts that the app shows *"Saving parameters…" / "Parameters
saved."* before it starts waiting on the engine. Without that, a timeout was
ambiguous between "the UI never sent the change" and "the engine was slow" —
which are completely different bugs.

**The engine is a shared, single, stateful process.** If anything else is
exercising the API while this suite runs, expect slow rankings. That is not
flakiness in the tests; it is contention, and the timeouts now absorb it.

### A real robustness flaw this exposed in the test itself

A later run failed step 5 with `Received array: []`. Two things were wrong, one
environmental and one mine:

* **Environmental:** the `api` container was restarted by other work *while the
  test was waiting on a re-rank* (`docker compose ps` showed `api Up 2 minutes`
  against `db/mr-service/web Up 34 minutes`). The in-flight request died and the
  UI correctly displayed *"The API is not answering"*. Nothing was wrong with
  the application.
* **Mine:** the poll compared the rendered top-20 against the baseline and
  stopped as soon as it *differed*. An empty list differs from a full one, so a
  transient re-render — or an error state — satisfied the most important
  assertion in the suite **trivially**. It would have reported a pass for the
  wrong reason had the ordering not genuinely changed.

Both are fixed. The poll now treats a list shorter than 10 rows as "not yet"
and keeps waiting, and it detects the API error state explicitly so that a
stack failure is reported as a stack failure rather than as a silent timeout or
a false positive.

This is worth stating plainly: **the guard matters more than the assertion.** An
inequality check against a live, asynchronous list is only meaningful if the
"changed" state is also a *valid* state.

---

## 6. The parameter-playground assertion, and why it is trustworthy

This is the assertion the brief calls the most important, so it is worth being
precise about what it proves.

* Rankings for **identical** parameters are **deterministic**. Verified directly
  against the API: five consecutive calls with unchanged weights returned a
  byte-identical top-20 every time. So a change in the ordering cannot be
  dismissed as sampling noise.
* The test moves the **`author` context weight from 1.00 to 0.00** using
  `press('Home')` on the native range input — a real keyboard gesture, not a
  synthetic value assignment. The rendered readout is asserted to change to
  `0.00`, so the app really did register it.
* Measured effect: **20 of 20 top-20 positions changed.**
* The assertion is `after !== before` on the *ordered* list of paper ids scraped
  from the DOM, plus a floor of ≥2 moved positions.

Which weight to move was chosen by probing the API first. Not all of them bite
for a fresh trust set:

| weight moved | top-20 changed? | positions differing |
| --- | --- | --- |
| `author` → 0 | yes | 20 |
| `author` → 0.5 | yes | 19 |
| `institution` → 0 | yes | 15 |
| `topic` → 0 | yes | 13 |
| `topic` → 0.5 | yes | 2 |
| `citation` → 0 or 0.5 | **no** | 0 |
| `venue` → 0 or 0.5 | **no** | 0 |

`citation` and `venue` being inert is not a UI fault — the API accepts the write
and returns 200, the engine simply produces the same ordering. It may be
expected (the citation backbone is present in every context by construction) or
it may be worth a look; either way the suite deliberately does **not** assert on
those two, and this table is the reason.

---

## 7. Tie groups and error bars

Both are asserted on the rankings screen, and cross-checked against the server
payload rather than trusted from the DOM alone:

* **Error bars.** Every scored row renders a `ScoreBar` with
  `role="img"` and an accessible name of the form
  `Trust 0.023 plus or minus 0.024, 95% interval 0.000–0.070 over 5 samples`.
  The test asserts one interval per row and matches that shape.
* **Tie groups.** The table prints `N statistically tied — order below is
  arbitrary` above each contiguous run sharing a `tie_group`. The test requires
  at least one such run *and* confirms from the API response that at least one
  `tie_group` value is genuinely shared between items.

Observed on a fresh five-seed profile: tie groups `0,0,1,1,2×10,3×6` over a
top-20 — i.e. 4 distinguishable positions in 20 rows. The UI reports this
honestly ("These 25 rows resolve to 1 statistically distinguishable position").

---

## 8. Things that could not be asserted more strongly

Recorded rather than dropped.

* **Explain paths depend on the trust set.** The journey asserts that at least
  one contributing path links back to one of the five papers *this run* trusted,
  by matching the `from seed` link's `href` against the seed ids returned by
  `GET /trust`. It cannot assert *which* seed, or how many paths, because both
  are engine outputs that legitimately vary with the corpus slice. Observed: 2
  paths, both from the same seed, accounting for 100% of the listed weight.
* **`hasNonContiguousGroups`.** The rankings screen has a branch for a server
  anomaly (a tie group interrupted mid-page). It has never fired in any run, so
  there is no test for it — asserting a branch we cannot provoke would be
  theatre.
* **Distrust edges, BibTeX import, the simulate/"Preview impact" dialog** are
  reachable in the UI and exercised by the app's own unit tests, but are outside
  the journey the brief specifies. Not covered here.
* **The graph explorer's node click → re-centre** interaction is not asserted:
  clicking a specific node means computing its screen position out of the sigma
  camera, which would be a brittle test of the renderer's internals rather than
  of the product. The keyboard-accessible node list, which does the same thing,
  *is* asserted instead.
