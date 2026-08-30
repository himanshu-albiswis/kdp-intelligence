# Source adapters + one estimates engine

**Date:** 2026-08-31
**Status:** implemented

## Problem

Two separate problems were reported as one: "the social scraping fails, and
we want it more accurate".

### Sources

Probing from a residential IP (Bengaluru), including through Scrapling's
Camoufox stealth browser, established what actually fails and why:

| Source | Plain HTTP | Stealth browser | Diagnosis |
|---|---|---|---|
| Reddit `search.json` | 403 | 403 | Auth wall. Anonymous API access is closed. |
| Reddit `search.rss` | 200 (real posts) | — | Works; rate-limited. |
| X search | JS wall | JS wall | Logged-out X serves content to nobody. |
| TikTok `/tag/booktok` | 302 | 200, 0 videos | Empty shell; data comes from signed API calls. |
| TikTok Creative Center API | — | `{"code":40101,"msg":"no permission"}` | Needs a session. |
| OpenLibrary | 200 | — | Free JSON API, useful. |
| Goodreads | 200 | — | Ratings render client-side; HTML yields no demand numbers. Rejected. |

The decisive insight: **stealth defeats bot-detection; these are auth and
signing walls.** A better fingerprint cannot manufacture a login session, so
the repo's standing advice ("use residential proxies for Reddit") was wrong.

Two further defects were found in the existing Reddit code:

1. Query strings were built with `query.replace(" ", "+")`, leaving raw `"`
   characters in the URL. Reddit answers **403** to that. Properly encoded,
   the same URL is served. Reddit was broken twice over.
2. A blocked source returned an empty list, which scoring read as "no
   demand" — indistinguishable from genuine absence of demand.

### Accuracy

Three defects in the money maths:

1. The BSR→sales curve existed twice (`kdp_intel_dashboard._BSR_ANCHORS`
   and `server/royalty.KINDLE_ANCHORS`).
2. The two modules **already disagreed** on ebook royalty: a flat `$0.06`
   delivery fee versus `$0.15/MB`. At $2.99 the dashboard said $2.03 and the
   royalty engine said $1.79 — a $0.24/sale divergence between two endpoints
   of the same product.
3. `ku_read_payout()` existed but was never called. With `ku_share_pct: 60`
   typical, KU-heavy niches were systematically understated.

Plus false precision: an order-of-magnitude curve reported to the cent.

## Design

### `kdp_estimates.py` — one engine

Repo root, so both the CLI scripts and the web app import the same module.
Owns the anchors, marketplace scaling, royalty rules, KENP, and the
inversion. `sales_per_day` returns an `Estimate(low, mid, high, confidence)`
rather than a float; confidence is never "high", because no public curve
earns it. `niche_income()` rolls a niche up into a band including KU.

`kdp_intel_dashboard` and `server/royalty` keep their historical
float-returning function names as thin wrappers, so call sites are
unchanged, but both now delegate. A regression test asserts the two agree.

### `server/sources/` — one adapter shape

Every source returns a `SourceResult{name, status, items, detail, transport,
evidence_url, meta}` where status is `ok | unavailable | not_configured |
excluded`, and `usable` requires `ok` *and* non-empty items. A dead source is
visibly dead. Excluded sources carry their probe evidence in `detail`.

- `reddit` — official OAuth API when `REDDIT_CLIENT_ID`/`SECRET` are set,
  public RSS otherwise. RSS items report `score: None`, never `0`.
  Escalating backoff on 429; never retries a 403, which is not transient.
- `autocomplete` — Google and YouTube.
- `openlibrary` — catalogue supply and publication-year spread.
- `registry` — runs sources with per-source failure isolation, merges in the
  documented exclusions, and produces a health summary the dashboard renders.

## Deliberately not done

- **BookTok.** Requires a paid provider (~$50–100/mo) or ToS-violating
  signature scraping. Reported as excluded with evidence rather than faked.
- **X/Twitter.** Same, at $200/mo.
- **Goodreads.** Evaluated, rejected: no demand numbers in the HTML.
- **Trend history.** Time-series momentum needs scheduled runs and a schema
  change; out of scope for this pass.
