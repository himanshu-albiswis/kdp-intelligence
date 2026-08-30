"""KDP long-tail keyword finder built on Scrapling.

Mines Amazon's own search-autocomplete API for long-tail variants of a
seed keyword (autocomplete = real buyer searches, i.e. proof of demand),
then validates each variant against its live search results and ranks
them by an opportunity score:

    #  KEYWORD                              RESULTS  MED.REV  <100REV  SCORE  VERDICT
    1  air fryer cookbook for diabetics       2,000       33      81%     64  STRONG
    2  air fryer recipes for two              1,000       55      75%     58  STRONG
    ...

Metrics per keyword (all shown, so every score is auditable):
    * suggest_rank     - position in Amazon autocomplete (lower = more searched)
    * total_results    - competing books for the exact query
    * median_reviews   - review moat of page-1 books (lower = beatable)
    * pct_under_100    - share of page-1 books under 100 reviews
    * ku_share         - how KU-dominated the niche is
    * avg_price        - page-1 average buy price
    * phrase_in_titles - page-1 titles containing the exact phrase (relevance proof)

Requirements:
    pip install "scrapling[fetchers]" pydantic
Usage:
    python kdp_longtail_finder.py "air fryer"
    python kdp_longtail_finder.py "air fryer" --marketplace uk --max-keywords 20
    python kdp_longtail_finder.py "keto" --min-results 20 --impersonate edge --plain-headers

Same ToS / proxy caveats as kdp_niche_validator.py.
"""

import argparse
import logging
import os
import math
import re
import statistics
import sys
from typing import Callable, Optional
from urllib.parse import quote_plus

import orjson

try:
    from pydantic import BaseModel, Field
except ImportError:
    sys.exit("This script needs pydantic. Run: pip install pydantic")

from scrapling.fetchers import Fetcher, FetcherSession, ProxyRotator
from scrapling.spiders import Request, Response, Spider

STORES = {"kindle": "digital-text", "books": "stripbooks"}

# marketplace -> (domain, currency, autocomplete marketplace-id)
MARKETPLACES = {
    "us": ("www.amazon.com", "$", "ATVPDKIKX0DER"),
    "uk": ("www.amazon.co.uk", "£", "A1F83G8C2ARO7P"),
    "ca": ("www.amazon.ca", "CDN$", "A2EUQ1WTGCTBG2"),
    "au": ("www.amazon.com.au", "AU$", "A39IBJ37TRP1C6"),
    "in": ("www.amazon.in", "₹", "A21TJRUUN4KGV"),
    "jp": ("www.amazon.co.jp", "¥", "A1VC38T7YXB528"),
    "de": ("www.amazon.de", "€", "A1PA6795UKMFR9"),
    "fr": ("www.amazon.fr", "€", "A13V1IB3VIYZZH"),
    "it": ("www.amazon.it", "€", "APJ6JRA9NG5V4"),
    "es": ("www.amazon.es", "€", "A1RKKUPIHCS9HS"),
    "nl": ("www.amazon.nl", "€", "A1805IZSGTT6HS"),
    "se": ("www.amazon.se", "kr", "A2NODRKZP88ZB9"),
    "br": ("www.amazon.com.br", "R$", "A2Q3Y263D00KWC"),
    "mx": ("www.amazon.com.mx", "MX$", "A1AM78C64UM0Y8"),
}

# Prefixes appended to the seed to make autocomplete reveal more of the tail
EXPANSIONS = ["", "for ", "with ", "without ", *[c for c in "abcdefghijklmnopqrstuvwxyz"], "2"]


def _to_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"\d[\d.,\s ，]*", text)
    if not match:
        return None
    number = match.group().replace(" ", "").replace(" ", "").replace("，", ",").rstrip(".,")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        head, _, tail = number.rpartition(",")
        number = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else number.replace(",", "")
    try:
        return float(number)
    except ValueError:
        return None


def _to_count(text: str) -> Optional[int]:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _parse_total_results(text: str) -> Optional[int]:
    match = re.search(r"of\s+(?:over|more than)?\s*([\d.,\s ]+)\s+results", text, re.I)
    if match:
        return _to_count(match.group(1))
    numbers = [n for n in (_to_count(t) for t in re.findall(r"\d[\d.,，]*", text)) if n]
    return max(numbers) if numbers else None


