# Deploying the KDP Niche Intelligence web app

The web app wraps the three CLI tools with a FastAPI backend, a job queue,
SQLite persistence, and a single-page dashboard. No frontend build step —
deploy it anywhere Python or Docker runs.

## Run locally

```bash
pip install -r requirements.txt -r requirements-server.txt
uvicorn app:app --app-dir server --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — click **Load demo dataset** to see the full
dashboard instantly (bundled sample from a real "air fryer" scan), or run a
live research job.

## Run with Docker

```bash
docker compose up --build -d
```

The job database persists in the `kdp-data` volume.

## Deploy to a VPS / Railway / Fly / Render

Any platform that runs a Dockerfile works as-is. Two production notes:

- **Auth.** Two options, decided by what you configure:
  - **Clerk (recommended for anything client-facing).** Create an application
    at [dashboard.clerk.com](https://dashboard.clerk.com), then add to `.env`:

    ```bash
    CLERK_PUBLISHABLE_KEY=pk_...
    CLERK_SECRET_KEY=sk_...
    ```

    Every `/api` route then requires a signed-in session (`/api/health` and
    `/api/config` stay open for probes and the sign-in screen). The page
    shows a sign-in overlay and a user menu; session tokens are verified
    server-side against Clerk's JWKS. The secret key never reaches the
    browser — `/api/config` exposes only the publishable key.
  - **`KDP_API_KEY`** (legacy): dashboard read-open, job creation/deletion
    requires the `X-API-Key` header. Ignored when Clerk is configured.
- **Run from a residential IP or configure proxies.** Datacenter IPs
  (all cloud hosts) get blocked by Amazon quickly. Paste a rotating
  residential proxy pool into the form's proxies field, or bake it in by
  passing `proxies` in every request. This is the single most important
  operational requirement.

## API

| Method & path | Purpose |
|---|---|
| `GET /api/health` | Liveness + whether auth is required |
| `POST /api/research` | Start a job — body: `{seed, marketplace, store, max_keywords, books, deep_dive, complaint_books, impersonate, plain_headers, proxy, proxies, focus, demo}` |
| `GET /api/research` | List jobs with status/progress |
| `GET /api/research/{id}` | Job detail; includes the full result bundle when finished |
| `DELETE /api/research/{id}` | Remove a finished job |
| `POST /api/trends` | Trend Radar scan — `{seed, marketplace, validate_top, proxies}`: Google + YouTube + Reddit demand, cross-checked against live Amazon, quadrant-ranked (GO/ANGLE/VERIFY/AVOID) |
| `POST /api/royalty` | Break-even calculator — `{goal_month, format, price, pages, color, file_mb, research_job_id}` → royalty/sale, required sales/day, required BSR, live-shelf feasibility |
| `POST /api/research/{id}/brief` | Generate the Niche Brief (verdict, gates, break-even, evidence, angles) for a finished job |

## AI narrative (optional)

The Niche Brief always ships its deterministic layer (gates, break-even,
evidence). Add a key to enable the grounded analyst narrative on top.

Put secrets in a `.env` file in the repo root — it is git-ignored and loaded
automatically at startup:

```bash
# .env
GEMINI_API_KEY=...        # + optional GEMINI_MODEL (default gemini-2.5-flash)
# or
ANTHROPIC_API_KEY=...     # + optional ANTHROPIC_MODEL (default claude-sonnet-5)
```

Gemini wins if both are set. Keys are read **per call**, not at import, so
you can add or rotate one without restarting the server — and a key added
after startup is picked up rather than silently ignored.

The model receives only the computed data digest and must cite ASINs; if the
call fails, the brief still renders and `narrative_source` says what went
wrong instead of failing the request.

## Two traps that silently corrupt results

Both were found by running real scans on 2026-08-31 and are now guarded.

**1. The `chrome` fingerprint plus stealth headers is refused by Amazon.**
Measured three times each on `amazon.com/s?k=adhd+for+beginners`:

| fingerprint | stealth headers | result |
|---|---|---|
| chrome | on | **HTTP 503, 2 KB decoy, 0 results** |
| chrome | off | HTTP 200, ~850 KB, 16 results |
| edge | on | HTTP 200, ~920 KB, 16 results |
| edge | off | HTTP 200, ~863 KB, 22 results |

Stealth headers are not the problem on their own — `edge` tolerates them.
The default is now `edge`, and choosing `chrome` auto-disables stealth
headers with a note rather than failing. A "Plain headers" checkbox is on
both forms as an escape hatch.

**2. Amazon localises prices to your IP.** From an Indian IP, `amazon.com`
returns prices in **INR** (`a-price-symbol">INR`, wholes like `1,337`)
while the tool labelled them `$`. That produced `avg_buy_price: $1593.95`
and royalties computed on rupee figures — every money number wrong, with no
warning. The pipeline now reads the rendered currency, compares it to the
marketplace's, and **suppresses prices and all derived income** on a
mismatch rather than reporting a confident wrong answer. Use a proxy in the
target country, or research the marketplace matching your location.

## Scraping resilience

- **Adaptive selectors.** Element fingerprints accumulate in `data/adaptive.db`
  while scans succeed. If Amazon renames `data-component-type`, the card is
  recovered by similarity and the run warns you instead of reporting a
  phantom soft block. Delete the file to relearn from scratch.
- **Adaptive throttling.** Spiders set `autothrottle_enabled`, so the delay is
  chosen per domain and backs off when Amazon starts blocking, rather than
  trusting one hand-tuned constant.
- **Response replay for development.** Set `KDP_DEV_CACHE=1` to cache and
  replay responses while iterating on parse logic, instead of re-hitting
  Amazon and burning into a soft block. Never set it in production — a stale
  cache scoring old data is worse than a slow scan.

## Operational notes

- **Demo mode** (`demo: true` or the dashboard button) serves the bundled
  sample dataset through the same job machinery — ideal for client demos.
- The queue is in-process; if you scale to multiple workers, keep
  concurrency at 1 against Amazon per egress IP.
- Amazon markup changes will eventually need selector touch-ups in the
  three CLI tools; the web layer is decoupled from them.
