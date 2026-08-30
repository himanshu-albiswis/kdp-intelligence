"""Reddit demand signal, with the transport chosen by what is available.

Measured from a residential IP on 2026-08-31:

    www.reddit.com/search.json   -> 403   (auth wall, not bot detection)
    www.reddit.com/search.rss    -> 200   (real posts, no auth, rate-limited)
    oauth.reddit.com (with token)-> 200   (full score/comment metadata)

The 403 is why the repo's old advice — "use residential proxies" — never
worked: Reddit removed anonymous JSON API access outright, so no IP and no
browser fingerprint recovers it. A stealth browser was tested against it
and still got 403.

So: use the official OAuth API when the operator supplies credentials
(free tier, full metadata), and fall back to the public RSS feed when they
do not. RSS carries titles and subreddits but no scores, and the items say
so rather than defaulting engagement to zero.
"""

import json
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

from . import base

NAME = "reddit"
USER_AGENT = "kdp-niche-intelligence/1.0 (research tool)"

# Reddit answers 429 when you ask too fast and 403 when the fingerprint or
# the endpoint is disallowed. Only the first is worth retrying; retrying a
# 403 just burns quota against a wall that will not move.
BASE_BACKOFF = 2.0
MAX_BACKOFF = 30.0

_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_LINK = re.compile(r'<link[^>]*href="([^"]+)"')
_CATEGORY = re.compile(r'<category[^>]*term="([^"]+)"')


def _unescape(text: str) -> str:
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    return text.strip()


def _queries(topic: str) -> list[str]:
    return [f'"{topic}" book', f"{topic} recommendations"]


def _collect_oauth(topic: str, fetch: Callable, credentials: dict) -> Optional[list[dict]]:
    """Official API. Returns None if the token handshake fails, so we can fall back."""
    token_response = fetch(
        "https://www.reddit.com/api/v1/access_token",
        method="POST",
        auth=(credentials["client_id"], credentials["client_secret"]),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": USER_AGENT},
    )
    if getattr(token_response, "status", 0) != 200:
        return None
    token = json.loads(base.body_text(token_response)).get("access_token")
    if not token:
        return None

    items: list[dict[str, Any]] = []
    for query in _queries(topic):
        response = fetch(
            "https://oauth.reddit.com/search?"
            f"q={quote_plus(query)}&sort=top&t=year&limit=25",
            headers={"Authorization": f"bearer {token}", "User-Agent": USER_AGENT},
        )
        if getattr(response, "status", 0) != 200:
            continue
        payload = json.loads(base.body_text(response))
        for child in payload.get("data", {}).get("children", []):
            d = child.get("data", {})
            items.append({
                "title": (d.get("title") or "")[:200],
                "subreddit": d.get("subreddit"),
                "score": d.get("score"),
                "comments": d.get("num_comments"),
                "url": "https://www.reddit.com" + (d.get("permalink") or ""),
            })
    return items


def _get(fetch: Callable, url: str, retries: int, sleep: Callable,
         state: dict) -> Any:
    """GET with escalating backoff on 429 only."""
    response = fetch(url)
    for attempt in range(retries):
        if getattr(response, "status", 0) != 429:
            return response
        state["delay"] = min(state["delay"] * 2, MAX_BACKOFF)
        sleep(state["delay"])
        response = fetch(url)
    return response


def _collect_rss(topic: str, fetch: Callable, retries: int,
                 sleep: Callable) -> tuple[list[dict], list[str]]:
    """Public RSS. No engagement numbers, but no auth either."""
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    # One escalating budget for the whole call: if Reddit is throttling us,
    # the next query should not start again from a one-second delay.
    state = {"delay": BASE_BACKOFF / 2}
    for query in _queries(topic):
        # quote_plus, not a space swap: raw quotes in the query string make
        # Reddit answer 403, and a raw & truncates it server-side.
        url = ("https://www.reddit.com/search.rss?"
               f"q={quote_plus(query)}&sort=top&t=year")
        response = _get(fetch, url, retries, sleep, state)
        status = getattr(response, "status", 0)
        if status != 200:
            errors.append(f"HTTP {status}")
            if status == 429:
                # Throttled even after backing off — stop asking. Continuing
                # would deepen the penalty for every later scan.
                errors.append("rate limit persisted; skipped remaining queries")
                break
            continue
        for chunk in _ENTRY.findall(base.body_text(response)):
            title = _TITLE.search(chunk)
            link = _LINK.search(chunk)
            category = _CATEGORY.search(chunk)
            if not title:
                continue
            items.append({
                "title": _unescape(title.group(1))[:200],
                "subreddit": category.group(1) if category else None,
                # RSS genuinely does not carry these; None means unknown,
                # which scoring must not silently read as zero.
                "score": None,
                "comments": None,
                "url": link.group(1) if link else None,
            })
    return items, errors


def _dedupe(items: list[dict]) -> list[dict]:
    ranked = sorted(items, key=lambda i: -(i.get("score") or 0))
    seen, unique = set(), []
    for item in ranked:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def collect(topic: str, *, fetch: Callable,
            credentials: Optional[dict] = None,
            retries: int = 2,
            sleep: Callable = time.sleep) -> base.SourceResult:
    errors: list[str] = []

    if credentials and credentials.get("client_id") and credentials.get("client_secret"):
        try:
            items = _collect_oauth(topic, fetch, credentials)
            if items:
                return base.ok(NAME, _dedupe(items), transport="oauth",
                               detail=f"{len(items)} posts via the official API",
                               evidence_url="https://oauth.reddit.com/search")
            if items is None:
                errors.append("OAuth token handshake failed; fell back to RSS")
        except Exception as exc:  # noqa: BLE001 - any transport failure falls back
            errors.append(f"OAuth error: {exc}")

    try:
        items, rss_errors = _collect_rss(topic, fetch, retries, sleep)
        errors.extend(rss_errors)
    except Exception as exc:  # noqa: BLE001
        return base.unavailable(NAME, f"RSS transport error: {exc}", transport="rss")

    if items:
        detail = f"{len(items)} posts via public RSS (no engagement numbers)"
        if errors:
            detail += " — " + "; ".join(errors)
        return base.ok(NAME, _dedupe(items), transport="rss", detail=detail,
                       evidence_url="https://www.reddit.com/search.rss")

    return base.unavailable(
        NAME,
        "Reddit returned no usable data (" + ("; ".join(errors) or "empty feed") + "). "
        "Anonymous JSON API access is closed; set REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET "
        "for the free official API.",
        transport="rss",
    )
