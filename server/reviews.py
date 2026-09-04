"""Review velocity and praise mining.

Complaint mining (1-3 stars) already tells a new entrant what to fix. Praise
mining (4-5 stars) tells them what they cannot skip — the features buyers
reward on this shelf. Velocity turns a raw review count into a launch-
strength signal by dividing by months since publication; 500 reviews on a
2019 book and 500 on a book from March are very different markets.
"""

import re
from collections import Counter
from datetime import date
from statistics import median
from typing import Any, Optional

STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "it", "its", "this", "that", "these", "those",
    "i", "my", "me", "we", "our", "you", "your", "they", "them", "so", "very",
    "really", "just", "have", "has", "had", "be", "been", "as", "at", "by",
    "from", "book", "books", "one", "all", "also", "can", "will", "would",
    "loved", "love", "great", "good", "nice", "amazing", "excellent",
    "every", "most", "make", "made", "makes", "use", "used", "using", "get", "got",
    "like", "well", "much", "many", "some", "lot", "lots", "time", "times", "thing",
    "things", "night", "day", "days", "way", "even", "than", "then", "when", "what",
    "which", "who", "how", "there", "here", "too", "into", "out", "up", "down",
    "more", "other", "only", "new", "own", "far", "quick", "found",
}

VELOCITY_BANDS = [(20.0, "surging"), (5.0, "steady"), (1.0, "slow"), (0.0, "stale")]


def band_for(per_month: Optional[float]) -> str:
    if per_month is None:
        return "unknown"
    for floor, name in VELOCITY_BANDS:
        if per_month >= floor:
            return name
    return "stale"


def velocity(reviews: Optional[int], publication_date: Optional[str],
             today: Optional[date] = None) -> dict[str, Any]:
    """Reviews per month since publication. Unknown when the date is."""
    today = today or date.today()
    if not publication_date or reviews is None:
        return {"per_month": None, "months": None, "band": "unknown"}
    try:
        published = date.fromisoformat(publication_date[:10])
    except ValueError:
        return {"per_month": None, "months": None, "band": "unknown"}
    days = max((today - published).days, 1)
    months = max(days / 30.44, 0.25)          # a launch week is not zero months
    per_month = round(reviews / months, 2)
    return {"per_month": per_month, "months": round(months, 1), "band": band_for(per_month)}


def shelf_velocity(intel: list[dict[str, Any]], today: Optional[date] = None) -> dict[str, Any]:
    rates = [velocity(b.get("reviews"), b.get("publication_date"), today)["per_month"]
             for b in intel]
    rates = [r for r in rates if r is not None]
    if not rates:
        return {"books_measured": 0, "median_per_month": None, "band": "unknown"}
    mid = round(median(rates), 2)
    return {"books_measured": len(rates), "median_per_month": mid, "band": band_for(mid)}


def niche_vocabulary(titles: list[str], share: float = 0.4) -> set[str]:
    """Words present in at least `share` of page-1 titles — the genre's own
    words, which reviews repeat without praising anything."""
    if not titles:
        return set()
    counts: Counter = Counter()
    for title in titles:
        for word in set(re.findall(r"[a-z']+", (title or "").lower())):
            if word not in STOP and len(word) > 2:
                counts[word] += 1
    return {w for w, n in counts.items() if n / len(titles) >= share}


def _phrases(text: str) -> set[str]:
    """Content words and 2-3 word phrases, one set per review.

    Stopwords stay inside phrases ("easy to follow" must survive intact);
    a phrase is only rejected when it starts or ends on one ("the photos").
    Single content words are kept too, so a feature praised in different
    wording across reviews ("beautiful photos", "photos are great") still
    surfaces as a theme.
    """
    words = re.findall(r"[a-z']+", text.lower())
    found: set[str] = set()
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if gram[0] in STOP or gram[-1] in STOP:
                continue
            if n == 1 and len(gram[0]) <= 3:
                continue
            found.add(" ".join(gram))
    return found


def praise_themes(snippets: list[dict[str, Any]], limit: int = 8,
                  exclude: str = "") -> list[dict[str, Any]]:
    """What 4-5 star reviews keep saying — phrases repeated across reviews.

    Counting each phrase once per review (not per occurrence) means a theme
    ranks by how many buyers raised it, not how often one buyer repeated it.
    `exclude` is the niche's own words: a scan for "air fryer cookbook"
    surfaced 'recipes' and 'air fryer' as its top praise, which is the topic
    restated, not a feature buyers reward.
    """
    praise = [s for s in snippets if (s.get("rating") or 0) >= 4 and s.get("body")]
    if not praise:
        return []
    excluded = set(re.findall(r"[a-z']+", (exclude or "").lower()))
    # "recipes" excluded must exclude "recipe" too
    excluded |= {w[:-1] for w in excluded if w.endswith("s")} | {w + "s" for w in excluded}
    counts: Counter = Counter()
    example: dict[str, str] = {}
    for snippet in praise:
        for phrase in _phrases(snippet["body"]):
            if all(w in excluded or w in STOP for w in phrase.split()):
                continue
            counts[phrase] += 1
            example.setdefault(phrase, snippet["body"][:160])

    themes = [{"theme": phrase, "mentions": n, "example": example[phrase]}
              for phrase, n in counts.most_common() if n >= 2]
    # a trigram that contains a ranked bigram is the same idea; keep the longer
    kept: list[dict[str, Any]] = []
    for theme in themes:
        if any(theme["theme"] in k["theme"] and theme["mentions"] == k["mentions"] for k in kept):
            continue
        kept.append(theme)
    # A phrase names a feature; a lone word usually names the genre. Boost
    # phrases so "easy to follow" outranks "easy" at similar counts.
    kept.sort(key=lambda t: (-t["mentions"] * (1.0 if " " in t["theme"] else 0.6),
                             -len(t["theme"])))
    return kept[:limit]
