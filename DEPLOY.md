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

- **Set `KDP_API_KEY`** (env var) so only you can start jobs; the dashboard
  is read-open, job creation then requires the `X-API-Key` header (add it in
  the browser via an extension, or front the app with basic auth).
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

Jobs run one at a time (deliberate — polite scraping). The result bundle is
the same JSON the CLI dashboard produces: summary KPIs, scored keywords,
books, BSR money metrics, release velocity, title gaps, n-grams, complaints,
and any warnings (blocked pages are flagged, never silently scored).

## Operational notes

- **Demo mode** (`demo: true` or the dashboard button) serves the bundled
  sample dataset through the same job machinery — ideal for client demos.
- The queue is in-process; if you scale to multiple workers, keep
  concurrency at 1 against Amazon per egress IP.
- Amazon markup changes will eventually need selector touch-ups in the
  three CLI tools; the web layer is decoupled from them.
