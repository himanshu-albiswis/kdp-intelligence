"""Trend Radar v1 — social listening cross-checked against live Amazon.

Sources (graded honestly, per live probing):
  * Google autocomplete  — works everywhere, strongest volume proxy
  * YouTube autocomplete — works everywhere, strong for how-to niches
  * Reddit public JSON   — works from residential IPs; datacenter IPs get
                           403 (Reddit blocks them). Degrades gracefully.
  * X/Twitter            — deliberately excluded: logged-out X serves only a
                           JavaScript wall; access requires a logged-in
                           browser account (ToS/ban risk) or the paid API.

Every surviving candidate is validated against live Amazon (competition,
review moat, book-relevance) through the existing LongTailSpider, then
placed in a GO / ANGLE / VERIFY / AVOID quadrant. Demand here is *breadth*
(how many independent sources surface the phrase) — v2 adds time-series
momentum once scheduled runs accumulate history.
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
from scrapling.fetchers import Fetcher  # noqa: E402

ProgressFn = Callable[[str, int, str], None]

# suffixes that expand a topic into how buyers phrase needs
EXPANSIONS = ["", " book", " for", " how to", " workbook", " guide", " for beginners", " vs"]
JUNK = re.compile(r"\b(pdf|free download|login|near me|reddit|youtube|amazon)\b", re.I)


def _fetch(url: str, impersonate: str) -> Any:
    return Fetcher.get(url, impersonate=impersonate, stealthy_headers=False, timeout=20)


def _suggest(base_params: str, topic: str, impersonate: str) -> list[str]:
    out: list[str] = []
    for suffix in EXPANSIONS:
        q = (topic + suffix).strip()
        try:
            page = _fetch(
                f"https://suggestqueries.google.com/complete/search?client=firefox&{base_params}"
                f"&q={q.replace(' ', '+')}",
                impersonate,
            )
            data = json.loads(page.body if isinstance(page.body, str) else page.body.decode("utf-8", "ignore"))
            out.extend(s for s in data[1] if isinstance(s, str))
        except Exception:
            continue
    return out


def google_suggestions(topic: str, impersonate: str = "edge") -> list[str]:
    return _suggest("hl=en&gl=us", topic, impersonate)


def youtube_suggestions(topic: str, impersonate: str = "edge") -> list[str]:
    return _suggest("ds=yt&hl=en&gl=us", topic, impersonate)


def reddit_signals(topic: str, impersonate: str = "edge") -> dict[str, Any]:
    """Recent Reddit posts asking about the topic. 403 from datacenter IPs."""
    posts, blocked = [], False
    for query in (f'"{topic}" book', f"{topic} recommendations"):
        try:
            page = _fetch(
                f"https://www.reddit.com/search.json?q={query.replace(' ', '+')}"
                "&sort=top&t=month&limit=15",
                impersonate,
            )
            if page.status != 200:
                blocked = True
                continue
            body = page.body if isinstance(page.body, str) else page.body.decode("utf-8", "ignore")
            for child in json.loads(body).get("data", {}).get("children", []):
                d = child.get("data", {})
                posts.append({
                    "title": d.get("title", "")[:160],
                    "subreddit": d.get("subreddit"),
                    "score": d.get("score", 0),
                    "comments": d.get("num_comments", 0),
                    "url": "https://www.reddit.com" + d.get("permalink", ""),
                })
        except Exception:
            blocked = True
    # dedupe by title
    seen, unique = set(), []
    for p in sorted(posts, key=lambda p: -p["score"]):
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)
    return {"posts": unique[:20], "blocked": blocked and not unique}


def _mine_candidates(topic: str, google: list[str], youtube: list[str],
                     reddit_posts: list[dict]) -> list[dict[str, Any]]:
    """Merge sources into ranked candidate phrases with corroboration breadth."""
    topic_words = set(topic.lower().split())
    scores: dict[str, dict[str, Any]] = {}

    def add(phrase: str, source: str, weight: float) -> None:
        phrase = re.sub(r"\s+", " ", phrase.lower().strip())
        if (len(phrase) < 4 or len(phrase) > 80 or JUNK.search(phrase)
                or not topic_words & set(phrase.split())):
            return
        entry = scores.setdefault(phrase, {"phrase": phrase, "sources": set(), "weight": 0.0})
        entry["sources"].add(source)
        entry["weight"] += weight

    for i, s in enumerate(google):
        add(s, "google", 1.0 / (1 + i * 0.1))
    for i, s in enumerate(youtube):
        add(s, "youtube", 0.8 / (1 + i * 0.1))
    for p in reddit_posts:
        # reddit titles are sentences; mine phrases around the topic words
        add(p["title"], "reddit", min(p["score"], 500) / 500)

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

    progress("sources", 10, "Reading Google autocomplete (US)")
    google = google_suggestions(topic, impersonate)
    progress("sources", 25, f"Google: {len(google)} phrases · reading YouTube")
    youtube = youtube_suggestions(topic, impersonate)
    progress("sources", 40, f"YouTube: {len(youtube)} phrases · reading Reddit")
    reddit = reddit_signals(topic, impersonate)
    if reddit["blocked"]:
        warnings.append("Reddit returned 403 (datacenter IP). Configure residential proxies to include "
                        "Reddit demand signals; Google + YouTube still counted.")

    progress("mine", 50, "Merging sources into candidate phrases")
    candidates = _mine_candidates(topic, google, youtube, reddit["posts"])
    if not candidates:
        return {"topic": topic, "generated_at": datetime.now().isoformat(timespec="seconds"),
                "sources": {"google": google, "youtube": youtube, "reddit": reddit},
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
        "sources": {
            "google": google[:40],
            "youtube": youtube[:40],
            "reddit": reddit,
            "excluded": {"x_twitter": "logged-out X serves a JavaScript wall; needs a logged-in "
                                       "account (ToS risk) or the paid API — excluded from v1"},
        },
        "candidates": candidates[:30],
        "validated": validated,
        "warnings": warnings,
    }
