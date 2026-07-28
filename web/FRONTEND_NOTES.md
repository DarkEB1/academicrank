# Frontend decisions the API contract did not settle

Every item here is a judgement call made while building against
`API_CONTRACT.md` v1. Where the contract was silent, the choice below is the one
in the code, with the reasoning. Anything that turns out to be wrong should be
fixed in the contract first and here second.

---

## 1. Percentiles have no stated range

`GET /profiles/{id}/papers/{pid}` types `percentiles` as
`{ trust: number, global: number, citations: number }` without saying whether
they are 0–1 or 0–100.

**Decision:** `normalisePercentile()` accepts either — values `> 1` are divided
by 100 — and everything downstream works in 0–1. This is ambiguous only for a
true 1.0 (100th percentile) versus 1%. A genuine 1st-percentile value expressed
as `1` will be read as the 100th. If the backend settles on one convention,
delete the branch. `src/lib/format.ts`.

## 2. `disagreement` range assumed 0–1

The contract says `disagreement: number; // 0..1`. The percentile normaliser is
reused for display, so a value above 1 clamps rather than overflowing its bar.
Band thresholds (concordant / mild / notable / stark at 0.2 / 0.45 / 0.7) are a
presentation choice, not from the contract.

## 3. `delta_rank` sign convention

`POST /simulate` returns `moved: { delta_rank }` without defining the sign.

**Decision:** a *positive* `delta_rank` is read as "the rank number went up",
i.e. the paper moved **down** the list. The UI therefore renders `-delta_rank`
with an up arrow for a rise. If the backend means the opposite, flip the
comparison in `src/components/SimulationPreview.tsx` — it is one line.

## 4. No abstract in the contract

The paper screen was specified to render an abstract with KaTeX, but
`PaperBrief` has no abstract field and neither does `PaperDetail`.

**Decision:** `PaperDetail.paper.abstract?: string | null` is typed as an
optional extension. It renders (with KaTeX) when the server sends it. When it is
absent the screen says so plainly and explains why — no placeholder text and no
fabricated summary. `src/lib/types.ts`, `src/routes/PaperView.tsx`.

## 5. Parameter slider ranges

`POST /params` accepts `alpha`, `epoch_half_life_years` and `num_walks` with no
stated bounds.

**Decision (presentation only):** `alpha` 0–1 step 0.01; `epoch_half_life_years`
0.5–50 step 0.5; `num_walks` 100–10 000 step 100; context weights 0–1 step 0.01.
Sliders are rendered **only** for parameters the server actually reports in
`profiles/me.params`. If the server reports none, the screen says there is
nothing to tune rather than inventing defaults. `src/routes/Params.tsx`.

## 6. 422 on `/params` disables the control

The contract states that parameters the engine does not honour are rejected with
422 rather than silently ignored. The UI takes that literally: on a 422 the
scalar controls in that request are disabled and labelled as not honoured, so a
dead knob never looks live. The payload does not identify *which* parameter was
rejected, so all scalars in the failed request are marked. Narrow this once the
error body names the field.

## 7. Sorting is client-side and page-local

`/rankings` exposes no sort parameter. Column sorting therefore reorders the
loaded page only, and the table says so. Tie brackets are drawn **only** in rank
order, because a tie group is a claim about adjacent ranks; sorting by year and
still drawing brackets would assert something the data does not.

## 8. Tie groups are assumed contiguous

`groupTies()` builds runs from contiguous equal `tie_group` values. If a group is
ever interrupted, the UI renders two separate runs and prints a warning under the
table rather than bracketing across unrelated rows. `hasNonContiguousGroups()`
detects it. `src/lib/ties.ts`.

## 9. Precision is bounded by the error bar

`formatScore(value, stderr)` prints one digit past the leading digit of the
standard error: `0.0143271 ± 0.004` renders as `0.014 ± 0.004`. Printing the
remaining digits would assert precision the estimate does not have. Values below
the resolution render as `<0.001` rather than `0.000`.