class KeywordMetrics(BaseModel):
    """Validated metrics for one long-tail candidate."""

    keyword: str = Field(min_length=2)
    suggest_rank: int = Field(ge=0)  # best position seen in autocomplete
    total_results: Optional[int] = Field(default=None, ge=0)
    page1_books: int = Field(ge=0)
    median_reviews: float = Field(default=0, ge=0)
    pct_under_100: float = Field(default=0, ge=0, le=100)
    ku_share: float = Field(default=0, ge=0, le=100)
    avg_price: Optional[float] = Field(default=None, ge=0)
    phrase_in_titles: int = Field(ge=0)
    opportunity: int = Field(default=0, ge=0, le=100)
    verdict: str = ""
    url: str = ""


def opportunity_score(m: KeywordMetrics) -> int:
    """0-100. Competition 30%, review moat 20%, entry ease 15%, demand 15%, book-relevance 20%.

    Relevance (page-1 titles containing the exact phrase) is what separates a
    real book niche ("air fryer cookbook for beginners" -> 15 title matches)
    from a product search that happens to return books ("air fryer black
    friday" -> 0 matches)."""
    results = m.total_results if m.total_results is not None else 100_000
    res_score = max(0.0, 1 - math.log10(results + 1) / 5)  # 0 -> 1.0, 100k+ -> 0.0
    moat_score = max(0.0, 1 - m.median_reviews / 500)
    entry_score = m.pct_under_100 / 100
    demand_score = max(0.0, 1 - m.suggest_rank / (len(EXPANSIONS) * 10))
    relevance_score = min(m.phrase_in_titles, 5) / 5
    return round(
        100
        * (0.30 * res_score + 0.20 * moat_score + 0.15 * entry_score + 0.15 * demand_score + 0.20 * relevance_score)
    )


def verdict_for(m: KeywordMetrics) -> str:
    if m.phrase_in_titles == 0:
        return "⚠️ PRODUCT-INTENT (no page-1 title uses this phrase)"
    if m.total_results is not None and m.total_results < 10 and m.phrase_in_titles <= 1:
        return "❔ THIN (little demand evidence)"
    if m.opportunity >= 70:
        return "🥇 GOLDEN"
    if m.opportunity >= 55:
        return "🟢 STRONG"
    if m.opportunity >= 40:
        return "🟡 POSSIBLE"
    return "🔴 CROWDED"


# Fragments ending in one of these are not phrases anyone types, so they are
# skipped when broadening a seed toward its head term.
# Empty expansions to tolerate before declaring a term exhausted.
DEAD_TERM_PROBES = 4

_TRAILING_STOPWORDS = {"for", "with", "without", "and", "or", "the", "a", "an",
                       "of", "to", "in", "on", "your", "my", "at", "by"}


def head_terms(seed: str) -> list[str]:
    """The seed, then progressively broader heads of it.

    "adhd for beginners" -> ["adhd for beginners", "adhd"]

    Amazon's autocomplete has nothing below an already-specific phrase, so a
    seed like "adhd for beginners" mines zero candidates and the report ends
    up with a single row. Broadening to the head term finds the long tail the
    user was actually asking for.
    """
    words = seed.split()
    terms: list[str] = []
    for end in range(len(words), 0, -1):
        candidate = " ".join(words[:end])
        if end < len(words) and words[end - 1].lower() in _TRAILING_STOPWORDS:
            continue
        terms.append(candidate)
    return terms


def _suggest_for(term: str, mid: str, impersonate: str, limit: int,
                 fetch: Callable) -> dict[str, int]:
    """Autocomplete candidates for one term, excluding the term itself."""
    found: dict[str, int] = {}
    for exp_index, expansion in enumerate(EXPANSIONS):
        prefix = f"{term} {expansion}".rstrip() + (" " if not expansion else "")
        try:
            page = fetch(
                "https://completion.amazon.com/api/2017/suggestions"
                f"?mid={mid}&alias=digital-text&limit=11&prefix={quote_plus(prefix)}",
                impersonate=impersonate,
                stealthy_headers=False,
            )
            suggestions = page.json().get("suggestions", [])
        except Exception:
            continue
        for pos, s in enumerate(suggestions):
            value = (s.get("value") or "").strip().lower()
            if not value or value == term.lower() or term.lower() not in value:
                continue
            rank = exp_index * 10 + pos
            if value not in found or rank < found[value]:
                found[value] = rank
        if len(found) >= limit * 3:  # plenty to choose from
            break
        # Amazon has nothing below an already-specific phrase. Proving that
        # across all 30 expansions costs 30 delayed requests, which is what
        # left jobs sitting at 5%. A few empty probes is enough evidence.
        if exp_index + 1 >= DEAD_TERM_PROBES and not found:
            break
    return found


