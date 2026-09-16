# KDP Niche Intelligence

A self-hosted Amazon KDP niche-research toolkit built on
[Scrapling](https://github.com/D4Vinci/Scrapling). Three tools, from quick
niche checks to a full research dashboard — the core of what Publisher
Rocket / KDSpy / Oxylabs-style services sell, free and fully auditable.

| Tool | What it does |
|---|---|
| `kdp_niche_validator.py` | Search a keyword, validate up to N books (ASIN, title, price, KU status, rating, reviews, cover, link) with Pydantic, and print a competition verdict |
| `kdp_longtail_finder.py` | Mine Amazon's autocomplete for long-tail variants (real buyer searches = demand proof), validate each against live results, and rank them by opportunity score |
| `kdp_intel_dashboard.py` | The full pipeline: keywords → niche scan → product-page deep dive (BSR → estimated sales & royalties, release velocity) → title gaps → complaint mining → one self-contained HTML dashboard |

See [`sample_output/`](sample_output/) for a real dashboard generated for
the keyword "air fryer".

## Install

Python 3.10+.

```bash
pip install -r requirements.txt
scrapling install          # browser deps (only needed for the stealth fallback)
```

## Usage

```bash
# 1) Validate a niche: how many books, how beatable?
python kdp_niche_validator.py "dog training for beginners"
python kdp_niche_validator.py "air fryer" --marketplace uk --max-books 300 --reviews

# 2) Find long-tail opportunities inside a niche
python kdp_longtail_finder.py "air fryer" --max-keywords 25

# 3) Everything at once -> HTML dashboard + JSON data bundle
python kdp_intel_dashboard.py "air fryer" --max-keywords 12 --books 30 --deep-dive 10
```

### Shared options (all three tools)

| Flag | Purpose |
|---|---|
| `--marketplace us\|uk\|ca\|au\|in\|jp\|de\|fr\|it\|es\|nl\|se\|br\|mx` | Which Amazon to search (locale-aware prices and result counts) |
| `--store kindle\|books` | Kindle store or print books |
| `--impersonate chrome\|edge\|safari\|firefox` | Browser TLS fingerprint |
| `--plain-headers` | Disable generated stealth headers (some networks get 503s with them) |
| `--proxy URL` | One static proxy |
| `--proxies "u1,u2,..."` or `--proxies @file.txt` | Rotating proxy pool — every request cycles IPs; blocked/dead proxies rotate automatically |

## How the numbers are derived (the "proof" layer)

- **Demand** — keywords come from Amazon's own search autocomplete, which
  only suggests what buyers actually type; suggestion position is the
  popularity signal.
- **Competition** — live result counts plus page-1 review medians.
- **Relevance** — how many page-1 titles contain the exact phrase
  (separates book niches from product searches).
- **Money** — Best Sellers Rank read from each product page, converted with
  a log-interpolated public BSR→sales curve (anchors are in the script and
  easy to tweak), times the real KDP royalty (70% of price −$0.06 in the
  $2.99–$9.99 band, else 35%). KU page-read income is **not** included.
- **Velocity** — publication dates from product pages: is page 1 held by
  old books, or is the niche flooded with fresh releases?
- **Complaints** — 1–3★ review texts of the top books (dedicated review
  pages when reachable; the reviews rendered on product pages as fallback,
  since Amazon login-walls review pagination for anonymous visitors).

Every metric appears in the JSON output, so every score is auditable.

## Honest limitations

- BSR→sales is an order-of-magnitude estimate, not accounting. The tool now
  reports it as a **range with a confidence label** (`kdp_estimates.Estimate`)
  rather than a point value, because a single number implied precision the
  public curve cannot support.
- The curve is calibrated on the US store; other marketplaces are scaled by
  rough relative market size. Crude, but far better than treating a UK BSR of
  10,000 as equal to a US one.
- KU income is now included (`kdp_estimates.ku_monthly_income`) but assumes a
  300-page KENP length and a 70% read-through. Override per niche if you know
  better.
- Anonymous scraping gets ~10 reviews per star filter per book — full review
  histories require a logged-in session (ban risk) or a paid API.
- Amazon marketplaces protect themselves differently: UK/JP/AU aggressively
  block datacenter IPs. Use region-local residential proxies via `--proxies`.
- **Selector rot is now cushioned, not eliminated.** The search-result card
  selector goes through `kdp_adaptive.select()`, which saves each element's
  fingerprint on every successful parse (`data/adaptive.db`) and relocates it
  by similarity when Amazon renames an attribute. A relocation raises a
  visible warning — running on similarity matches means the selector still
  needs updating, and a silent recovery would trade one hidden failure for
  another. It also makes a redesign distinguishable from a block, which the
  old code could not do.
- **Reddit** needs free official API credentials for engagement numbers; the
  RSS fallback carries titles only. **X/Twitter and TikTok/BookTok are not
  measured at all** — see the source table in [DEPLOY.md](DEPLOY.md) for the
  probe evidence. The tool reports them as excluded instead of inventing a
  signal.
- No search-volume numbers (autocomplete presence/position is the proxy).

## What the web app adds on top of the CLI tools

| Feature | What it does |
|---|---|
| **Niche Research** | Keyword table with an honest 0–100 demand index, category picks with entry bars, income as a confidence band, pricing bands and sweet spot, review velocity, praise mining (what 4–5★ reviews reward), complaint mining, listing-polish benchmark, and the "customers also viewed" flow between books |
| **Discovery** | Seedless: harvests what people ask for, normalises it into book concepts with receipts, scores demand ÷ supply |
| **Trend Radar** | Phrase-level trends, with or without a topic, validated against live Amazon |
| **Teardown** | Paste ASINs: positioning, crowdedness, listing polish, and an author profile (catalog size, review mass, series habit, cadence when dates are known) |
| **Listing** | KDP metadata guidelines checker and per-marketplace translation, every pack re-checked |
| **Categories** | A catalogue that grows with every scan, plus a bounded crawl of Amazon's bestseller tree |
| **Export** | CSV for every table, formula-injection safe |

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The suite is offline: network is injected into every source adapter as a
`fetch` callable, so tests are deterministic and fast. The money maths has a
regression test (`TestOneSourceOfTruth`) asserting the CLI dashboard and the
web royalty engine return identical figures — they had silently diverged
before `kdp_estimates.py` existed.

## Legal

Scraping Amazon is against their Terms of Service. This code is for
educational and research use; you are responsible for how you use it.
Consider the Amazon Product Advertising API or Keepa for compliant
alternatives. Be polite: the defaults use low concurrency and delays —
keep them that way.

## Credits

Built on [Scrapling](https://github.com/D4Vinci/Scrapling) (BSD-3) by
Karim Shoair — the adaptive scraping framework doing the heavy lifting
(TLS impersonation, spider engine, blocked-request retry, proxy rotation).


## Frontend

The UI lives in `web/` (Vite, React, TypeScript, Tailwind v4, shadcn). The
landing page is at `/` and the dashboard at `/app`; both are served by the
FastAPI app from `web/dist` once built.

```bash
cd web && npm ci
npm run dev      # http://localhost:5173, proxies /api to the server on :8000
npm run build    # writes web/dist; the server picks it up on next start
```

shadcn components are added with `npx shadcn@latest add <name>` and land in
`web/src/components/ui`, imported as `@/components/ui/<name>`. The original
single-file page stays available at `/legacy`.
# kdp-intelligence
