"""Google and YouTube autocomplete — the demand proxy that still works.

Suggestions are what real people type, so presence and position are a
popularity signal. Both endpoints answered from a plain residential IP in
testing, which is why they carry the most weight in the merge.
"""

import json
from typing import Callable
from urllib.parse import quote_plus

from . import base

EXPANSIONS = ["", " book", " for", " how to", " workbook", " guide",
              " for beginners", " vs"]


def _suggest(name: str, params: str, topic: str, fetch: Callable) -> base.SourceResult:
    phrases: list[str] = []
    errors: list[str] = []
    for suffix in EXPANSIONS:
        query = quote_plus((topic + suffix).strip())
        url = (f"https://suggestqueries.google.com/complete/search?client=firefox"
               f"&{params}&q={query}")
        try:
            response = fetch(url)
            if getattr(response, "status", 0) != 200:
                errors.append(f"HTTP {getattr(response, 'status', '?')}")
                continue
            data = json.loads(base.body_text(response))
            phrases.extend(s for s in data[1] if isinstance(s, str))
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:60])

    if not phrases:
        return base.unavailable(name, "; ".join(errors) or "no suggestions returned")
    items = [{"phrase": p, "position": i} for i, p in enumerate(phrases)]
    return base.ok(name, items, transport="autocomplete",
                   detail=f"{len(phrases)} suggestions across {len(EXPANSIONS)} expansions")


def google(topic: str, *, fetch: Callable) -> base.SourceResult:
    return _suggest("google", "hl=en&gl=us", topic, fetch)


def youtube(topic: str, *, fetch: Callable) -> base.SourceResult:
    return _suggest("youtube", "ds=yt&hl=en&gl=us", topic, fetch)
