# Provenance

**Given the papers you already trust, how much should you trust this one?**

Provenance answers that question *subjectively* and *per user*. There is no global
ranking, no impact factor, no h-index. You declare a set of papers you trust, and the
system runs [MeritRank](https://arxiv.org/html/2207.09950v2) over a heterogeneous graph
of mathematics literature seeded from your declarations.

The number it gives you is **proximity in a weighted trust graph**. It is not a measure
of quality, correctness, or importance, and the interface says so in those words.

---

## What it does

- **Trust set builder** — search the corpus, add papers at strength 1–5, mark distrust,
  import a `.bib` file. Below five seeds it tells you the rankings are unreliable
  instead of pretending otherwise.
- **Personalised rankings** — every score carries an error bar, and papers that are
  statistically tied are shown as tied rather than given a spurious order.
- **Explanations** — the actual contributing paths from your trust set to a target:
  *"Terence Tao, whose work you trust, cites this, and it shares 14 references with two
  other papers you trust."* A trust score with no derivation is astrology.
- **Recommendations with a diversity dial** — from exploitation (nearest your trust set)
  to exploration (high merit, far away).
- **Comparison strip** — your trust score vs. unpersonalised merit vs. raw citation
  count. Papers where these three sharply disagree are the most interesting objects in
  the system, and they are surfaced deliberately.
- **Parameter playground** — live per-context weights with the top-20 re-ranking as you
  drag. This is the screen that shows it isn't a black box.
- **Graph explorer** — WebGL (sigma.js), coloured by score, filterable by relation.

---

## Running it

```bash
cp .env.example .env        # add your OpenAlex API key
docker compose up --build   # builds two Rust images; first build is slow
```

Then open **http://localhost:5173**. The API is on `:8000` (`/docs` for OpenAPI), the
database on `:55432`, and `mr-service` on `:10234`.

The committed dataset boots without network access. To rebuild the corpus from OpenAlex:

```bash
python scripts/scrape.py          # disk-cached; a warm cache makes this a no-op
bash   scripts/rebuild_all.sh     # load -> stats -> build graph -> divergence check
```

> **Port note:** the database is published on **55432**, not 5432, because a
> host-installed PostgreSQL commonly occupies 5432 and silently shadows the container.

---

## Architecture

```
                 ┌──────────────┐
   OpenAlex ───► │  scrape.py   │  disk cache, credit-aware, resumable
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │  load_db.py  │  normalised tables + raw JSONB
                 └──────┬───────┘
                        ▼
                 ┌───────────────┐   typed nodes, hub damping, IDF, epoch factor
                 │ build_graph.py│──────────────────────────┐
                 └──────┬────────┘                          │
                        │ mr_bulk_load_edges                ▼
                        ▼                            ┌─────────────┐
   ┌──────────┐   ┌───────────┐   TCP 10234          │ graph_edges │
   │   web    │──►│    api    │──►│  db (pgmer2) │──►│  (explain)  │
   │ React/TS │   │  FastAPI  │   │  Postgres 17 │   └─────────────┘
   └──────────┘   └───────────┘   └──────┬───────┘
                                          │ NNG
                                   ┌──────▼───────┐
                                   │  mr-service  │  Rust MeritRank engine
                                   └──────────────┘
```

The ranking algorithm is **never reimplemented in this codebase**. `api/provenance/
meritrank.py` is a typed wrapper over the `mr_*` SQL functions; everything the engine
genuinely does not provide (uncertainty, path reconstruction) is clearly marked as ours.

### The graph

Papers are nodes; authors, institutions, topics and venues are *also* nodes. We never
materialise pairwise "same institution" edges between papers — one large institution
would produce hundreds of thousands of edges and swamp every walk. Instead the walk
discovers meta-paths (`Paper → Author → Paper` = co-authorship trust) for free, and the
graph stays sparse.

| Relation | Weight | Note |
|---|---|---|
| `cites` | 1.00 | the deliberate endorsement |
| `cited_by` | 0.15 | weak evidence |
| `authored_by` / `wrote` | 0.60 | author identity carries trust |
| `couples` | 0.35 | scaled by Jaccard of reference sets |
| `co_cited` | 0.30 | log-damped by count |
| `tagged` / `tags` | 0.20 | scaled by topic IDF |
| `published_in` / `publishes` | 0.15 | |
| `affiliated` / `hosts` | 0.10 | a shared employer is barely evidence |

Entity out-edges are hub-damped by `1/sqrt(corpus_degree)`. Without this, an author with
99 papers or a topic with 1,191 swallows every walk and every user gets identical
results.

### Node naming is dictated by the engine, not by us

The engine derives node *kind* from the **first character of the node name** (`U`=User,
`B`=Beacon, …), rejects any edge that is not `(User,User)`, `(NonUser,User)` or
`(User,NonUser)`, and permits only `User` nodes as an ego. Papers therefore **must** be
`U` nodes — the prompt's natural schema (papers as Beacons) would have caused every
citation edge to be silently discarded. Full reasoning in `DECISIONS.md` D1.

---

## Does MeritRank earn its place? The measurements

The brief asked for the sybil experiment to be the strongest evidence that MeritRank
earns its place here. Run honestly, **it is not.** Both results below are as measured.

### 1. It correlates with personalised PageRank, but does not match it

A NetworkX personalised-PageRank baseline over the identical graph and seed set.

| Measure | Value (4 runs) |
|---|---|
| nodes compared | 27,937 |
| Spearman rank correlation | **0.334** (sd 0.052) |
| Kendall tau | 0.252 |
| top-50 overlap | 54% |

Moderate agreement, nowhere near identity. The two algorithms genuinely rank
differently — which is a precondition for MeritRank being worth the trouble, but is not
by itself evidence that its differences are *improvements*.

### 2. Sybil suppression: no measurable effect

20 synthetic papers citing each other densely, attached to the real corpus by a single
edge, scored from the same seed set under both algorithms. Repeated 4 times, because
MeritRank scores are Monte Carlo estimates and one run cannot separate an effect from
sampling noise.

| Algorithm | Share of total score captured by the ring |
|---|---|
| personalised PageRank (deterministic) | 0.2168% |
| **MeritRank** (mean of 4) | **0.2168%** (sd 0.0498) |

| MeritRank / PPR ratio | |
|---|---|
| mean | **1.000** |
| standard deviation | 0.230 |
| range across runs | 0.703 – 1.348 |

**A ratio of 1.00 +/- 0.23 means MeritRank neither suppressed nor amplified the citation
ring relative to plain PageRank, within noise.** Individual runs ranged from 0.70
(looks like suppression) to 1.35 (looks like amplification). Quoting the 0.70 run as
evidence of sybil resistance — which a single-run experiment would have done, and an
earlier sparser build of this graph did — would have been a measurement artefact.

Why the null result, honestly:

- At `MERITRANK_NUM_WALKS=10000` over ~111k nodes, the score of a 20-node ring reachable
  through one edge is small enough that sampling noise swamps it. The experiment as
  designed cannot resolve an effect of this size; a much larger walk count would be
  needed to say anything, and that was not run.
- The ring is attached by a *bidirectional* edge to a genuinely trusted paper, so some
  trust legitimately flows in. Connectivity decay should discount a subgraph reachable
  through a single bottleneck, but any discount applied here is below the noise floor.
- Transitivity and connectivity decay are compiled into `meritrank_core` with no runtime
  surface (`KNOWN_ISSUES.md` §1), so they cannot be varied to isolate their contribution.

**What this means for the project:** on this evidence the choice of MeritRank over
personalised PageRank is *unproven*. It remains defensible on the paper's arguments and
on the decay mechanisms being present in the engine, but this build does not demonstrate
a sybil-resistance benefit against citation-ring-shaped attacks, and this README will not
claim one. It is the single result I would most want another eight hours to chase.

Reproduce with `python scripts/sybil_experiment.py`; per-run numbers in
`data/sybil_results.json`.


---

## Limitations

This section is long on purpose.

**It measures proximity, not quality.** The score says a paper is close to your trust
set in a weighted graph. A paper can be excellent and score low because you seeded a
different subfield. It can score high because it sits next to something you trust while
being wrong.

**Sybil resistance is demonstrated by analogy.** MeritRank's tolerance was derived for
tokenomic feedback systems, where an attacker pays a cost to create edges. A citation
ring is a reasonable analogy but not an instance of that threat model: citations are
free, real rings are small, and genuine citation manipulation is far subtler than a
clique. And in any case the measurement above found **no suppression effect at all**
above the noise floor, so there is currently no empirical claim here to generalise from.

**The decay mechanisms are largely unreachable.** Transitivity and connectivity decay —
the entire reason for choosing MeritRank over personalised PageRank — are compiled into
`meritrank_core` with no runtime surface. We cannot tune them or show you their values.
Epoch decay in this product is *ours*, applied to edge weights at build time; it is not
the paper's epoch decay. See `KNOWN_ISSUES.md` §1.

**The corpus is statistics, not mathematics.** Built exactly as specified — OpenAlex
field *Mathematics*, since 1990, by citation count — the result is dominated by
statistical and biostatistical methodology, because OpenAlex files statistics under
Mathematics and applied science cites it at a volume pure mathematics never reaches. The
largest topics are Statistical Methods (1,191 papers), Bayesian Inference (1,151), Causal
Inference (666), and *COVID-19 epidemiology* (331). The most-cited paper in the corpus is
Rosenbaum & Rubin on propensity scores; the best-connected algebraic geometry paper has 8
in-corpus citations against its 250. **A pure mathematician would open this and recognise
almost nothing.** Stratified sampling across mathematics subfields would fix it and was
not done — see `KNOWN_ISSUES.md` §5, which is the most serious item in this build.

**Coverage bias.** OpenAlex under-represents non-English work, pre-digital literature,
and some regions. Our corpus is ~7,200 full papers seeded from the most-cited
mathematics works since 1990, which compounds this: it is biased toward highly-cited,
English-language, digitally-indexed material. **A low score frequently means "thinly
represented in the data", not "untrustworthy."** Additionally ~29% of our full papers
arrive from OpenAlex with an empty reference list — disproportionately books and older
articles — so they can only ever be reached through incoming citations.

**Institutional edges launder hierarchy.** Encoding "these authors share an employer" as
evidence of trust risks dressing existing academic prestige up as an objective
measurement. That is exactly why `affiliated` is the lowest-weighted relation, why the
weight is user-adjustable, and why the parameter playground says this out loud next to
the slider.

**Uncertainty is leave-one-out, not sampling error.** The service exposes no per-call
walk count or sampling seed, so we cannot report a true Monte Carlo standard error. We
report how much the ranking depends on any single trust decision instead. It is a
useful number and an honest one, but it is not the same number.

**Rankings lean heavily on direct citations.** Measured: 19 of a top 20 were direct
citation neighbours of a seed. That is defensible (a citation *is* the strongest signal)
but it means the default view is closer to "your reading list's bibliography" than to
discovery. The diversity dial and blindspots exist for this reason.

---

## Documents

| File | What's in it |
|---|---|
| `DECISIONS.md` | every fork, the rejected alternative, and what the engine actually does versus what the brief assumed |
| `BUILD_LOG.md` | chronological build record, including the full `\df` surface |
| `KNOWN_ISSUES.md` | everything broken, missing or cut, ranked by severity |
| `DEMO.md` | a five-minute scripted walkthrough |
| `API_CONTRACT.md` | the API contract the frontend was built against |

## Licence

`vendor/meritrank-rust` is MIT, © Intersubjective. Our code is MIT.
