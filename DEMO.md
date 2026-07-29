# DEMO — five minutes

Start the stack and open **http://localhost:5173**.

```bash
docker compose up -d
curl -s http://localhost:5173/api/health     # expect ok:true
```

> **Pick your seeds from statistics/causal inference, not pure maths.** The corpus is
> dominated by statistical methodology — see `KNOWN_ISSUES.md` §5. The demo below uses
> the densest, best-connected region of the graph, which is where the system has
> something interesting to say.

---

## 0:00 — What this is (15 seconds)

Read the line in the masthead out loud before touching anything:

> *This measures proximity in a weighted trust graph. It is not a measure of quality.*

That sentence is the product. Everything else is machinery for making it inspectable.

---

## 0:15 — Build a trust set (90 seconds)

Go to **Trust** (`/#/trust`). Search and add these five, all at strength **4**:

These queries were checked against the live search; the position of the paper you want
is given, because search ranks by relevance and does not always put an exact title
first (`KNOWN_ISSUES.md` §13).

| Search for | Take result | Paper |
|---|---|---|
| `Multiple Imputation Nonresponse Surveys` | **1st** | Multiple Imputation for Nonresponse in Surveys |
| `Bayesian Inference for Causal Effects` | **1st** | Bayesian Inference for Causal Effects: The Role of Randomization |
| `Inference and missing data` | **1st** | Inference and missing data |
| `Estimating causal effects treatments randomized nonrandomized` | **1st** | Estimating causal effects of treatments in randomized and nonrandomized studies |
| `Maximum Likelihood from Incomplete Data` | **3rd** | Maximum Likelihood from Incomplete Data Via the *EM* Algorithm |

That is a coherent, densely connected cluster — the Rubin school of causal inference and
missing data — which is the strongest region of this corpus.

**Watch the cold-start notice.** With fewer than five seeds the app states plainly that
the rankings are unreliable. It disappears at five. It is not a dismissable toast — you
cannot proceed past it by clicking it away.

> The first ranking after saving builds random walks on the engine and takes up to a
> minute. That is real work, not a spinner. Subsequent views are ~45 ms.

---

## 1:45 — Rankings, with the uncertainty visible (60 seconds)

Go to **Rankings** (`/#/`).

Two things to point at:

1. **Every score has an error bar.** Not decoration — it is a leave-one-out spread: how
   much the ranking moves when any single one of your five trust decisions is removed.
2. **The tie brackets.** Papers grouped under a bracket are *statistically
   indistinguishable*; the order inside a bracket is arbitrary and the UI says so. On a
   five-seed profile you will typically see rank 1 alone and ranks 2–8 bracketed
   together. That is the honest answer, and most tools would have shown you a confident
   1-2-3-4-5.

---

## 2:45 — Explain: where the trust actually came from (75 seconds)

Click **Explain** on the top-ranked paper.

You get the real contributing paths, reconstructed over the same edge data that produced
the score — not a post-hoc rationalisation:

- a **1-hop** path: a paper you trust cites this one directly;
- **2-hop** paths through entity nodes: *"…via a shared topic"*, *"…via an author whose
  work you trust"*. These meta-paths are exactly what the heterogeneous graph buys you.

Below that, the **per-context decomposition** — typically citation ~0.77, topic ~0.13,
author ~0.06, venue and institution near zero.

> Say the caveat out loud: each context is *the citation backbone plus one relation
> family*, because the engine replicates paper-to-paper edges into every context. So
> those numbers are **marginal** contributions, not isolated ones. `DECISIONS.md` D1.6.

---

## 4:00 — The parameter playground (60 seconds)

Go to **Parameters** (`/#/params`). This is the screen that decides whether a sceptic
believes you.

Drag **author** up to 2.0 and watch the top-20 reorder live. Drag **institution** to 0
and watch it barely move. These are genuinely per-user and genuinely live: the API holds
raw per-context scores and re-composes them, so nothing is recomputed on the engine.

**Read the note beside the institution slider.** It says that institutional edges risk
laundering existing academic hierarchy into apparent objectivity, which is why the weight
is lowest by default and why you can zero it.

Then be straight about the limits: the sliders that are absent are absent on purpose.
Alpha, walk count and the paper's transitivity/connectivity decay are compiled into the
engine with no runtime surface, so there is no slider for them, and `POST /api/params`
returns **422** rather than accepting a value it would silently ignore.

---

## 5:00 — The most interesting object in the system

Open any paper view (`/#/paper/:id`) and look at the **comparison strip**: your trust
score vs. unpersonalised merit vs. raw citation count, as three percentile bars.

Papers where those three sharply disagree are the point of the whole exercise — highly
cited but far from you, or close to you and largely uncited. The app flags high
disagreement prominently.

---

## If you have one more minute: the honest bit

Open the README's **measurements** section. The sybil experiment — the one that was
supposed to prove MeritRank earns its place — came back at a ratio of **1.00 ± 0.23**
over four runs. No measurable suppression of a citation ring versus plain personalised
PageRank. A single run had said 0.70 and would have looked like proof.

That is in the README, not buried here.
