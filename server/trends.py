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
# Phrases that look learnable but are an account action, a booking, or a local
# lookup — nobody publishes a book into these. They arrive mostly from Google
# Trends' regional feed, which surfaced "how to delete instagram account" as a
# GO opportunity in a live seedless scan.
JUNK = re.compile(
    r"\b(pdf|free download|login|log in|sign ?up|near me|reddit|youtube|amazon"
    r"|delete .{0,20}account|book my|my ticket|customer care|helpline"
    r"|price in|net banking|track order|status check|full movie|watch online)\b",
    re.I)


def _phrases(result) -> list[str]:
    """Autocomplete sources yield phrases; Reddit yields post titles.

    A source can be absent entirely (never configured, or a partial harvest),
    which is not the same as present-but-empty.
    """
    if result is None or not result.usable:
        return []
    return [i.get("phrase") or i.get("title") or "" for i in result.items]


# Words that mark a phrase as shopping for an object rather than learning a
# subject. A book niche is about a topic; these are about a thing you buy.
_PRODUCT_WORDS = re.compile(
    r"\b(shoes?|sneakers?|boots?|shirts?|jeans?|watch(es)?|bags?|wallets?"
    r"|phones?|iphone|samsung|galaxy|oneplus|xiaomi|laptop|macbook|tablet"
    r"|headphones?|earbuds?|speakers?|tv|television|fridge|washing machine"
    r"|chair|sofa|mattress|car|bike|scooter|tyres?|price|cheap|discount|deals?"
    r"|buy|shop|store|online|near me|under \d|models?|sale|products?|brands?|company|login|app|game|movie|series|season)\b", re.I)

# A trailing version or model number — "forza horizon 6", "iphone 17 pro max",
# "sony wh-1000xm5". Books rarely carry one; products almost always do.
_MODEL_NUMBER = re.compile(r"\b([a-z]{1,4}-?\d{3,}[a-z\d]*|\d+\s*(pro|max|plus|ultra)?)$", re.I)

# Words that mark something teachable. A phrase with one of these is about a
# subject even if it also mentions an object ("air fryer cookbook").
_LEARNABLE_WORDS = re.compile(
    r"\b(book|books|cookbook|workbook|guide|journal|planner|recipes?|how to"
    r"|for beginners|beginner|learn|learning|tips|handbook|manual|course"
    r"|therapy|exercises?|training|diet|meal prep|routine|habits?|mindset"
    r"|for dummies|step by step|made simple|explained)\b", re.I)


def is_book_shaped(phrase: str) -> bool:
    """Could someone publish a book on this, or is it shopping for an object?

    A live seedless scan returned "formal shoes for men", "forza horizon 6"
    and "forever living products" as opportunities. The root cause was the
    harvest, but product searches reach a seeded scan too, and a tool for book
    publishers should not offer them.
    """
    phrase = (phrase or "").strip().lower()
    if not phrase:
        return False
    if _LEARNABLE_WORDS.search(phrase):
        return True           # explicitly about learning something
    if _PRODUCT_WORDS.search(phrase):
        return False
    if _MODEL_NUMBER.search(phrase):
        return False
    # "forever living products", "forza horizon 6" — a brand or title with no
    # subject in it. Require at least one word suggesting a topic.
    return len(phrase.split()) >= 3


def _harvest_sources(topic: str, collect: Optional[Callable] = None) -> dict:
    """Collect signals for a topic, or across the category panel when there is none.

    Seedless mode used to call the collectors with an empty string, which made
    autocomplete expand bare suffixes and return whatever Google is popular for
    that day. Harvesting the same category seeds Discovery uses gives phrases
    that are actually about something learnable.
    """
    collect = collect or registry.collect_all
    if topic:
        return collect(topic)

    try:
        from .discovery.collectors import CATEGORIES
    except (ImportError, ValueError):  # pragma: no cover - flat import shape
        from discovery.collectors import CATEGORIES  # type: ignore

    merged: dict[str, Any] = {}
    for category, spec in CATEGORIES.items():
        for seed in spec["seeds"][:2]:          # two seeds per category keeps it quick
            for name, result in (collect(seed) or {}).items():
                key = f"{name}:{category}:{seed}"
                merged[key] = result
    return merged


def _looks_learnable(phrase: str) -> bool:
    """Could a book plausibly teach this? Used only when there is no topic.

    A trending term of one or two words is almost always a brand, a person or
    an event — "bookmyshow", "formula 1", "djokovic". A phrase describing
    something learnable is descriptive and therefore longer. Reddit-style
    sentences short of that still pass on an explicit intent marker, which is
    why both checks are here: the intent regex is tuned for sentences and
    rejects perfectly good autocomplete phrases like "adhd for beginners".
    """
    if len(phrase.split()) >= 3:
        return True
    try:
        from .discovery.concepts import has_book_intent
    except (ImportError, ValueError):  # pragma: no cover - flat import shape
        from discovery.concepts import has_book_intent  # type: ignore
    return has_book_intent({"text": phrase, "source": "trends"})


