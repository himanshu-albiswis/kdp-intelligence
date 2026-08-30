"""Reverse-ASIN teardown — one competitor book, one structured row.

You paste ASINs or Amazon links; each becomes a row describing how that book
is positioned and how crowded the shelf it competes on actually is.

Every value is either scraped from the product page, computed from the
existing estimates engine, or written by the model from the scraped page and
nothing else. A field that cannot be grounded stays empty: a blank Credential
means the page did not say, not that the author lacks one, and an absent ISBN
is a fact about a Kindle-only title rather than a parse failure.

Patterns here are taken from the live page rather than guessed. Two caught
earlier attempts out:

  * "Best Sellers Rank: #33,125 in Kindle Store" — a colon and a hash sit
    between the label and the number, and the category ranks that follow
    ("#2 in Latin American Cooking") are far more tempting to a loose regex
    than the store-wide rank you actually want.
  * "Publication date September 16, 2020" is only contiguous once tags are
    stripped; in raw HTML the label and value are separated by markup.
"""

import html as html_mod
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable, Optional

AMAZON_HOST = "https://www.amazon.com"

_ASIN = re.compile(r"\b(B[0-9A-Z]{9})\b")
_TITLE = re.compile(r'id="productTitle"[^>]*>\s*([^<]{2,300})', re.S)
_AUTHOR = re.compile(r'/e/B[A-Z0-9]{9}[^>]*>\s*([A-Za-z][A-Za-z.\'\- ]{2,40})\s*<')
_AUTHOR_FALLBACK = re.compile(r'class="author[^"]*".{0,200}?>\s*([A-Za-z][A-Za-z.\'\- ]{2,40})\s*<', re.S)
_RATING = re.compile(r"([\d.]+)\s+out of 5 stars")
# The product page says "491 global ratings"; a related-products carousel
# says "62 ratings". Both are the same field.
_REVIEWS = re.compile(r"([\d,]+)\s+(?:global\s+)?ratings?\b")
_PAGES = re.compile(r"(?:Print length|Page count)\D{0,20}([\d,]+)\s*pages", re.I)
_PAGES_LOOSE = re.compile(r"([\d,]+)\s*pages", re.I)
_PUBDATE = re.compile(r"Publication date\D{0,40}([A-Za-z]+ \d{1,2}, (\d{4}))")
_YEAR_LOOSE = re.compile(r"Publication date\D{0,40}(\d{4})")
# The store-wide rank only. Anchored on the label so the category ranks that
# follow it cannot be mistaken for it.
_BSR = re.compile(r"Best Sellers Rank[:\s]*#?([\d,]+)\s+in\s+(?:Kindle Store|Books)", re.I)
_CATEGORY_RANK = re.compile(r"#([\d,]+)\s+in\s+([A-Za-z][A-Za-z'&,\- ]{3,45})")
_ISBN13 = re.compile(r"ISBN-13\D{0,20}([\d][\d\-]{11,19})")
_ISBN10 = re.compile(r"ISBN-10\D{0,20}([\dX][\dX\-]{8,16})")

FORMATS = ("Kindle", "Paperback", "Hardcover", "Audible Audiobook", "Audiobook",
           "Spiral-bound", "Board book")

SUBTITLE_MAX = 120


def _int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_asin(token: str) -> Optional[str]:
    """An ASIN from a bare code or any Amazon URL shape."""
    if not token:
        return None
    match = _ASIN.search(token.strip().upper())
    return match.group(1) if match else None


def parse_identifiers(pasted: str) -> list[str]:
    """Every ASIN in a pasted blob, deduplicated, in the order given."""
    seen: list[str] = []
    for raw in re.split(r"[\s,;]+", pasted or ""):
        asin = parse_asin(raw)
        if asin and asin not in seen:
            seen.append(asin)
    return seen


def _first(pattern: re.Pattern, text: str, group: int = 1) -> Optional[str]:
    match = pattern.search(text or "")
    return match.group(group).strip() if match else None


def parse_book(html: str, text: str, asin: str) -> dict[str, Any]:
    """Scraped fields only. Anything the page does not state stays None."""
    full_title = _first(_TITLE, html)
    title, subtitle = full_title, None
    if full_title:
        full_title = html_mod.unescape(re.sub(r"\s+", " ", full_title)).strip()
        if ":" in full_title:
            head, _, tail = full_title.partition(":")
            title, subtitle = head.strip(), tail.strip()[:SUBTITLE_MAX] or None
        else:
            title = full_title

    author = _first(_AUTHOR, html) or _first(_AUTHOR_FALLBACK, html)

    rating_text = _first(_RATING, text)
    pages = _int(_first(_PAGES, text)) or _int(_first(_PAGES_LOOSE, text))
    year = _int(_first(_PUBDATE, text, group=2)) or _int(_first(_YEAR_LOOSE, text))

    category_rank = None
    for rank, category in _CATEGORY_RANK.findall(text or ""):
        head = category.strip()
        if head.split()[0] not in ("Kindle", "Books"):   # skip the store-wide rank
            category_rank = f"#{rank} in {head[:45]}"
            break

    return {
        "asin": asin,
        "title": title,
        "subtitle": subtitle,
        "author": author,
        "rating": float(rating_text) if rating_text else None,
        "reviews": _int(_first(_REVIEWS, text)),
        "pages": pages,
        "year": year,
        "bsr": _int(_first(_BSR, text)),
        "category_rank": category_rank,
        "formats": [f for f in FORMATS if re.search(rf">\s*{re.escape(f)}\s*<", html or "")],
        "paperback_isbn": _first(_ISBN13, text) or _first(_ISBN10, text),
        "source_url": f"{AMAZON_HOST}/dp/{asin}",
    }


