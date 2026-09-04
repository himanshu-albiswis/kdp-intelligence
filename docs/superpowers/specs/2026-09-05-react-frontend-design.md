# React frontend: landing page and dashboard port

**Date:** 2026-09-05
**Status:** built in this session; the legacy single-file page remains at `/legacy`.

## Why

The UI was one 1,067-line `server/static/index.html` with string-built HTML. The
client asked for a shadcn / Tailwind / TypeScript frontend, a specific hero
component for a landing page, and the same dashboard in that stack.

## Shape

- `web/` — Vite + React 19 + TypeScript + Tailwind v4 + shadcn (new-york).
  Components live in `web/src/components/ui` (reached as `@/components/ui`,
  which is what shadcn's CLI and the supplied `demo.tsx` expect; with Vite
  there is no root `/components` folder).
- Routes: `/` renders the hero landing (`Hero2`, rebranded); `/app` renders
  the dashboard. `?tab=` selects the tool; `?demo=1` runs the sample scan.
- Data flow: `lib/api.ts` is the only fetch layer. `useJobs` polls the job
  list every 5 s; `useJob` polls one job every 2.5 s until it finishes.
  Forms post to the API and return either a job id (rendered by polling) or a
  finished node handed straight to the main panel. Errors render inline.
- Views: one file per job kind (research, trends, discovery, teardown, crawl)
  plus the static results (listing check, translation, categories, calculator).
  They are ports of the legacy renderers, field for field.
- Serving: FastAPI serves `web/dist` at `/`, `/app`, `/assets` when the build
  exists, else falls back to the legacy page. `npm run dev` proxies `/api`.

## Design language

Kept from the legacy page: Instrument Serif display, IBM Plex Sans/Mono,
aurora field under frosted glass, uppercase section rules, income shown as a
range bar with named confidence. Expressed as Tailwind theme tokens
(`text-money`, `bg-glass-strong`, …) plus a small set of component classes in
`web/src/index.css`.

## Security carried over

Concept phrases and titles are React state, never markup, so the earlier
escape-into-onclick bug class cannot recur. Job list rows are real buttons.

## Not done

Chrome extension (parked by the client). No authentication (removed earlier
at the client's request).
