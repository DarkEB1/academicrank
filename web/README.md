# Provenance — web client

A front end for the Provenance trust-graph API. It is deliberately not a
dashboard: every score is shown with its uncertainty, statistically tied papers
are drawn as tied, and the interface says out loud what the number is and is not.

## Requirements

- Node 20 or newer (built and tested on Node 22)
- The Provenance API listening on `http://localhost:8000`

## Running it

```bash
cd web
npm install
npm run dev          # http://localhost:5173
```

The dev server proxies `/api` to `http://localhost:8000`, so the backend needs no
CORS configuration. If the API is not running, the app renders an explicit
"the API is not answering" state rather than a spinner that never resolves.

There is no login. On first load the client calls `POST /api/profiles` to mint an
anonymous profile, stores the token in `localStorage` (the server also sets the
`pv_token` cookie), and sends it as `Authorization: Bearer <token>` thereafter.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server on port 5173 with the `/api` proxy |
| `npm run build` | `tsc -b` then `vite build` → `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run test` | Vitest, single run |
| `npm run test:watch` | Vitest in watch mode |
| `npm run typecheck` | Type check without emitting |

## Production build

```bash
npm run build
```

`dist/` is fully static. Asset paths are relative and routing is hash-based, so
the output works from any static file server — including one with no SPA
rewrite rules — and from a subdirectory:

```bash
npx serve dist          # or: python -m http.server -d dist
```

The built client still calls `/api` on whatever origin serves it. Put the API
behind the same origin at `/api`, or build with an explicit base:

```bash
VITE_API_BASE=https://provenance.example.org/api npm run build
```

## Screens

| Route | Purpose |
|---|---|
| `/` | Rankings: sortable, filterable, tie groups bracketed, per-row Explain panel |
| `/trust` | Trust set builder: search, strength 1–5, distrust, BibTeX import, impact preview |
| `/recommendations` | Diversity dial (exploitation ←→ exploration) with the trade-off stated |
| `/paper/:id` | Metadata, abstract, explanation, local neighbourhood graph, three-measure comparison |
| `/graph` | WebGL graph explorer with legend, kind/relation filters, click-to-focus |
| `/params` | Live parameter sliders with the top twenty re-ranking as you drag |

Press <kbd>⌘K</kbd> / <kbd>Ctrl+K</kbd> anywhere for the command palette: it
navigates between screens and searches the corpus.

## Stack

React 18, TypeScript, Vite, TanStack Query v5, Tailwind CSS, sigma.js +
graphology (WebGL), KaTeX, lucide-react, Vitest.

UI primitives in `src/components/ui` are written by hand in the shadcn/ui idiom.
The shadcn CLI is not used and no component library is installed.

## Layout

```
src/
  lib/            API client, contract types, and all pure logic
    types.ts      Transcribed from API_CONTRACT.md — the source of truth
    api.ts        Typed fetch wrapper, one function per endpoint
    queries.ts    TanStack Query hooks and cache keys
    format.ts     Score/uncertainty formatting and error-bar geometry
    ties.ts       Tie-group grouping and copy
    diversity.ts  The diversity dial
    paths.ts      Turning graph paths into readable sentences
    katex.ts      Safe TeX segmentation and rendering
  components/     Shared components; ui/ holds the primitives
  routes/         One file per screen
  test/           Vitest suites
```

## Notes on behaviour

- **First query is slow.** The first ranking for a new profile warms the ego's
  walks server-side. Skeletons cover it and retries are limited so they do not
  pile onto a request that is already working.
- **Dark mode** is a `dark` class on `<html>`, set before first paint by an
  inline script in `index.html` and persisted to `localStorage`. Both themes are
  designed separately; neither is an inversion of the other.
- **No mock data.** Nothing renders from a hard-coded array. If the API is
  unavailable the UI says so.

Decisions the API contract did not settle are recorded in `FRONTEND_NOTES.md`.