def mine_suggestions(seed: str, marketplace: str, impersonate: str, limit: int,
                     fetch: Optional[Callable] = None) -> tuple[dict[str, int], Optional[str]]:
    """Ask Amazon autocomplete for long-tail variants of the seed.

    Returns ({keyword: best_rank}, note) where rank encodes expansion-order +
    position (lower = surfaced earlier = stronger demand signal), and note is
    set when the seed was exhausted and a broader head term was used instead.
    """
    fetch = fetch or Fetcher.get
    mid = MARKETPLACES[marketplace][2]
    seed = " ".join(seed.split())

    for index, term in enumerate(head_terms(seed)):
        found = _suggest_for(term, mid, impersonate, limit, fetch)
        if not found:
            continue
        best = dict(sorted(found.items(), key=lambda kv: kv[1])[:limit])
        if index == 0:
            return best, None
        return best, (
            f'"{seed}" is already specific — Amazon autocomplete offers nothing '
            f'below it. Broadened to "{term}" to find the long tail; results are '
            f'siblings of your phrase, not children of it.'
        )
    return {}, None


class LongTailSpider(Spider):
    name = "kdp-longtail"
    concurrent_requests = 2
    download_delay = 1.5
    max_blocked_retries = 3
    logging_level = logging.INFO
    # Let Scrapling pick the delay per domain and back off when Amazon starts
    # blocking, instead of trusting one hand-tuned constant everywhere.
    autothrottle_enabled = True
    # Replay cached responses while iterating on parse logic. Off unless asked:
    # a stale cache silently scoring old data would be worse than a slow run.
    development_mode = bool(os.environ.get("KDP_DEV_CACHE"))

    def __init__(
        self,
        candidates: dict[str, int],
        store: str = "digital-text",
        marketplace: str = "us",
        impersonate: str = "chrome",
        stealthy_headers: bool = True,
        proxy: Optional[str] = None,
        proxies: Optional[list[str]] = None,
        **kwargs,
    ):
        if proxy and proxies:
            raise ValueError("Use either 'proxy' or 'proxies', not both")
        self.candidates = candidates
        self.store = store
        self.marketplace = marketplace
        self.domain, self.currency, _ = MARKETPLACES[marketplace]
        self.impersonate = impersonate
        self.stealthy_headers = stealthy_headers
        self.proxy = proxy
        self.proxy_rotator = ProxyRotator(proxies) if proxies else None
        self.results: list[KeywordMetrics] = []
        self.failed_keywords: list[str] = []
        super().__init__(**kwargs)  # start_requests() is overridden, no start_urls needed

    def configure_sessions(self, manager):
        manager.add(
            "http",
            FetcherSession(
                impersonate=self.impersonate,
                stealthy_headers=self.stealthy_headers,
                proxy=self.proxy,
                proxy_rotator=self.proxy_rotator,
            ),
            default=True,
        )

    async def start_requests(self):
        for keyword, rank in self.candidates.items():
            url = f"https://{self.domain}/s?k={quote_plus(keyword)}&i={self.store}"
            yield Request(url, sid="http", meta={"keyword": keyword, "rank": rank})

    async def is_blocked(self, response: Response) -> bool:
        if await super().is_blocked(response):
            return True
        if response.status == 202:  # Amazon's JavaScript bot challenge
            return True
        return bool(response.css('form[action*="validateCaptcha"]'))

    async def parse(self, response: Response):
        meta = response.request.meta if response.request else {}
        keyword: str = meta.get("keyword", "")

        info = " ".join(response.css('[data-component-type="s-result-info-bar"] ::text').getall())
        total = _parse_total_results(info) if info.strip() else None

        cards = response.css('div[data-component-type="s-search-result"]')
        if not cards and total is None:
            # A soft block / degraded page, not a real "0 results" answer -
            # recording it would poison the ranking with fake zeros
            self.failed_keywords.append(keyword)
            self.logger.warning(f"No usable results page for {keyword!r} (soft block?) - keyword skipped")
            return

        reviews, prices, ku_flags, phrase_hits = [], [], [], 0
        for card in cards:
            title = " ".join(card.css("h2 ::text").getall()).strip()
            review_count = _to_count(
                card.css('a[aria-label*="ratings"] span::text').get()
                or card.css("span.a-size-base.s-underline-text::text").get()
                or ""
            )
            reviews.append(review_count or 0)
            price_section = " ".join(card.css('[data-cy="price-recipe"] ::text').getall())
            ku_flags.append("Kindle Unlimited" in price_section)
            card_prices = [
                p for p in (_to_float(t) for t in card.css(".a-price .a-offscreen::text").getall()) if p
            ]
            if card_prices:
                prices.append(max(card_prices))
            if keyword.lower() in title.lower():
                phrase_hits += 1

        metrics = KeywordMetrics(
            keyword=keyword,
            suggest_rank=meta.get("rank", 0),
            total_results=total,
            page1_books=len(reviews),
            median_reviews=float(statistics.median(reviews)) if reviews else 0,
            pct_under_100=(sum(1 for r in reviews if r < 100) / len(reviews) * 100) if reviews else 0,
            ku_share=(sum(ku_flags) / len(ku_flags) * 100) if ku_flags else 0,
            avg_price=round(sum(prices) / len(prices), 2) if prices else None,
            phrase_in_titles=phrase_hits,
            url=response.url,
        )
        metrics.opportunity = opportunity_score(metrics)
        metrics.verdict = verdict_for(metrics)
        self.results.append(metrics)
        yield metrics.model_dump()