## 10. Error bars share one domain per page

`domainFor()` computes a single min/max across all visible intervals so that
bars on the same screen are comparable. It is therefore a *relative* scale:
a bar near the right edge is the highest **on this page**, not in the corpus.
The graph explorer's colour ramp works the same way and says so in its legend.

## 11. Hash routing

`main.tsx` uses `HashRouter`, and Vite is configured with `base: './'`.

**Reason:** the brief requires the build to work when served statically. With
history routing, a deep link like `/paper/W123` 404s on any static server without
a rewrite rule. Hash routing plus relative assets works from any directory, any
subpath, and `npx serve dist` with no configuration. Switch to `BrowserRouter`
and `base: '/'` if the deployment target grows an SPA fallback.

## 12. Anonymous session bootstrap is module-scoped

React 18 StrictMode invokes effects twice in development. A naive bootstrap
either creates two anonymous profiles or deadlocks behind an in-flight guard —
the second was observed and fixed. The pending promise now lives at module scope
so both invocations join the same request, and failures are not cached so retry
works. `src/lib/session.tsx`.

## 13. Seed count comes from the trust set, not the profile

`profiles/me.trust_count` is fetched once at bootstrap and goes stale the moment
a seed is added. Everything that gates on "do you have seeds yet" reads
`useSeedCount()`, which derives the count from the live trust-set query. The
`cold_start.seeds` figure returned by `/rankings` still takes precedence on that
screen, since it is what the server actually scored against.

## 14. TeX rendering is applied to server strings, not just titles

OpenAlex titles carry inline TeX in several conventions (`$…$`, `\(…\)`,
`$$…$$`, `\[…\]`). `renderMathToHtml()` splits a string into text and maths
segments, HTML-escapes the text, renders the maths with KaTeX
(`trust: false`, `throwOnError: false`), and joins. Malformed TeX falls back to
showing the source verbatim rather than dropping content. This is applied to
titles, abstracts, path sentences, recommendation `reason` strings, and graph
node labels in panels. It is **not** applied to sigma.js node labels, which are
drawn into WebGL and cannot contain markup.

## 15. Relation vocabulary is open

`PathEdge.relation` and `SubgraphEdge.relation` are untyped strings. Known
relations get a natural-language phrase and a legend colour; unknown ones are
humanised (`about_topic` → "about topic") and given a neutral colour, so a new
relation family degrades rather than breaking. `src/lib/paths.ts`,
`src/lib/graphColors.ts`.

## 16. Graph layout runs in bursts, not a worker

ForceAtlas2 is run synchronously in short bursts between animation frames, with
Barnes-Hut enabled above 800 nodes and the iteration budget scaled down as the
node count rises. A layout worker would be marginally smoother but adds a second
bundle entry point; bursts keep the main thread responsive and let the layout be
watched as it settles. Labels are suppressed below a size threshold and edges are
hidden while panning above 2 000 nodes.

The canvas is not keyboard-operable — WebGL nodes are not focusable DOM elements.
A parallel node list beneath the canvas exposes the same selection and focus
actions to keyboard and screen-reader users.

## 17. Tooltips are `sr-only` when closed

An invisible-but-laid-out tooltip widened the document and forced horizontal
scroll at narrow viewports. Tooltip content is now rendered `sr-only` for
assistive technology and mounted visually only while open. Where the trigger
already carries an equivalent accessible name — the error bars, which describe
their own value and interval — the tooltip is `visualOnly` so screen-reader users
do not hear the same figures twice on every row.

## 18. What is not built

- `/blindspots` and `/diversity` have typed clients and query hooks
  (`useBlindspots`, `useDiversityProfile`) but no screen of their own. They were
  not in the six specified screens; the hooks are there for whoever adds them.
- There is no offline or optimistic mode. A trust change round-trips to the
  server before the UI reflects it, because the server re-warms the walks and any
  optimistic score would be a guess.
