"""Product-page signals: listing quality, on-page reviews, also-viewed graph.

All three read the raw product page the deep-dive already fetches, so they
cost no extra requests. Markup facts, captured from a live page on
2026-09-05, that shaped the parsers:

  * The "customers also viewed" carousel ships its ASINs inside JSON in the
    data-a-carousel-options attribute (ajax.id_list, each entry itself a
    JSON string). Titles load later by AJAX, so only ASINs are available
    here; the pipeline resolves titles from books it already knows.
  * A+ content is an aplus_feature_div that can be present but EMPTY. On
    the probed page the div existed with zero modules, so presence is not
    content — modules are.
  * Paginated 4-5 star review pages are login-walled to anonymous visitors
    (HTTP 200, zero cards), but the product page renders ~13 reviews with
    ratings. That is where praise comes from.
"""

import html as html_mod
import json
import re
from statistics import mean
from typing import Any, Optional

_CAROUSEL = re.compile(r'data-a-carousel-options="([^"]+)"')
_REVIEW = re.compile(r'data-hook="review"(.*?)(?=data-hook="review"|$)', re.S)
_STARS = re.compile(r"(\d(?:\.\d)?) out of 5 stars")
_BODY = re.compile(r'data-hook="(?:review-body|reviewRichContentContainer)"[^>]*>(.*?)</(?:span|div)>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_SERIES = re.compile(r"Book (\d+) of (\d+)(?::\s*([^<\n]{2,80}))?")
_DESC = re.compile(r'id="bookDescription_feature_div"(.*?)(?:<div id=|$)', re.S)
_ASIN = re.compile(r"^[A-Z0-9]{10}$")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(_TAGS.sub(" ", fragment))).strip()


def also_viewed_asins(page: str, own_asin: Optional[str] = None) -> list[str]:
    """ASINs Amazon shows alongside this book, in carousel order, deduplicated."""
    found: list[str] = []
    for raw in _CAROUSEL.findall(page or ""):
        try:
            options = json.loads(html_mod.unescape(raw))
        except ValueError:
            continue
        for entry in (options.get("ajax") or {}).get("id_list") or []:
            try:
                asin = json.loads(entry).get("id") if isinstance(entry, str) else entry.get("id")
            except (ValueError, AttributeError):
                continue
            if asin and _ASIN.match(asin) and asin != own_asin and asin not in found:
                found.append(asin)
    return found


def onpage_reviews(page: str) -> list[dict[str, Any]]:
    """Every review rendered on the product page, all star levels."""
    reviews: list[dict[str, Any]] = []
    for chunk in _REVIEW.findall(page or ""):
        stars = _STARS.search(chunk)
        body = _BODY.search(chunk)
        if not stars or not body:
            continue
        text = _text(body.group(1))
        if text:
            reviews.append({"rating": float(stars.group(1)), "body": text[:400]})
    return reviews


def listing_quality(page: str) -> dict[str, Any]:
    """How polished this listing is — the things a competitor can copy."""
    page = page or ""
    desc = _DESC.search(page)
    description_chars = len(_text(desc.group(1))) if desc else 0
    aplus = len(re.findall(r"aplus-module|aplus-v2", page)) > 0
    series_match = _SERIES.search(page)
    series = None
    if series_match:
        series = {"book": int(series_match.group(1)), "of": int(series_match.group(2)),
                  "name": (series_match.group(3) or "").strip() or None}
    look_inside = bool(re.search(r"litb|ebooksSitbLogo", page))
    images = max(len(re.findall(r'"hiRes"', page)), 1 if "media-amazon.com/images/I/" in page else 0)

    score = 0
    score += 35 if description_chars >= 1200 else 25 if description_chars >= 600 else 10 if description_chars >= 150 else 0
    score += 25 if aplus else 0
    score += 15 if look_inside else 0
    score += 15 if images >= 3 else 8 if images >= 1 else 0
    score += 10 if series and series["of"] > 1 else 0

    return {"description_chars": description_chars, "aplus": aplus, "series": series,
            "look_inside": look_inside, "images": images, "score": min(100, score)}


def shelf_benchmark(books: list[dict[str, Any]]) -> dict[str, Any]:
    """Average polish of page 1, so a client knows the bar to clear."""
    qualities = [b.get("quality") for b in books if b.get("quality")]
    if not qualities:
        return {"books": 0, "avg_score": None, "aplus_share_pct": None,
                "read": "No product pages were deep-dived, so no listing benchmark."}
    avg = round(mean(q["score"] for q in qualities))
    aplus_pct = round(100 * sum(1 for q in qualities if q.get("aplus")) / len(qualities))
    lookinside_pct = round(100 * sum(1 for q in qualities if q.get("look_inside")) / len(qualities))
    series_pct = round(100 * sum(1 for q in qualities if q.get("series") and q["series"].get("of", 1) > 1) / len(qualities))
    desc = round(mean(q["description_chars"] for q in qualities))
    return {
        "books": len(qualities), "avg_score": avg, "aplus_share_pct": aplus_pct,
        "look_inside_pct": lookinside_pct, "series_pct": series_pct, "avg_description_chars": desc,
        "read": (f"{len(qualities)} books benchmarked: average polish {avg}/100, {aplus_pct}% "
                 f"carry A+ content, {series_pct}% belong to a series, descriptions average "
                 f"{desc:,} characters."),
    }


def adjacency(books: list[dict[str, Any]]) -> dict[str, Any]:
    """Who buyers flow between on this shelf, from the also-viewed carousels.

    Counts how many of our page-1 books point at each ASIN; a neighbour
    named by many is a gravitational centre of the shelf, whether or not it
    ranked for our keyword. Titles resolve from books we already scanned.
    """
    titles = {b.get("asin"): b.get("title") for b in books if b.get("asin")}
    counts: dict[str, int] = {}
    for book in books:
        for asin in book.get("also_viewed") or []:
            counts[asin] = counts.get(asin, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    inside = [a for a, _ in ranked if a in titles]
    return {
        "neighbours": [{"asin": a, "named_by": n, "title": titles.get(a),
                        "on_page_1": a in titles,
                        "url": f"https://www.amazon.com/dp/{a}"} for a, n in ranked[:25]],
        "page1_cohesion_pct": round(100 * len(inside) / len(ranked)) if ranked else None,
        "read": (f"{len(ranked)} neighbouring books named across the shelf; "
                 f"{len(inside)} of them are on page 1 for this keyword. "
                 f"Neighbours off page 1 are the adjacent niches buyers drift into.")
        if ranked else "No also-viewed data was captured.",
    }