def bsr_status(bsr: Optional[int], observed_at: Optional[str] = None) -> str:
    """Whether the rank can be trusted, and when it was read.

    A blank cell cannot distinguish "this book has no rank" from "we could not
    read the page", so this never returns an empty string. Confidence mirrors
    the estimates engine: the public curve is best sampled in the mid-list.
    """
    when = observed_at or date.today().isoformat()
    if not bsr:
        return (f"unavailable — no Best Sellers Rank on the page as of {when} "
                f"(unranked, or the page did not render it)")
    if bsr > 100_000:
        confidence = "low confidence — deep in the long tail, where the curve is guesswork"
    elif bsr < 1_000:
        confidence = "low confidence — top ranks move hourly and the anchors are sparse"
    else:
        confidence = "medium confidence"
    return f"read {when} · {confidence}"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
try:  # pragma: no cover - the app runs flat (--app-dir server), tests as a package
    from .sources import registry
except ImportError:  # pragma: no cover
    from sources import registry

MAX_BOOKS = 20
_TAGS = re.compile(r"<(script|style).*?</\1>|<[^>]+>", re.S)

ENRICH_PROMPT = """You are describing competitor books for a KDP publisher.

Rules:
- Output ONLY a JSON array. No prose, no code fences.
- One element per book: {"asin": str, "credential": str, "sub_niche": str,
  "positioning": str}
- "credential" is the author's stated authority (e.g. "RD, 15 years clinical").
  Use "" if the page does not state one. Never infer or invent a credential.
- "sub_niche" is the specific shelf this book competes on, phrased as a buyer
  would search it — e.g. "air fryer cookbooks for beginners".
- "positioning" is one sentence on how the book differentiates: angle,
  promise, format or price play.
- Use only what the supplied page text states. Do not use outside knowledge.

Books:
"""


def page_text(html: str) -> str:
    """Tag-stripped text. The detail fields are only contiguous once markup is gone."""
    return re.sub(r"\s+", " ", _TAGS.sub(" ", html or "")).strip()


def _fetch_one(fetch: Callable, asin: str) -> dict[str, Any]:
    url = f"{AMAZON_HOST}/dp/{asin}"
    try:
        response = fetch(url)
    except Exception as exc:  # noqa: BLE001 - one bad book must not lose the batch
        return {**_blank(asin), "error": f"{type(exc).__name__}: {exc}"}
    status = getattr(response, "status", 0)
    body = getattr(response, "body", "") or ""
    if not isinstance(body, str):
        body = body.decode("utf-8", "ignore")
    if status != 200 or len(body) < 5_000 and "productTitle" not in body:
        return {**_blank(asin),
                "error": f"HTTP {status}, {len(body)} bytes — blocked or not a product page"}
    return {**parse_book(body, page_text(body), asin), "error": None}


def _blank(asin: str) -> dict[str, Any]:
    return {"asin": asin, "title": None, "subtitle": None, "author": None,
            "rating": None, "reviews": None, "pages": None, "year": None,
            "bsr": None, "category_rank": None, "formats": [],
            "paperback_isbn": None, "source_url": f"{AMAZON_HOST}/dp/{asin}"}


def _enrich(rows: list[dict[str, Any]], llm: Optional[Callable]) -> Optional[str]:
    """Fill credential / sub_niche / positioning. Returns a warning, or None."""
    for row in rows:
        row.setdefault("credential", None)
        row.setdefault("sub_niche", None)
        row.setdefault("positioning", None)

    usable = [r for r in rows if r.get("title")]
    if not usable:
        return None
    if llm is None:
        return ("No model configured, so Credential, Sub-niche and Positioning are "
                "blank. Set GEMINI_API_KEY to fill them.")

    listing = "\n".join(
        json.dumps({"asin": r["asin"], "title": r.get("title"),
                    "subtitle": r.get("subtitle"), "author": r.get("author"),
                    "pages": r.get("pages"), "year": r.get("year"),
                    "category_rank": r.get("category_rank")})
        for r in usable)
    try:
        reply = llm(ENRICH_PROMPT + listing)
    except Exception as exc:  # noqa: BLE001
        return f"Model call failed ({type(exc).__name__}: {exc}); soft fields left blank."

    try:
        text = reply.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fence:
            text = fence.group(1).strip()
        if not text.startswith("["):
            text = text[text.index("["):]
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        return "Model reply was not usable JSON; soft fields left blank."

    by_asin = {r["asin"]: r for r in rows}
    for entry in parsed if isinstance(parsed, list) else []:
        if not isinstance(entry, dict):
            continue
        # Never trust an ASIN we did not ask about.
        row = by_asin.get(str(entry.get("asin", "")).upper())
        if row is None:
            continue
        for field in ("credential", "sub_niche", "positioning"):
            value = str(entry.get(field) or "").strip()
            row[field] = value[:300] or None
    return None


