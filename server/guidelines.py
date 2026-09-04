"""KDP metadata guidelines checker — catch the rejection before Amazon does.

Encodes KDP's published metadata rules: title and subtitle length, seven
keyword slots of at most 50 characters each, a 4,000-character description
limited to a small HTML subset, at most three categories, and the promotional
or time-sensitive words KDP rejects in metadata. Errors are things KDP will
bounce; warnings are things that waste the listing (a keyword slot repeating
the title, an empty slot, a thin description).
"""

import re
from typing import Any

TITLE_MAX = 200
SUBTITLE_MAX = 200
KEYWORD_SLOTS = 7
KEYWORD_MAX = 50
DESCRIPTION_MAX = 4000
DESCRIPTION_THIN = 150
CATEGORY_MAX = 3

# KDP rejects claims it cannot verify and anything promotional in metadata.
PROMOTIONAL = ["free", "bestseller", "best seller", "best-seller", "bestselling",
               "best selling", "#1", "number one", "on sale", "discount", "kindle unlimited",
               "kindle", "amazon", "award winning", "award-winning"]
# Time-sensitive words go stale and are discouraged rather than banned.
TIME_SENSITIVE = ["new", "today", "now", "latest", "2024 edition", "2025 edition", "2026 edition"]
ALLOWED_TAGS = {"b", "i", "u", "em", "strong", "br", "p", "h1", "h2", "h3", "h4", "h5", "h6",
                "ul", "ol", "li"}

_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)[^>]*>")
_WORD = re.compile(r"[a-z0-9']+")


def _issue(field: str, severity: str, rule: str, message: str) -> dict[str, str]:
    return {"field": field, "severity": severity, "rule": rule, "message": message}


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text.lower()) is not None


def _check_text_field(field: str, text: str, limit: int, required: bool,
                      issues: list[dict[str, str]]) -> None:
    text = text or ""
    if required and not text.strip():
        issues.append(_issue(field, "error", "required", f"{field} is required"))
        return
    if len(text) > limit:
        issues.append(_issue(field, "error", "length",
                             f"{field} is {len(text)} characters; KDP allows {limit}"))
    if _TAG.search(text):
        issues.append(_issue(field, "error", "html", f"{field} may not contain HTML"))
    for phrase in PROMOTIONAL:
        if _contains_phrase(text, phrase):
            issues.append(_issue(field, "error", "promotional",
                                 f'"{phrase}" in the {field} — KDP rejects promotional or '
                                 f'unverifiable claims in metadata'))
    for phrase in TIME_SENSITIVE:
        if _contains_phrase(text, phrase):
            issues.append(_issue(field, "warn", "time-sensitive",
                                 f'"{phrase}" in the {field} goes stale; KDP discourages '
                                 f'time-sensitive words'))
    if text.isupper() and len(text) > 8:
        issues.append(_issue(field, "warn", "caps", f"{field} is all capitals"))


def check_listing(listing: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    title = listing.get("title") or ""
    subtitle = listing.get("subtitle") or ""

    _check_text_field("title", title, TITLE_MAX, required=True, issues=issues)
    _check_text_field("subtitle", subtitle, SUBTITLE_MAX, required=False, issues=issues)

    # --- keywords ----------------------------------------------------------
    keywords = [str(k or "").strip() for k in (listing.get("keywords") or [])]
    if len(keywords) > KEYWORD_SLOTS:
        issues.append(_issue("keywords", "error", "slots",
                             f"{len(keywords)} keyword slots; KDP allows {KEYWORD_SLOTS}"))
    filled = [k for k in keywords if k]
    if len(filled) < KEYWORD_SLOTS:
        issues.append(_issue("keywords", "warn", "unused",
                             f"{KEYWORD_SLOTS - len(filled)} keyword slot(s) unused — "
                             f"each is free discoverability"))
    title_words = set(_WORD.findall(f"{title} {subtitle}".lower()))
    seen: set[str] = set()
    for slot in filled:
        low = slot.lower()
        if len(slot) > KEYWORD_MAX:
            issues.append(_issue("keywords", "error", "length",
                                 f'"{slot[:30]}…" is {len(slot)} characters; slots hold {KEYWORD_MAX}'))
        if '"' in slot or "'" in slot and slot.count("'") > 1:
            issues.append(_issue("keywords", "error", "quotes",
                                 f'"{slot}" contains quotation marks; KDP matches phrases without them'))
        if low in seen:
            issues.append(_issue("keywords", "warn", "duplicate", f'"{slot}" appears twice'))
        seen.add(low)
        slot_words = set(_WORD.findall(low))
        if slot_words and slot_words <= title_words:
            issues.append(_issue("keywords", "warn", "wasted",
                                 f'"{slot}" only repeats words already in the title — a '
                                 f'wasted slot; title words are indexed anyway'))
        for phrase in PROMOTIONAL:
            if _contains_phrase(low, phrase):
                issues.append(_issue("keywords", "error", "promotional",
                                     f'"{phrase}" in a keyword slot is rejected'))

    # --- description -------------------------------------------------------
    description = listing.get("description") or ""
    if len(description) > DESCRIPTION_MAX:
        issues.append(_issue("description", "error", "length",
                             f"description is {len(description)} characters; KDP allows {DESCRIPTION_MAX}"))
    bad_tags = sorted({t.lower() for t in _TAG.findall(description) if t.lower() not in ALLOWED_TAGS})
    if bad_tags:
        issues.append(_issue("description", "error", "html",
                             f"disallowed HTML: {', '.join('<' + t + '>' for t in bad_tags)} — "
                             f"KDP permits only {', '.join(sorted(ALLOWED_TAGS))}"))
    plain = _TAG.sub("", description).strip()
    if plain and len(plain) < DESCRIPTION_THIN:
        issues.append(_issue("description", "warn", "thin",
                             f"description is {len(plain)} characters of text; thin descriptions convert poorly"))
    if not plain:
        issues.append(_issue("description", "warn", "empty", "description is empty"))

    # --- categories --------------------------------------------------------
    categories = [c for c in (listing.get("categories") or []) if c]
    if len(categories) > CATEGORY_MAX:
        issues.append(_issue("categories", "error", "count",
                             f"{len(categories)} categories; KDP allows {CATEGORY_MAX}"))
    if not categories:
        issues.append(_issue("categories", "warn", "none", "no categories chosen"))

    errors = sum(1 for i in issues if i["severity"] == "error")
    warns = sum(1 for i in issues if i["severity"] == "warn")
    score = max(0, 100 - 15 * errors - 4 * warns)
    return {"passed": errors == 0, "score": score, "errors": errors, "warnings": warns,
            "issues": issues}
