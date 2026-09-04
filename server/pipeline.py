"""Research pipeline wrapper for the web app.

Wraps the CLI tools (kdp_longtail_finder, kdp_niche_validator,
kdp_intel_dashboard) into one callable that reports progress and returns
the same JSON bundle the CLI dashboard produces.
"""

import json
import os
import statistics
import sys
from datetime import datetime
from typing import Any, Callable, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from kdp_longtail_finder import (  # noqa: E402
    MARKETPLACES,
    STORES,
    LongTailSpider,
    attach_demand,
    mine_suggestions,
)
from kdp_niche_validator import KDPNicheSpider  # noqa: E402
from kdp_intel_dashboard import (  # noqa: E402
    ComplaintSpider,
    DeepDiveSpider,
    find_title_gaps,
    release_velocity,
    title_ngrams,
)
from scrapling.fetchers import ProxyRotator  # noqa: E402
import kdp_estimates  # noqa: E402
import kdp_adaptive  # noqa: E402

try:  # pragma: no cover - import-shape shim (see server/trends.py)
    from . import pricing as pricing_mod, reviews as reviews_mod
    from . import product_signals as signals_mod
    from .category_db import CategoryStore
except ImportError:  # pragma: no cover
    import pricing as pricing_mod
    import reviews as reviews_mod
    import product_signals as signals_mod
    from category_db import CategoryStore

CATEGORY_DB = os.environ.get("KDP_CATEGORY_DB", os.path.join(REPO_ROOT, "data", "categories.db"))

try:  # pragma: no cover - import-shape shim (see server/trends.py)
    from . import categories as categories_mod
except ImportError:  # pragma: no cover
    import categories as categories_mod

try:  # pragma: no cover - import-shape shim (see server/trends.py)
    from . import transport
except ImportError:  # pragma: no cover
    import transport

SAMPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data.json")

ProgressFn = Callable[[str, int, str], None]  # (stage, percent, message)


_STORE: Optional[CategoryStore] = None


def _category_store() -> CategoryStore:
    global _STORE
    if _STORE is None:
        _STORE = CategoryStore(CATEGORY_DB)
    return _STORE