def _mine_candidates(topic: Optional[str], sources: dict) -> list[dict[str, Any]]:
    """Merge sources into ranked candidate phrases with corroboration breadth.

    With a topic, candidates must share a word with it. Without one — the
    seedless mode Discovery already uses — everything harvested is kept and
    only the junk filter applies.
    """
    topic_words = set((topic or "").lower().split())
    scores: dict[str, dict[str, Any]] = {}

    def add(phrase: str, source: str, weight: float) -> None:
        phrase = re.sub(r"\s+", " ", (phrase or "").lower().strip())
        if len(phrase) < 4 or len(phrase) > 80 or JUNK.search(phrase):
            return
        if topic_words:
            # An explicit topic is the user asserting relevance; respect it.
            if not topic_words & set(phrase.split()):
                return
        elif not _looks_learnable(phrase) or not is_book_shaped(phrase):
            # Seedless, nothing else filters. Google Trends' daily feed is news
            # ("bookmyshow", "formula 1"), and a live scan validated those
            # against Amazon and called one a GO. Require book intent instead.
            return
        entry = scores.setdefault(phrase, {"phrase": phrase, "sources": set(), "weight": 0.0})
        entry["sources"].add(source)
        entry["weight"] += weight

    # Seedless harvesting keys results per category seed ("google:health:adhd"),
    # so iterate what was actually collected and label by the engine's own name
    # rather than looking up fixed keys — that mismatch mined zero candidates.
    weights = {"google": 1.0, "youtube": 0.8}
    for result in sources.values():
        if result is None or not result.usable:
            continue
        engine = getattr(result, "name", "") or ""
        if engine == "reddit":
            for item in result.items:
                score = item.get("score")
                # RSS has no scores. Weight those posts by presence alone rather
                # than treating "unknown engagement" as "zero engagement".
                weight = min(score, 500) / 500 if score is not None else 0.3
                add(item.get("title", ""), "reddit", weight)
            continue
        base_weight = weights.get(engine)
        if base_weight is None:
            continue
        for i, item in enumerate(result.items):
            phrase = item.get("phrase") or item.get("text") or ""
            add(phrase, engine, base_weight / (1 + i * 0.1))

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


def _validate_against_amazon(to_validate: dict, store_key: str, marketplace: str,
                             params: dict[str, Any]) -> tuple[dict, list]:
    """Live Amazon cross-check. Split out so it can be stubbed in tests."""
    spider = LongTailSpider(
        candidates=to_validate,
        store=STORES[store_key],
        marketplace=marketplace,
        impersonate=params.get("amazon_impersonate", params.get("impersonate", "edge")),
        stealthy_headers=not params.get("plain_headers", False),
        proxy=params.get("proxy") or None,
        proxies=params.get("proxies") or None,
    )
    spider.start()
    return {m.keyword: m.model_dump() for m in spider.results}, list(spider.failed_keywords)


def run_trend_radar(params: dict[str, Any], progress: ProgressFn) -> dict[str, Any]:
    # No topic is a valid request: report what is trending everywhere.
    topic: str = (params.get("seed") or "").strip()
    scope = topic or "all categories"
    marketplace: str = params.get("marketplace", "us")
    store_key: str = params.get("store", "kindle")
    validate_top: int = int(params.get("validate_top", 8))
    impersonate: str = params.get("impersonate", "edge")
    warnings: list[str] = []

    progress("sources", 10, "Collecting demand signals (Google, YouTube, Reddit, OpenLibrary)")
    sources = _harvest_sources(topic)
    health = registry.health(sources)
    progress("sources", 40, f"{health['ok']} source(s) answered, {len(health['degraded'])} degraded")
    for name in health["degraded"]:
        warnings.append(f"{name}: {sources[name].detail}")

    progress("mine", 50, "Merging sources into candidate phrases")
    candidates = _mine_candidates(topic, sources)
    if not candidates:
        return {"topic": topic, "scope": scope, "generated_at": datetime.now().isoformat(timespec="seconds"),
                "sources": {n: r.as_dict() for n, r in sources.items()},
                "source_health": health,
                "candidates": [], "validated": [],
                "warnings": warnings + ["No candidate phrases mined — try a broader topic."]}

    to_validate = {c["phrase"]: i for i, c in enumerate(candidates[:validate_top])}
    progress("amazon", 60, f"Validating top {len(to_validate)} candidates against live Amazon {marketplace.upper()}")
    validated_by_kw, failed = _validate_against_amazon(to_validate, store_key, marketplace, params)
    if failed:
        warnings.append(f"{len(failed)} Amazon page(s) blocked, left as VERIFY: " + ", ".join(failed))

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
        "scope": scope,
        "marketplace": marketplace,
        "store": store_key,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {n: r.as_dict() for n, r in sources.items()},
        "source_health": health,
        "candidates": candidates[:30],
        "validated": validated,
        "warnings": warnings,
    }
