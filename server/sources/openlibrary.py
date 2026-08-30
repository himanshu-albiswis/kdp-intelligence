"""OpenLibrary: catalogue supply and saturation, independent of Amazon.

A real JSON API, no key, no blocking. It answers a question Amazon cannot:
how large and how old is the published corpus on this subject? A niche with
3,000 catalogued works whose newest entries are five years old is a very
different bet from one with 200 works published mostly last year — and
neither shows up in an Amazon page-1 scrape.

Goodreads was evaluated for this slot and rejected: it renders ratings
client-side, so the HTML yields titles with no demand numbers — nothing
Amazon does not already give us.
"""

import json
from typing import Any, Callable

from . import base

NAME = "openlibrary"
SEARCH = ("https://openlibrary.org/search.json?q={q}&limit=20"
          "&fields=title,first_publish_year,edition_count,want_to_read_count")


def collect(topic: str, *, fetch: Callable) -> base.SourceResult:
    url = SEARCH.format(q=topic.replace(" ", "+"))
    try:
        response = fetch(url)
    except Exception as exc:  # noqa: BLE001
        return base.unavailable(NAME, f"transport error: {exc}")

    status = getattr(response, "status", 0)
    if status != 200:
        return base.unavailable(NAME, f"HTTP {status} from OpenLibrary")

    try:
        payload = json.loads(base.body_text(response))
    except ValueError as exc:
        return base.unavailable(NAME, f"unparseable response: {exc}")

    docs = payload.get("docs", []) or []
    items: list[dict[str, Any]] = [{
        "title": d.get("title"),
        "first_publish_year": d.get("first_publish_year"),
        "edition_count": d.get("edition_count"),
        "want_to_read": d.get("want_to_read_count"),
    } for d in docs]

    years = [d.get("first_publish_year") for d in docs if d.get("first_publish_year")]
    meta = {
        "total_works": payload.get("numFound"),
        "oldest_year": min(years) if years else None,
        "newest_year": max(years) if years else None,
        "sampled": len(items),
    }
    return base.ok(NAME, items, transport="json-api", meta=meta,
                   detail=f"{meta['total_works']} catalogued works on this subject",
                   evidence_url=url)