def run_research(params: dict[str, Any], progress: ProgressFn) -> dict[str, Any]:
    """Run the full research pipeline. Blocking; call from a worker thread."""
    if params.get("demo"):
        return _run_demo(progress)

    seed: str = params["seed"].strip()
    marketplace: str = params.get("marketplace", "us")
    store_key: str = params.get("store", "kindle")
    max_keywords: int = int(params.get("max_keywords", 10))
    max_books: int = int(params.get("books", 30))
    deep_dive_n: int = int(params.get("deep_dive", 8))
    complaint_n: int = int(params.get("complaint_books", 3))
    # Measured defaults; see server/transport.py for the A/B that chose them.
    impersonate, stealthy_headers, transport_notes = transport.resolve(params)
    proxy: Optional[str] = params.get("proxy") or None
    proxies: Optional[list[str]] = params.get("proxies") or None

    # Fingerprints accumulate in data/adaptive.db while selectors work,
    # ready for the day Amazon redesigns.
    kdp_adaptive.configure()

    store = STORES[store_key]
    domain, currency, _mid = MARKETPLACES[marketplace]
    warnings: list[str] = list(transport_notes)

    common = dict(
        marketplace=marketplace,
        impersonate=impersonate,
        stealthy_headers=stealthy_headers,
        proxy=proxy,
        proxies=proxies,
    )
    session_kwargs: dict[str, Any] = dict(
        impersonate=impersonate,
        stealthy_headers=stealthy_headers,
        proxy=proxy,
        proxy_rotator=ProxyRotator(proxies) if proxies else None,
    )

    # 1 — long-tail mining + validation
    progress("keywords", 5, f'Mining Amazon autocomplete for "{seed}"')
    candidates, broaden_note = mine_suggestions(seed, marketplace, impersonate, max_keywords)
    if broaden_note:
        warnings.append(broaden_note)
    progress("keywords", 12, f"{len(candidates)} long-tail candidates found; validating against live results")
    lt_spider = LongTailSpider(candidates=candidates or {seed: 0}, store=store, **common)
    lt_spider.start()
    keywords = sorted(lt_spider.results, key=lambda m: m.opportunity, reverse=True)
    progress("keywords", 30, f"Probing autocomplete demand for {len(keywords)} keywords")
    # The honest stand-in for "estimated searches/month": how few characters
    # Amazon needs before it suggests the phrase. Comparability, not volume.
    attach_demand(keywords, marketplace, impersonate)
    if lt_spider.failed_keywords:
        warnings.append(f"{len(lt_spider.failed_keywords)} keyword page(s) blocked and excluded: "
                        + ", ".join(lt_spider.failed_keywords))

    book_niches = [m for m in keywords if "PRODUCT-INTENT" not in m.verdict and m.phrase_in_titles >= 2]
    focus = params.get("focus") or (book_niches[0].keyword if book_niches else seed)
    focus_metrics = next((m for m in keywords if m.keyword == focus), None)

    # 2 — niche scan
    progress("niche", 35, f'Scanning up to {max_books} books for "{focus}"')
    niche_spider = KDPNicheSpider(keyword=focus, store=store, max_books=max_books, **common)
    niche_spider.start()
    books = niche_spider.valid_books

    # Amazon localises prices by IP: amazon.com serves INR to an Indian visitor.
    # Scoring those as dollars produced avg_buy_price ~$1,594 and nonsense
    # royalties, so verify what was actually rendered before trusting money.
    served = kdp_estimates.observed_currency(getattr(niche_spider, "price_texts", []))
    currency_ok = kdp_estimates.currency_matches(currency, served)
    if not currency_ok:
        warnings.append(
            f"Amazon served prices in {served}, but the {marketplace.upper()} "
            f"marketplace bills in {currency}. Your IP is being localised, so "
            f"prices and every figure derived from them are suppressed. Use a "
            f"proxy in the target country, or select the marketplace that "
            f"matches your location."
        )

    if getattr(niche_spider, "selector_drift", None):
        warnings.append(niche_spider.selector_drift)

    if not books:
        warnings.append(transport.block_advice(impersonate, stealthy_headers))

    # 3 — product-page deep dive
    deep_targets = sorted(books, key=lambda b: -b.reviews)[:deep_dive_n]
    progress("deepdive", 60, f"Deep-diving {len(deep_targets)} product pages (BSR, dates, money)")
    dd_spider = DeepDiveSpider(books=deep_targets, session_kwargs=session_kwargs)
    if deep_targets:
        dd_spider.start()
    intel = dd_spider.intel
    velocity = release_velocity(intel)

    # 4 — complaints
    complaint_targets = deep_targets[:complaint_n]
    progress("complaints", 80, f"Mining 1–2★ reviews of top {len(complaint_targets)} books")
    c_spider = ComplaintSpider(books=complaint_targets, domain=domain, session_kwargs=session_kwargs)
    if complaint_targets:
        c_spider.start()
    seen: set[str] = set()
    complaints, praise = [], []
    for s in [*c_spider.snippets, *dd_spider.review_snippets]:
        key = f"{s.asin}:{s.body[:60]}"
        if key in seen:
            continue
        seen.add(key)
        (praise if s.rating >= 4 else complaints).append(s)
    complaints.sort(key=lambda s: s.rating)

    # 5 — analytics + bundle
    progress("analytics", 92, "Computing gaps, n-grams, and summary")
    try:
        _category_store().record_observed(
            categories_mod.category_intel([b.model_dump() for b in intel],
                                          marketplace=marketplace, store=store_key)["categories"],
            niche=focus)
    except Exception as exc:  # noqa: BLE001 - the catalogue is a nice-to-have
        warnings.append(f"category catalogue not updated: {exc}")
    priced = [b.price for b in books if b.price is not None]
    bundle = {
        "seed": seed,
        "marketplace": marketplace,
        "store": store_key,
        "currency": currency,
        "focus_keyword": focus,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "served_currency": served,
        "currency_ok": currency_ok,
        "summary": {
            "total_results": focus_metrics.total_results if focus_metrics else None,
            "books_scanned": len(books),
            "ku_share_pct": round(sum(1 for b in books if b.kindle_unlimited) / len(books) * 100) if books else None,
            "avg_buy_price": (round(sum(priced) / len(priced), 2)
                              if priced and currency_ok else None),
            "median_reviews": statistics.median([b.reviews for b in books]) if books else None,
            "royalty_pool_month": (round(sum(b.est_monthly_royalty or 0 for b in intel), 2)
                                   if currency_ok else None),
            "velocity_pct_90d": velocity["pct_90d"],
        },
        # Income as a band including KU page reads. The flat
        # `royalty_pool_month` above is kept for backwards compatibility but
        # is paid-sales only and carries false precision; prefer this.
        "income": kdp_estimates.niche_income(
            [{"bsr": b.bsr, "price": b.price} for b in intel],
            ku_share=(sum(1 for b in books if b.kindle_unlimited) / len(books)) if books else 0.0,
            marketplace=marketplace,
            store=store_key,
            currency_ok=currency_ok,
        ),
        "keywords": [m.model_dump() for m in keywords],
        "books": [b.model_dump() for b in books],
        # Per-book royalty obeys the same currency guard as the headline;
        # a live scan once showed INR royalties under a "$" sign here.
        "book_intel": kdp_estimates.money_guard([b.model_dump() for b in intel], currency_ok),
        # Which shelves the niche actually lives on, and what each badge costs.
        "category_intel": categories_mod.category_intel(
            [b.model_dump() for b in intel], marketplace=marketplace, store=store_key),
        # Pricing, launch velocity, praise, listing polish, and buyer flow —
        # all read from data the scan already collected.
        "pricing": pricing_mod.price_intel(
            [{"price": b.price, "bsr": b.bsr} for b in intel] or
            [{"price": b.price, "bsr": None} for b in books], currency_ok=currency_ok),
        "velocity": reviews_mod.shelf_velocity([b.model_dump() for b in intel]),
        "praise": reviews_mod.praise_themes(
            [s.model_dump() for s in praise],
            exclude=f"{seed} {focus} " + " ".join(
                reviews_mod.niche_vocabulary([b.title for b in books]))),
        "listing_benchmark": signals_mod.shelf_benchmark([b.model_dump() for b in intel]),
        "also_viewed": signals_mod.adjacency([b.model_dump() for b in intel]),
        "release_velocity": velocity,
        "title_gaps": [m.keyword for m in find_title_gaps(keywords)],
        "title_ngrams": title_ngrams([b.title for b in books]),
        "complaints": [c.model_dump() for c in complaints],
        "warnings": warnings,
    }
    progress("done", 100, "Research complete")
    return bundle


