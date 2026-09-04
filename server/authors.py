"""Author tracker — who owns this shelf, and how prolific are they.

An author page (amazon.com/<Name>/e/<ID>) embeds a JSON catalog: one record
per book with detailPageLinkURL, a title, and a customerReviewsSummary. The
"most popular" tile carries the full title in its link's aria-label.
Publication dates are not on the page, so cadence is computed only from
dates we already learned in deep-dives — never estimated from thin air —
and reads "unknown" until two dated books exist.
"""

import json
import re
from datetime import date
from typing import Any, Optional

_TITLE_TAG = re.compile(r"<title>\s*Amazon\.[a-z.]+:\s*([^:<]+?):\s*books", re.I)
_RECORD = re.compile(
    r'"customerReviewsSummary":\{"rating":\{[^}]*?"value":([\d.]+)\}[^}]*?"count":\{[^}]*?"value":(\d+)\}\}'
    r'[^{}]{0,200}?"detailPageLinkURL":"([^"]*?/dp/([A-Z0-9]{10}))"(?:[^{}]{0,300}?"title":"([^"]{3,200})")?',
    re.S)
_TILE = re.compile(r'href="[^"]*?/dp/([A-Z0-9]{10})[^"]*"[^>]*aria-label="([^"]{3,200})"')
_ANY_DP = re.compile(r'href="(/[^"]*?)/dp/([A-Z0-9]{10})')
_BIO = re.compile(r'(?:"biography"|"bio")\s*:\s*"([^"]{40,1500})"')
_SERIES_IN_TITLE = re.compile(r"\bBook \d+\b|\(.*?Book \d+\)|Series\b", re.I)


def _title_from_slug(path: str) -> str:
    slug = path.strip("/").split("/")[0]
    words = [w for w in slug.split("-") if w.lower() not in ("ebook", "book")]
    return " ".join(words).strip()


def parse_author_page(page: str) -> dict[str, Any]:
    page = page or ""
    name_match = _TITLE_TAG.search(page)
    name = name_match.group(1).strip() if name_match else None

    books: dict[str, dict[str, Any]] = {}
    for rating, count, url, asin, title in _RECORD.findall(page):
        books[asin] = {"asin": asin, "title": title or _title_from_slug(url),
                       "rating": float(rating), "reviews": int(count),
                       "url": f"https://www.amazon.com/dp/{asin}"}
    for asin, label in _TILE.findall(page):
        entry = books.setdefault(asin, {"asin": asin, "title": None, "rating": None,
                                        "reviews": None, "url": f"https://www.amazon.com/dp/{asin}"})
        if not entry["title"] or len(label) > len(entry["title"]):
            entry["title"] = label.strip()
    for path, asin in _ANY_DP.findall(page):
        books.setdefault(asin, {"asin": asin, "title": _title_from_slug(path) or None,
                                "rating": None, "reviews": None,
                                "url": f"https://www.amazon.com/dp/{asin}"})

    bio_match = _BIO.search(page)
    bio = bio_match.group(1).replace("\\n", " ").strip() if bio_match else None
    return {"name": name, "catalog": list(books.values()), "bio": bio}


def profile(page: dict[str, Any], known_dates: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Catalog size, review mass, series habit, and cadence where dates exist."""
    catalog = page.get("catalog") or []
    known_dates = known_dates or {}
    reviews = [b["reviews"] for b in catalog if b.get("reviews") is not None]
    series = sum(1 for b in catalog if b.get("title") and _SERIES_IN_TITLE.search(b["title"]))

    dated = sorted(date.fromisoformat(d[:10]) for a, d in known_dates.items()
                   if a in {b["asin"] for b in catalog} and d)
    if len(dated) >= 2:
        span_months = (dated[-1] - dated[0]).days / 30.44
        cadence = {"books_dated": len(dated),
                   "months_between": round(span_months / (len(dated) - 1), 1),
                   "first": dated[0].isoformat(), "latest": dated[-1].isoformat()}
    else:
        cadence = {"books_dated": len(dated), "months_between": None,
                   "first": None, "latest": None}

    size = len(catalog)
    read = f"{page.get('name') or 'This author'} has {size} book(s) on Amazon"
    if reviews:
        read += f" with {sum(reviews):,} reviews in total"
    if series:
        read += f"; {series} title(s) belong to a series"
    if cadence["months_between"] is not None:
        read += f"; publishes roughly every {cadence['months_between']:g} months"
    read += "."

    return {"name": page.get("name"), "catalog_size": size,
            "total_reviews": sum(reviews) if reviews else 0,
            "avg_rating": round(sum(b["rating"] for b in catalog if b.get("rating")) /
                                max(1, sum(1 for b in catalog if b.get("rating"))), 2)
            if any(b.get("rating") for b in catalog) else None,
            "series_titles": series, "cadence": cadence, "bio": page.get("bio"),
            "catalog": catalog[:40], "read": read}