def crowdedness_verdict(metrics: dict[str, Any]) -> dict[str, Any]:
    """How contested is this shelf? Not how much demand there is.

    Deliberately separate from Discovery's gap scoring, which mixes demand
    into its wording. A teardown measures no demand at all, so reusing that
    vocabulary produced "GOLDMINE — strong multi-source demand" for a single
    book whose demand was never looked at.
    """
    total = metrics.get("total_results")
    reviews = metrics.get("median_reviews")
    if total is None or reviews is None:
        return {"score": None, "verdict": "VERIFY — no clean Amazon read of this shelf",
                "basis": "Amazon returned no usable results page for the sub-niche"}

    competition = _clamp((math.log10(max(float(total), 1)) - 2.0) / 3.0)
    moat = _clamp((math.log10(max(float(reviews), 1)) - 0.7) / 2.5)
    pressure = 0.6 * competition + 0.4 * moat
    score = round(100.0 * (1.0 - pressure), 1)

    if score >= 70:
        verdict = "OPEN — few competitors and a shallow review moat"
    elif score >= 45:
        verdict = "CONTESTED — a real shelf; you need a clear angle"
    else:
        verdict = "CROWDED — deep shelf and an established review moat"

    return {"score": score, "verdict": verdict,
            "basis": (f"{int(float(total)):,} competing books, median review count "
                      f"{int(float(reviews)):,}")}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _crowdedness(rows: list[dict[str, Any]], validator: Callable) -> None:
    """Judge each book against a live search of the shelf it competes on."""
    phrases: dict[str, str] = {}
    for row in rows:
        phrase = (row.get("sub_niche") or row.get("title") or "").strip()
        if phrase:
            phrases[row["asin"]] = phrase

    supply: dict[str, Any] = {}
    if phrases:
        try:
            supply = validator(sorted(set(phrases.values()))) or {}
        except Exception:  # noqa: BLE001
            supply = {}

    for row in rows:
        metrics = supply.get(phrases.get(row["asin"], ""), {}) or {}
        verdict = crowdedness_verdict(metrics)
        row["crowdedness"] = {"phrase": phrases.get(row["asin"]),
                              "score": verdict["score"], "verdict": verdict["verdict"],
                              "basis": verdict["basis"],
                              "competing_books": metrics.get("total_results"),
                              "median_reviews": metrics.get("median_reviews")}


def run_teardown(params: dict[str, Any], progress, fetch: Optional[Callable] = None,
                 llm: Optional[Callable] = "auto",
                 validator: Optional[Callable] = None) -> dict[str, Any]:
    """Blocking; call from a worker thread."""
    asins = parse_identifiers(params.get("identifiers", ""))[:MAX_BOOKS]
    warnings: list[str] = []
    if not asins:
        progress("done", 100, "Nothing to tear down")
        return {"rows": [], "generated_at": date.today().isoformat(),
                "warnings": ["No ASIN or Amazon link found in what you pasted."]}

    fetch = fetch or registry.default_fetch("edge")
    if llm == "auto":
        llm = _default_llm()

    progress("fetch", 15, f"Reading {len(asins)} product page(s)")
    with ThreadPoolExecutor(max_workers=min(4, len(asins))) as pool:
        rows = list(pool.map(lambda a: _fetch_one(fetch, a), asins))

    today = date.today().isoformat()
    for row in rows:
        row["bsr_status"] = bsr_status(row.get("bsr"), observed_at=today)

    progress("enrich", 55, "Describing positioning")
    note = _enrich(rows, llm)
    if note:
        warnings.append(note)

    progress("crowdedness", 78, "Checking how contested each shelf is")
    _crowdedness(rows, validator or _default_validator)

    blocked = [r["asin"] for r in rows if r.get("error")]
    if blocked:
        warnings.append(f"{len(blocked)} page(s) could not be read: {', '.join(blocked)}")

    progress("done", 100, f"{len(rows)} book(s) torn down")
    return {"rows": rows, "generated_at": today, "warnings": warnings}


def _default_llm() -> Optional[Callable]:
    try:
        from . import brief as brief_mod
    except (ImportError, ValueError):  # pragma: no cover - flat import shape
        import brief as brief_mod  # type: ignore
    if brief_mod.active_provider() is None:
        return None
    return lambda prompt: brief_mod.call_llm(prompt, max_output_tokens=4096)


def _default_validator(phrases: list[str]) -> dict[str, Any]:
    from kdp_longtail_finder import STORES, LongTailSpider

    spider = LongTailSpider(
        candidates={p: i for i, p in enumerate(phrases)},
        store=STORES["kindle"], marketplace="us",
        impersonate="edge", stealthy_headers=True,
    )
    spider.start()
    return {m.keyword: m.model_dump() for m in spider.results}