def print_ranking(seed: str, spider: LongTailSpider) -> None:
    ranked = sorted(spider.results, key=lambda m: m.opportunity, reverse=True)
    print("\n" + "=" * 100)
    print(f'  Long-tail opportunities for "{seed}" (Amazon {spider.marketplace.upper()})')
    print("=" * 100)
    print(f"  {'#':>2}  {'KEYWORD':<44} {'RESULTS':>8} {'MED.REV':>8} {'<100REV':>8} {'KU%':>5} {'SCORE':>6}  VERDICT")
    print("-" * 100)
    for i, m in enumerate(ranked, 1):
        total = f"{m.total_results:,}" if m.total_results is not None else "?"
        print(
            f"  {i:>2}  {m.keyword:<44.44} {total:>8} {m.median_reviews:>8.0f} "
            f"{m.pct_under_100:>7.0f}% {m.ku_share:>4.0f}% {m.opportunity:>6}  {m.verdict}"
        )
    print("-" * 100)
    print("  SCORE = 30% low competition + 20% weak review moat + 15% easy entry + 15% autocomplete demand")
    print("          + 20% book-relevance (page-1 titles actually using the phrase)")
    print("  Autocomplete presence itself is the demand proof: Amazon only suggests what buyers actually type.")
    if spider.failed_keywords:
        print(f"\n  ⚠️ {len(spider.failed_keywords)} keyword(s) could not be validated (blocked pages), NOT ranked:")
        print(f"     {', '.join(spider.failed_keywords)}")
        print("     Re-run later or use --proxies; unvalidated keywords are never scored.")


def main() -> None:
    parser = argparse.ArgumentParser(description="KDP long-tail keyword finder + validator")
    parser.add_argument("seed", help='Seed keyword, e.g. "air fryer"')
    parser.add_argument("--marketplace", choices=list(MARKETPLACES), default="us")
    parser.add_argument("--store", choices=list(STORES), default="kindle")
    parser.add_argument("--max-keywords", type=int, default=25, help="How many variants to validate")
    parser.add_argument("--impersonate", default="chrome")
    parser.add_argument("--plain-headers", action="store_true")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--proxies", default=None, help="Comma-separated proxy URLs or @file")
    args = parser.parse_args()

    proxy_pool: Optional[list[str]] = None
    if args.proxies:
        if args.proxies.startswith("@"):
            with open(args.proxies[1:]) as f:
                proxy_pool = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        else:
            proxy_pool = [p.strip() for p in args.proxies.split(",") if p.strip()]

    print(f'Mining Amazon autocomplete for "{args.seed}" long-tails...')
    candidates, broaden_note = mine_suggestions(args.seed, args.marketplace, args.impersonate, args.max_keywords)
    if broaden_note:
        print(f"note: {broaden_note}")
    if not candidates:
        sys.exit("No autocomplete suggestions found - try a broader seed keyword.")
    print(f"Found {len(candidates)} candidates; validating each against live search results...\n")

    spider = LongTailSpider(
        candidates=candidates,
        store=STORES[args.store],
        marketplace=args.marketplace,
        impersonate=args.impersonate,
        stealthy_headers=not args.plain_headers,
        proxy=args.proxy,
        proxies=proxy_pool,
    )
    spider.start()
    print_ranking(args.seed, spider)

    slug = re.sub(r"[^a-z0-9]+", "_", args.seed.lower()).strip("_") or "seed"
    out_file = f"kdp_longtail_{args.marketplace}_{slug}.json"
    ranked = sorted(spider.results, key=lambda m: m.opportunity, reverse=True)
    with open(out_file, "wb") as f:
        f.write(orjson.dumps([m.model_dump() for m in ranked], option=orjson.OPT_INDENT_2))
    print(f"\n  Saved {len(ranked)} validated keywords to {out_file}\n")


if __name__ == "__main__":
    main()