def _run_demo(progress: ProgressFn) -> dict[str, Any]:
    """Serve the bundled sample dataset through the same stages, instantly demoable."""
    import time

    with open(SAMPLE_PATH) as f:
        raw = json.load(f)
    for stage, pct, msg in [
        ("keywords", 15, "Demo: loading sample keyword validation"),
        ("niche", 45, "Demo: loading sample niche scan"),
        ("deepdive", 70, "Demo: loading sample BSR deep dive"),
        ("analytics", 95, "Demo: assembling sample bundle"),
    ]:
        progress(stage, pct, msg)
        time.sleep(0.6)

    books = raw.get("books", [])
    intel = raw.get("book_intel", [])
    priced = [b["price"] for b in books if b.get("price") is not None]
    keywords = raw.get("keywords", [])
    focus = raw.get("focus_keyword", raw.get("seed", "demo"))
    focus_m = next((k for k in keywords if k["keyword"] == focus), None)
    bundle = {
        "seed": raw.get("seed", "air fryer"),
        "marketplace": raw.get("marketplace", "us"),
        "store": "kindle",
        "currency": "$",
        "focus_keyword": focus,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_results": (focus_m or {}).get("total_results"),
            "books_scanned": len(books),
            "ku_share_pct": round(sum(1 for b in books if b.get("kindle_unlimited")) / len(books) * 100) if books else None,
            "avg_buy_price": round(sum(priced) / len(priced), 2) if priced else None,
            "median_reviews": statistics.median([b.get("reviews", 0) for b in books]) if books else None,
            "royalty_pool_month": round(sum(b.get("est_monthly_royalty") or 0 for b in intel), 2),
            "velocity_pct_90d": raw.get("release_velocity", {}).get("pct_90d", 0),
        },
        "income": kdp_estimates.niche_income(
            [{"bsr": b.get("bsr"), "price": b.get("price")} for b in intel],
            ku_share=(sum(1 for b in books if b.get("kindle_unlimited")) / len(books)) if books else 0.0,
            marketplace=raw.get("marketplace", "us"),
        ),
        "keywords": keywords,
        "books": books,
        "book_intel": intel,
        "release_velocity": raw.get("release_velocity", {}),
        "title_gaps": raw.get("title_gaps", []),
        "title_ngrams": raw.get("title_ngrams", []),
        "complaints": raw.get("complaints", []),
        "warnings": ["Demo dataset (live scrape of 'air fryer', captured earlier) — not a fresh scan."],
    }
    progress("done", 100, "Demo bundle ready")
    return bundle
