"""Seedless harvesting — the demand side of Discovery Mode.

You give it a time window and optionally a category; it goes looking for what
people are asking for, with no keyword from you.

Collector reachability, probed live on 2026-08-31 from a residential IP:

  * Amazon **Hot New Releases** — 200, 32 ASINs with titles. Usable, and it is
    the Amazon signal this module actually uses.
  * Amazon **Movers & Shakers** — 200 but only 2 ASINs and no titles, and a
    Camoufox stealth browser returned the same. The list is lazy-loaded behind
    interaction, so the "24h rank jump" feed is not reachable. Substituted with
    Hot New Releases, which is fresh-supply rather than movement, and labelled
    as such rather than pretending it is the same signal.
  * Google Trends daily RSS — 200 with 10 items, but they are news cycles
    ("rays", "djokovic", "sport boys"), not learnable intent. Kept at low
    weight and flagged in its own detail string so nobody reads it as demand
    for a book.
  * Reddit — 403 on both the JSON API and RSS for subreddit feeds. The panel
    therefore requires free official API credentials, which also happen to be
    the only way to get the upvote and comment counts that intensity scoring
    needs. Without them this collector reports `not_configured`, never zero.

Every signal carries the URL it came from, so a concept built on top of it can
always be traced back to the thread or listing that produced it.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

try:  # pragma: no cover - the app runs flat (--app-dir server), tests as a package
    from ..sources import base
except ImportError:  # pragma: no cover
    from sources import base

USER_AGENT = "kdp-niche-intelligence/1.0 (discovery)"

# window -> (reddit span, days)
WINDOWS: dict[str, tuple[str, int]] = {
    "24h": ("day", 1),
    "7d": ("week", 7),
    "30d": ("month", 30),
}

# The demand panel. Subreddits were chosen for people describing problems in
# their own words — that is where "is there a book about…" actually lives.
# `seeds` drive autocomplete; `node` is the Amazon browse node for the
# category's new-releases page.
CATEGORIES: dict[str, dict[str, Any]] = {
    "health": {
        "subreddits": ["ADHD", "Anxiety", "Menopause", "ChronicPain", "EatingDisorders",
                       "sleep", "Biohackers"],
        "seeds": ["adhd", "anxiety", "menopause", "chronic pain", "gut health"],
        "node": "154606011",
    },
    "self_help": {
        "subreddits": ["selfimprovement", "getdisciplined", "DecidingToBeBetter",
                       "productivity", "socialskills"],
        "seeds": ["habits", "discipline", "confidence", "burnout", "focus"],
        "node": "154607011",
    },
    "cooking": {
        "subreddits": ["Cooking", "MealPrepSunday", "EatCheapAndHealthy",
                       "slowcooking", "airfryer"],
        "seeds": ["meal prep", "air fryer", "slow cooker", "budget meals", "high protein"],
        "node": "156104011",
    },
    "finance": {
        "subreddits": ["personalfinance", "FinancialPlanning", "Frugal", "investing"],
        "seeds": ["budgeting", "investing for beginners", "debt payoff", "retirement"],
        "node": "154684011",
    },
    "kids": {
        "subreddits": ["daddit", "Mommit", "Parenting", "toddlers", "specialed"],
        "seeds": ["parenting", "toddler behaviour", "picky eater", "autism parenting"],
        "node": "156111011",
    },
    "career": {
        "subreddits": ["careerguidance", "jobs", "cscareerquestions", "smallbusiness"],
        "seeds": ["career change", "interview", "freelancing", "side hustle"],
        "node": "154674011",
    },
}


def reddit_span(window: str) -> str:
    if window not in WINDOWS:
        raise ValueError(f"unsupported window {window!r}; use one of {sorted(WINDOWS)}")
    return WINDOWS[window][0]


def window_days(window: str) -> int:
    if window not in WINDOWS:
        raise ValueError(f"unsupported window {window!r}; use one of {sorted(WINDOWS)}")
    return WINDOWS[window][1]


def selected_categories(names: Optional[list[str]]) -> list[str]:
    """Requested categories that actually exist; unknown names are ignored."""
    if not names:
        return list(CATEGORIES)
    return [n for n in names if n in CATEGORIES]


def _signal(text: str, source: str, url: str, intensity: float,
            category: Optional[str] = None, **extra: Any) -> dict[str, Any]:
    return {"text": text, "source": source, "url": url,
            "intensity": intensity, "category": category, **extra}


# --------------------------------------------------------------------------
# Google Trends
# --------------------------------------------------------------------------
_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_TRAFFIC = re.compile(r"<ht:approx_traffic>(.*?)</ht:approx_traffic>", re.S)
_NEWS = re.compile(r"<ht:news_item_title>(.*?)</ht:news_item_title>", re.S)


def _traffic_number(text: str) -> float:
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else 0.0


def google_trends(*, fetch: Callable, geo: str = "US") -> base.SourceResult:
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    try:
        response = fetch(url)
    except Exception as exc:  # noqa: BLE001
        return base.unavailable("google_trends", f"transport error: {exc}")
    if getattr(response, "status", 0) != 200:
        return base.unavailable("google_trends", f"HTTP {getattr(response,'status','?')}")

    body = base.body_text(response)
    items: list[dict[str, Any]] = []
    for chunk in _ITEM.findall(body):
        title = _TITLE.search(chunk)
        if not title:
            continue
        headlines = _NEWS.findall(chunk)
        items.append(_signal(
            title.group(1).strip(), "google_trends", url,
            _traffic_number(_TRAFFIC.search(chunk).group(1) if _TRAFFIC.search(chunk) else ""),
            headlines=[h.strip() for h in headlines],
        ))
    if not items:
        return base.unavailable("google_trends", "feed returned no items")
    return base.ok(
        "google_trends", items, transport="rss", evidence_url=url,
        detail=(f"{len(items)} trending searches — note this feed is news-shaped "
                f"(sports and current events dominate), so it is weighted low and "
                f"most items are expected to fail the book-intent filter"),
    )


# --------------------------------------------------------------------------
# Amazon Hot New Releases
# --------------------------------------------------------------------------
# Amazon serves: href="/Slug-Words/dp/ASIN/ref=zg_..." then a nested <div>
# before the <img alt="Title">. The ASIN is a path segment, not the end of the
# href, and the img is not an immediate sibling. `[^<]*` between tags keeps the
# match from running past this listing into the next one.
_ANCHOR = re.compile(
    r'href="[^"]*?/dp/([A-Z0-9]{10})[^"]*"[^>]*>(?:\s*<div[^>]*>){0,3}\s*<img[^>]*alt="([^"]{6,140})"',
    re.S)


def amazon_new_releases(categories: list[str], *, fetch: Callable) -> base.SourceResult:
    """Fresh supply per category — what publishers just bet on.

    This replaces Movers & Shakers, which is lazy-loaded and yielded nothing.
    It answers a different question — new supply rather than rank movement —
    and the scoring layer treats it accordingly.
    """
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()

    for category in selected_categories(categories):
        node = CATEGORIES[category].get("node", "")
        url = f"https://www.amazon.com/gp/new-releases/digital-text/{node}"
        try:
            response = fetch(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{category}: {exc}")
            continue
        if getattr(response, "status", 0) != 200:
            errors.append(f"{category}: HTTP {getattr(response,'status','?')}")
            continue
        for asin, title in _ANCHOR.findall(base.body_text(response)):
            if asin in seen:
                continue
            seen.add(asin)
            items.append(_signal(title.strip(), "amazon_new_releases",
                                 f"https://www.amazon.com/dp/{asin}", 1.0,
                                 category=category, asin=asin))

    if not items:
        return base.unavailable("amazon_new_releases",
                                "; ".join(errors) or "no listings parsed")
    detail = f"{len(items)} fresh listings across {len(selected_categories(categories))} categories"
    if errors:
        detail += " — " + "; ".join(errors[:3])
    return base.ok("amazon_new_releases", items, transport="html", detail=detail)


# --------------------------------------------------------------------------
# Reddit demand panel
# --------------------------------------------------------------------------
# Phrasings that mark someone looking for a book that may not exist.
WANT_PATTERNS = re.compile(
    r"\b(is there a book|any book|book recommend|recommend(ations)? for|"
    r"how do i learn|where do i start|anyone know a|looking for a (book|guide)|"
    r"wish there was|beginner'?s? guide|resources for)\b", re.I)


def reddit_panel(window: str, categories: Optional[list[str]], *,
                 fetch: Callable, credentials: Optional[dict]) -> base.SourceResult:
    span = reddit_span(window)
    if not (credentials and credentials.get("client_id") and credentials.get("client_secret")):
        return base.not_configured(
            "reddit_panel",
            "Reddit blocks anonymous API and RSS access (403 on both), and upvote "
            "and comment counts — which intensity scoring needs — are only "
            "available through the official API. Set REDDIT_CLIENT_ID and "
            "REDDIT_CLIENT_SECRET (free, 2 minutes at reddit.com/prefs/apps).")

    try:
        token_response = fetch(
            "https://www.reddit.com/api/v1/access_token", method="POST",
            auth=(credentials["client_id"], credentials["client_secret"]),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT})
    except Exception as exc:  # noqa: BLE001
        return base.unavailable("reddit_panel", f"token request failed: {exc}")
    if getattr(token_response, "status", 0) != 200:
        return base.unavailable(
            "reddit_panel",
            f"token handshake returned HTTP {getattr(token_response,'status','?')} — check the credentials")
    token = json.loads(base.body_text(token_response)).get("access_token")
    if not token:
        return base.unavailable("reddit_panel", "token response carried no access_token")

    headers = {"Authorization": f"bearer {token}", "User-Agent": USER_AGENT}
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    for category in selected_categories(categories):
        for subreddit in CATEGORIES[category]["subreddits"]:
            url = (f"https://oauth.reddit.com/r/{quote_plus(subreddit)}/top"
                   f"?t={span}&limit=25")
            try:
                response = fetch(url, headers=headers)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"r/{subreddit}: {exc}")
                continue
            if getattr(response, "status", 0) != 200:
                errors.append(f"r/{subreddit}: HTTP {getattr(response,'status','?')}")
                continue
            for child in json.loads(base.body_text(response)).get("data", {}).get("children", []):
                d = child.get("data", {})
                title = (d.get("title") or "").strip()
                if not title:
                    continue
                body_text = (d.get("selftext") or "")[:400]
                items.append(_signal(
                    title[:300], "reddit", "https://www.reddit.com" + (d.get("permalink") or ""),
                    float(d.get("score") or 0), category=category,
                    subreddit=d.get("subreddit"), comments=int(d.get("num_comments") or 0),
                    body=body_text,
                    asking=bool(WANT_PATTERNS.search(f"{title} {body_text}")),
                ))

    if not items:
        return base.unavailable("reddit_panel", "; ".join(errors[:5]) or "no posts returned")
    asking = sum(1 for i in items if i.get("asking"))
    detail = (f"{len(items)} posts from {sum(len(CATEGORIES[c]['subreddits']) for c in selected_categories(categories))} "
              f"subreddits over the last {window}; {asking} read as someone asking for a book")
    if errors:
        detail += f" — {len(errors)} subreddit(s) failed"
    return base.ok("reddit_panel", items, transport="oauth", detail=detail)


# --------------------------------------------------------------------------
# YouTube autocomplete on category seeds
# --------------------------------------------------------------------------
HOW_TO = ["how to ", "learn ", "beginner ", "guide to "]


def youtube_rising(categories: Optional[list[str]], *, fetch: Callable,
                   max_workers: int = 8) -> base.SourceResult:
    """Autocomplete across every category seed, fanned out.

    This is the widest collector — categories x seeds x prefixes — and run
    serially it dominated harvest time. The requests are independent, so they
    go out concurrently; one failing host loses only its own phrase.
    """
    jobs: list[tuple[str, str]] = []
    for category in selected_categories(categories):
        for seed in CATEGORIES[category]["seeds"]:
            for prefix in HOW_TO:
                query = quote_plus(f"{prefix}{seed}")
                jobs.append((category,
                             "https://suggestqueries.google.com/complete/search?client=firefox"
                             f"&ds=yt&hl=en&gl=us&q={query}"))
    if not jobs:
        return base.unavailable("youtube", "no category seeds to query")

    items: list[dict[str, Any]] = []
    errors: list[str] = []

    def one(job: tuple[str, str]) -> tuple[str, str, Any]:
        category, url = job
        try:
            response = fetch(url)
        except Exception as exc:  # noqa: BLE001
            return category, url, exc
        return category, url, response

    with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as pool:
        for category, url, outcome in pool.map(one, jobs):
            if isinstance(outcome, Exception):
                errors.append(str(outcome)[:50])
                continue
            if getattr(outcome, "status", 0) != 200:
                errors.append(f"HTTP {getattr(outcome, 'status', '?')}")
                continue
            try:
                data = json.loads(base.body_text(outcome))
            except ValueError as exc:
                errors.append(str(exc)[:50])
                continue
            for position, phrase in enumerate(data[1] if len(data) > 1 else []):
                if isinstance(phrase, str):
                    items.append(_signal(phrase, "youtube", url,
                                         max(0.2, 1.0 - position * 0.08),
                                         category=category))

    if not items:
        return base.unavailable("youtube", "; ".join(errors[:3]) or "no suggestions")
    detail = f"{len(items)} how-to phrasings across category seeds"
    if errors:
        detail += f" — {len(errors)} of {len(jobs)} queries failed"
    return base.ok("youtube", items, transport="autocomplete", detail=detail)
