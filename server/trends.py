"""Trend Radar — social listening cross-checked against live Amazon.

Sources come from `server.sources`, which grades each one honestly. Probed
2026-08-31 from a residential IP, including through a Camoufox stealth
browser:

  * Google / YouTube autocomplete — work everywhere; strongest volume proxy
  * Reddit  — anonymous JSON API is closed (403 regardless of IP or browser
    fingerprint). Uses the free official OAuth API when credentials are set,
    otherwise the public RSS feed, which carries titles but no scores.
  * OpenLibrary — catalogue supply and saturation, independent of Amazon
  * X/Twitter and TikTok — excluded with recorded evidence; see
    `server.sources.registry.EXCLUDED`. Neither yields data without a paid
    API or ToS-violating signature scraping, and a niche tool that invents
    social proof is worse than one that admits the gap.

Every surviving candidate is validated against live Amazon through the
existing LongTailSpider, then placed in a GO / ANGLE / VERIFY / AVOID
quadrant. Demand here is *breadth* (how many independent sources surface
the phrase) — v2 adds time-series momentum once scheduled runs accumulate
history.
"""

import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Callable, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from kdp_longtail_finder import STORES, LongTailSpider  # noqa: E402

# The app runs as `uvicorn app:app --app-dir server` (flat modules) while the
# tests import `server.trends` (package). Support both import shapes.
try:  # pragma: no cover - import-shape shim
    from .sources import registry  # noqa: E402
except ImportError:  # pragma: no cover
    from sources import registry  # noqa: E402

ProgressFn = Callable[[str, int, str], None]

# suffixes that expand a topic into how buyers phrase needs
EXPANSIONS = ["", " book", " for", " how to", " workbook", " guide", " for beginners", " vs"]
JUNK = re.compile(r"\b(pdf|free download|login|near me|reddit|youtube|amazon)\b", re.I)


def _phrases(result) -> list[str]:
    """Autocomplete sources yield phrases; Reddit yields post titles."""
    if not result.usable:
        return []
    return [i.get("phrase") or i.get("title") or "" for i in result.items]


def _mine_candidates(topic: str, sources: dict) -> list[dict[str, Any]]:
    """Merge sources into ranked candidate phrases with corroboration breadth."""
    topic_words = set(topic.lower().split())
    scores: dict[str, dict[str, Any]] = {}

    def add(phrase: str, source: str, weight: float) -> None:
        phrase = re.sub(r"\s+", " ", (phrase or "").lower().strip())
        if (len(phrase) < 4 or len(phrase) > 80 or JUNK.search(phrase)
                or not topic_words & set(phrase.split())):
            return
        entry = scores.setdefault(phrase, {"phrase": phrase, "sources": set(), "weight": 0.0})
        entry["sources"].add(source)
        entry["weight"] += weight

    for i, phrase in enumerate(_phrases(sources.get("google"))):
        add(phrase, "google", 1.0 / (1 + i * 0.1))
    for i, phrase in enumerate(_phrases(sources.get("youtube"))):
        add(phrase, "youtube", 0.8 / (1 + i * 0.1))

    reddit = sources.get("reddit")
    if reddit is not None and reddit.usable:
        for item in reddit.items:
            score = item.get("score")
            # RSS has no scores. Weight those posts by presence alone rather
            # than treating "unknown engagement" as "zero engagement".
            weight = min(score, 500) / 500 if score is not None else 0.3
            add(item.get("title", ""), "reddit", weight)

    ranked = sorted(scores.values(), key=lambda e: (-len(e["sources"]), -e["weight"]))
    return [{"phrase": e["phrase"], "sources": sorted(e["sources"]),
             "breadth": len(e["sources"]), "weight": round(e["weight"], 2)} for e in ranked]


def _quadrant(m: dict[str, Any]) -> str:
    if "PRODUCT-INTENT" in (m.get("verdict") or ""):
        return "AVOID — product search, not a book niche"
    results = m.get("total_results")
    opp = m.get("opportunity", 0)
    if results is None or m.get("page1_books", 0) == 0:
        return "VERIFY — no clean Amazon read yet"
    if opp >= 55 and results <= 3000:
        return "GO — demand with weak supply"
    if opp >= 40:
        return "ANGLE — enter only with differentiation"
    return "AVOID — crowded for the demand shown"


def run_trend_radar(params: dict[str, Any], progress: ProgressFn) -> dict[str, Any]:
    topic: str = params["seed"].strip()
    marketplace: str = params.get("marketplace", "us")
    store_key: str = params.get("store", "kindle")
    validate_top: int = int(params.get("validate_top", 8))
    impersonate: str = params.get("impersonate", "edge")
    warnings: list[str] = []

    progress("sources", 10, "Collecting demand signals (Google, YouTube, Reddit, OpenLibrary)")
    sources = registry.collect_all(topic, impersonate=impersonate)
    health = registry.health(sources)
    progress("sources", 40, f"{health['ok']} source(s) answered, {len(health['degraded'])} degraded")
    for name in health["degraded"]:
        warnings.append(f"{name}: {sources[name].detail}")

    progress("mine", 50, "Merging sources into candidate phrases")
    candidates = _mine_candidates(topic, sources)
    if not candidates:
        return {"topic": topic, "generated_at": datetime.now().isoformat(timespec="seconds"),
                "sources": {n: r.as_dict() for n, r in sources.items()},
                "source_health": health,
                "candidates": [], "validated": [],
                "warnings": warnings + ["No candidate phrases mined — try a broader topic."]}

    to_validate = {c["phrase"]: i for i, c in enumerate(candidates[:validate_top])}
    progress("amazon", 60, f"Validating top {len(to_validate)} candidates against live Amazon {marketplace.upper()}")
    spider = LongTailSpider(
        candidates=to_validate,
        store=STORES[store_key],
        marketplace=marketplace,
        impersonate=params.get("amazon_impersonate", impersonate),
        stealthy_headers=not params.get("plain_headers", False),
        proxy=params.get("proxy") or None,
        proxies=params.get("proxies") or None,
    )
    spider.start()
    validated_by_kw = {m.keyword: m.model_dump() for m in spider.results}
    if spider.failed_keywords:
        warnings.append(f"{len(spider.failed_keywords)} Amazon page(s) blocked, left as VERIFY: "
                        + ", ".join(spider.failed_keywords))

    progress("assemble", 92, "Placing candidates in the opportunity quadrant")
    validated = []
    for c in candidates[:validate_top]:
        m = validated_by_kw.get(c["phrase"], {})
        row = {**c, **{k: m.get(k) for k in ("total_results", "median_reviews", "pct_under_100",
                                             "ku_share", "phrase_in_titles", "opportunity", "verdict", "url")}}
        row["quadrant"] = _quadrant(m) if m else "VERIFY — no clean Amazon read yet"
        validated.append(row)
    order = {"GO": 0, "ANGLE": 1, "VERIFY": 2, "AVOID": 3}
    validated.sort(key=lambda r: (order.get(r["quadrant"].split(" ")[0], 9), -(r.get("opportunity") or 0)))

    progress("done", 100, "Trend radar complete")
    return {
        "topic": topic,
        "marketplace": marketplace,
        "store": store_key,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {n: r.as_dict() for n, r in sources.items()},
        "source_health": health,
        "candidates": candidates[:30],
        "validated": validated,
        "warnings": warnings,
    }
