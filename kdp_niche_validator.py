"""KDP niche discovery & validation spider built on Scrapling.

Searches Amazon for a keyword, walks the paginated search results,
validates every book record it finds (ASIN format, title, price,
rating, review count) with Pydantic, and prints a niche verdict:

    Niche: "dog training for beginners"
    Amazon reports ~ 3,000 competing books
    Collected 500 records -> 487 VALID / 13 invalid (97.4% clean)
    Verdict: HIGH competition (more than 500 books in this niche)

Requirements:
    pip install "scrapling[fetchers]" pydantic
    scrapling install

Usage:
    python kdp_niche_validator.py "your keyword"
    python kdp_niche_validator.py "your keyword" --store books --max-books 300
    python kdp_niche_validator.py "your keyword" --threshold 500
    python kdp_niche_validator.py "your keyword" --reviews --max-reviews 50
    python kdp_niche_validator.py "air fryer" --marketplace uk
    python kdp_niche_validator.py "recetas freidora de aire" --marketplace es
    python kdp_niche_validator.py "air fryer" --proxies @proxies.txt
    python kdp_niche_validator.py "air fryer" --proxies "http://u:p@ip1:8080,http://u:p@ip2:8080"

Notes:
    * Scraping Amazon is against their ToS — keep concurrency and delays
      low, and consider the Product Advertising API / Keepa where they fit.
    * At any real volume you will need rotating residential proxies:
      pass ``proxy_rotator=ProxyRotator([...])`` to the FetcherSession in
      ``configure_sessions`` below.
    * REVIEWS: Amazon only shows the FIRST page of reviews (~10) to
      anonymous visitors — paginating further redirects to sign-in.
      With ``--reviews`` this spider fetches one page per star rating
      (5★..1★), which yields up to ~50 reviews per book without login.
      Getting truly *all* reviews requires a logged-in session (account
      ban risk) or a commercial reviews API.
"""

import argparse
import logging
import os
import re
import sys

import orjson  # already a Scrapling dependency
from typing import Optional
from urllib.parse import quote_plus

try:
    from pydantic import BaseModel, Field, ValidationError, field_validator
except ImportError:
    sys.exit("This script needs pydantic. Run: pip install pydantic")

import kdp_adaptive  # noqa: E402

from scrapling.fetchers import AsyncStealthySession, FetcherSession, ProxyRotator
from scrapling.spiders import Request, Response, Spider

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
STORES = {"kindle": "digital-text", "books": "stripbooks"}
STAR_FILTERS = ("five_star", "four_star", "three_star", "two_star", "one_star")

# marketplace code -> (domain, currency label)
MARKETPLACES = {
    "us": ("www.amazon.com", "$"),
    "uk": ("www.amazon.co.uk", "£"),
    "ca": ("www.amazon.ca", "CDN$"),
    "au": ("www.amazon.com.au", "AU$"),
    "in": ("www.amazon.in", "₹"),
    "jp": ("www.amazon.co.jp", "¥"),
    "de": ("www.amazon.de", "€"),
    "fr": ("www.amazon.fr", "€"),
    "it": ("www.amazon.it", "€"),
    "es": ("www.amazon.es", "€"),
    "nl": ("www.amazon.nl", "€"),
    "se": ("www.amazon.se", "kr"),
    "br": ("www.amazon.com.br", "R$"),
    "mx": ("www.amazon.com.mx", "MX$"),
}

# "1-16 of over 3,000 results" and its localized siblings
_RESULT_COUNT_PATTERNS = (
    re.compile(r"of\s+(?:over|more than)?\s*([\d.,\s ]+)\s+results", re.I),  # en (us/uk/ca/au/in)
    re.compile(r"von\s+(?:mehr als\s+)?([\d.,\s ]+)\s+Ergebnissen", re.I),  # de
    re.compile(r"sur\s+(?:plus de\s+)?([\d.,\s ]+)\s+résultats", re.I),  # fr
    re.compile(r"di\s+(?:oltre\s+)?([\d.,\s ]+)\s+risultati", re.I),  # it
    re.compile(r"de\s+(?:más de\s+|mais de\s+)?([\d.,\s ]+)\s+resultados", re.I),  # es/br/mx
    re.compile(r"van\s+(?:meer dan\s+)?([\d.,\s ]+)\s+resultaten", re.I),  # nl
    re.compile(r"av\s+(?:fler än\s+)?([\d.,\s ]+)\s+resultat", re.I),  # se
    re.compile(r"検索結果\s*([\d,，]+)"),  # jp: 検索結果 50,000 以上 のうち 1-16件
)


# --------------------------------------------------------------------------
# Validation layer — every scraped record must pass this model
# --------------------------------------------------------------------------
class Review(BaseModel):
    review_id: str = Field(min_length=5)
    rating: float = Field(ge=1, le=5)
    title: str = ""
    body: str = Field(min_length=1)
    date: str = ""
    verified_purchase: bool = False
    helpful_votes: int = Field(default=0, ge=0)


class Book(BaseModel):
    asin: str
    title: str = Field(min_length=2)
    url: str = Field(pattern=r"^https://")
    image_url: Optional[str] = Field(default=None, pattern=r"^https://")
    price: Optional[float] = Field(default=None, ge=0)  # buy price; None when the card only shows the KU $0.00
    currency: str = "$"
    marketplace: str = "us"
    kindle_unlimited: bool = False
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    reviews: int = Field(default=0, ge=0)
    page: int = Field(ge=1)
    review_list: list[Review] = Field(default_factory=list)

    @field_validator("asin")
    @classmethod
    def check_asin(cls, value: str) -> str:
        if not ASIN_RE.fullmatch(value):
            raise ValueError(f"malformed ASIN: {value!r}")
        return value


def _to_float(text: Optional[str]) -> Optional[float]:
    """Parse a number out of text in any Amazon locale: '3,000', '4.99',
    '4,99 €' (de/fr/es/it), '1.234,56' (eu), '￥1,200' (jp), '1 234' (fr/se)."""
    if not text:
        return None
    match = re.search(r"\d[\d.,\s ，]*", text)
    if not match:
        return None
    number = match.group().replace(" ", "").replace(" ", "").replace("，", ",").rstrip(".,")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):  # 1.234,56 -> eu style
            number = number.replace(".", "").replace(",", ".")
        else:  # 1,234.56 -> en style
            number = number.replace(",", "")
    elif "," in number:
        head, _, tail = number.rpartition(",")
        if len(tail) == 2:  # 4,99 -> decimal comma
            number = f"{head.replace(',', '')}.{tail}"
        else:  # 3,000 / 10,000 -> thousands separator
            number = number.replace(",", "")
    try:
        return float(number)
    except ValueError:
        return None


def _to_count(text: str) -> Optional[int]:
    """Result counts are whole numbers in every locale, so all separators
    ('3,000', '1.000', '4 000', '50,000') are thousands separators."""
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _parse_total_results(text: str) -> Optional[int]:
    """Read the results count from the (localized) results-info bar."""
    for pattern in _RESULT_COUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            value = _to_count(match.group(1))
            if value:
                return value
    # Fallback for an unrecognized locale: the count is the biggest number
    # in the bar ("1-16 of 342" -> 342)
    numbers = [n for n in (_to_count(t) for t in re.findall(r"\d[\d.,，]*", text)) if n]
    return max(numbers) if numbers else None


def _to_int(text: Optional[str]) -> int:
    value = _to_float(text)
    return int(value) if value is not None else 0


# --------------------------------------------------------------------------
# The spider
# --------------------------------------------------------------------------
CARD_SELECTOR = 'div[data-component-type="s-search-result"]'


class KDPNicheSpider(Spider):
    name = "kdp-niche"
    concurrent_requests = 2  # be gentle with Amazon
    download_delay = 2.0
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
        keyword: str,
        store: str = "digital-text",
        max_books: int = 500,
        fetch_reviews: bool = False,
        max_reviews_per_book: int = 50,
        impersonate: str = "chrome",
        stealthy_headers: bool = True,
        marketplace: str = "us",
        proxy: Optional[str] = None,
        proxies: Optional[list[str]] = None,
        **kwargs,
    ):
        if proxy and proxies:
            raise ValueError("Use either 'proxy' (one static proxy) or 'proxies' (rotating pool), not both")
        self.proxy = proxy
        # With a pool, every request pulls the next proxy (cyclic), so a
        # blocked request automatically lands on a different IP when the
        # spider re-schedules it; dead proxies are also retried on the next
        # proxy by the fetcher itself.
        self.proxy_rotator = ProxyRotator(proxies) if proxies else None
        self.keyword = keyword
        self.impersonate = impersonate
        self.stealthy_headers = stealthy_headers
        self.max_books = max_books
        self.fetch_reviews = fetch_reviews
        self.max_reviews_per_book = max_reviews_per_book
        if marketplace not in MARKETPLACES:
            raise ValueError(f"Unknown marketplace {marketplace!r}. Choose from: {', '.join(MARKETPLACES)}")
        self.marketplace = marketplace
        self.domain, self.currency = MARKETPLACES[marketplace]
        self.start_urls = [f"https://{self.domain}/s?k={quote_plus(keyword)}&i={store}"]

        self.total_results: Optional[int] = None  # what Amazon claims for this keyword
        self.valid_books: list[Book] = []
        # Set when a card had to be recovered by similarity — surfaced as a
        # warning so a redesign is never mistaken for a block.
        self.selector_drift: Optional[str] = None
        # Raw price strings, kept so the pipeline can verify Amazon served
        # the currency this marketplace actually uses. amazon.com returns
        # INR to an Indian IP, which used to be scored as dollars.
        self.price_texts: list[str] = []
        self.books_by_asin: dict[str, Book] = {}
        self.invalid_records: list[tuple[dict, str]] = []
        self.invalid_reviews = 0
        self.seen_asins: set[str] = set()
        self.seen_review_ids: set[str] = set()
        self.login_walled_asins: set[str] = set()
        super().__init__(**kwargs)

    def configure_sessions(self, manager):
        # Responses built by a Spider take their parsing arguments from
        # `selector_config`; without this, `auto_save` is silently inert and
        # no element fingerprints are ever written.
        selector_config = kdp_adaptive.selector_kwargs()

        # Cheap TLS-impersonated HTTP does the bulk of the work...
        manager.add(
            "http",
            FetcherSession(
                impersonate=self.impersonate,
                stealthy_headers=self.stealthy_headers,
                proxy=self.proxy,
                proxy_rotator=self.proxy_rotator,
                selector_config=selector_config,
            ),
            default=True,
        )
        # ...and the stealth browser only ever starts (lazy=True) if Amazon
        # begins serving captchas and requests get re-routed to it below.
        # It shares the same rotating pool, so escalated requests also come
        # from fresh IPs.
        manager.add(
            "stealth",
            AsyncStealthySession(headless=True, proxy=self.proxy,
                                 proxy_rotator=self.proxy_rotator,
                                 selector_config=selector_config),
            lazy=True,
        )

    async def is_blocked(self, response: Response) -> bool:
        if await super().is_blocked(response):  # 403/429/503/...
            return True
        # Amazon serves its robot check with HTTP 200, so sniff the page too
        if response.css('form[action*="validateCaptcha"]'):
            return True
        title = (response.css("title::text").get() or "").lower()
        return "robot check" in title or "sorry" in title

    async def retry_blocked_request(self, request: Request, response: Response) -> Request:
        if self.proxy_rotator:
            # The session pulls the next proxy per request, so the retry
            # automatically goes out through a different IP
            self.logger.info(f"Blocked ({response.status}); retry rotates to the next proxy: {request.url}")
        # First retry stays on HTTP; after that, escalate to the stealth browser
        if request._retry_count > 1:
            self.logger.warning(f"Escalating to stealth browser: {request.url}")
            request.sid = "stealth"
        return request

    async def parse(self, response: Response):
        page_no = response.request.meta.get("page", 1) if response.request else 1

        # 1) Niche size: "1-16 of over 3,000 results for ..."
        if self.total_results is None:
            info = " ".join(response.css('[data-component-type="s-result-info-bar"] ::text').getall())
            self.total_results = _parse_total_results(info) if info.strip() else None
            if self.total_results:
                self.logger.info(f'Amazon {self.marketplace.upper()} reports ~{self.total_results:,} results for "{self.keyword}"')

        # 2) Extract + validate every book card on this page.
        # Routed through kdp_adaptive so an Amazon redesign relocates the card
        # by similarity instead of looking identical to a soft block.
        cards, how = kdp_adaptive.select(
            response, CARD_SELECTOR, identifier="amazon_search_card")
        note = kdp_adaptive.explain(how, CARD_SELECTOR)
        if note and not self.selector_drift:
            self.selector_drift = note
            self.logger.warning(note)

        for card in cards:
            if len(self.valid_books) >= self.max_books:
                break

            asin = card.attrib.get("data-asin", "")
            if not asin or asin in self.seen_asins:
                continue
            self.seen_asins.add(asin)

            href = card.css("h2 a::attr(href)").get() or card.css("a.a-link-normal::attr(href)").get() or ""

            # A card can carry two prices: the Kindle Unlimited "$0.00" and the
            # real buy price ("or $4.99 to buy"). Take the highest so KU's zero
            # never masks the buy price; if KU is the only price shown, the buy
            # price simply isn't on the card - record None, not $0.00.
            price_section = " ".join(card.css('[data-cy="price-recipe"] ::text').getall())
            is_ku = "Kindle Unlimited" in price_section
            price_texts = card.css(".a-price .a-offscreen::text").getall()
            self.price_texts.extend(t for t in price_texts if t)
            prices = [p for p in (_to_float(t) for t in price_texts) if p is not None]
            buy_price = max(prices) if prices else None
            if is_ku and buy_price == 0:
                buy_price = None

            raw = {
                "asin": asin,
                "title": " ".join(card.css("h2 ::text").getall()).strip(),
                "url": response.urljoin(href) if href else f"https://{self.domain}/dp/{asin}",
                "image_url": card.css("img.s-image::attr(src)").get(),
                "price": buy_price,
                "currency": self.currency,
                "marketplace": self.marketplace,
                "kindle_unlimited": is_ku,
                "rating": _to_float(card.css(".a-icon-alt::text").get()),
                "reviews": _to_int(
                    card.css('a[aria-label*="ratings"] span::text').get()
                    or card.css("span.a-size-base.s-underline-text::text").get()
                ),
                "page": page_no,
            }

            try:
                book = Book(**raw)
            except ValidationError as error:
                self.invalid_records.append((raw, str(error)))
                self.logger.warning(f"Invalid record {asin} on page {page_no}: {error.error_count()} field error(s)")
                continue

            self.valid_books.append(book)
            self.books_by_asin[book.asin] = book
            yield book.model_dump(exclude={"review_list"})

            # 3) Optionally fan out to the book's reviews. Anonymous visitors
            # only get ONE page per listing, so we request one page per star
            # filter (5*..1*) => up to ~50 reviews per book without login.
            if self.fetch_reviews and book.reviews > 0:
                for star in STAR_FILTERS:
                    yield Request(
                        f"https://{self.domain}/product-reviews/{book.asin}/"
                        f"?filterByStar={star}&sortBy=helpful&pageNumber=1",
                        callback=self.parse_reviews,
                        meta={"asin": book.asin},
                        priority=-1,  # books first, reviews when search pages are done
                    )

        # 4) Keep paginating until we hit the target or run out of pages
        if len(self.valid_books) < self.max_books:
            next_href = response.css("a.s-pagination-next::attr(href)").get()
            if next_href:
                yield response.follow(next_href, meta={"page": page_no + 1})

    async def parse_reviews(self, response: Response):
        asin = response.request.meta["asin"] if response.request else ""
        book = self.books_by_asin.get(asin)
        if book is None:
            return

        # Amazon redirects review pages to sign-in once it wants a login
        if "/ap/signin" in response.url or response.css('form[name="signIn"]'):
            if asin not in self.login_walled_asins:
                self.login_walled_asins.add(asin)
                self.logger.warning(f"Login wall on reviews of {asin} — keeping the {len(book.review_list)} collected so far")
            return

        for card in response.css('div[data-hook="review"]'):
            if len(book.review_list) >= self.max_reviews_per_book:
                return

            review_id = card.attrib.get("id", "")
            if not review_id or review_id in self.seen_review_ids:
                continue
            self.seen_review_ids.add(review_id)

            title_parts = [
                text.strip()
                for text in card.css('[data-hook="review-title"] span::text').getall()
                if text.strip() and "out of 5 stars" not in text
            ]
            raw = {
                "review_id": review_id,
                "rating": _to_float(card.css('i[data-hook*="review-star-rating"] span::text').get()) or 0,
                "title": title_parts[-1] if title_parts else "",
                "body": " ".join(card.css('span[data-hook="review-body"] ::text').getall()).strip(),
                "date": (card.css('span[data-hook="review-date"]::text').get() or "").strip(),
                "verified_purchase": bool(card.css('span[data-hook="avp-badge"]')),
                "helpful_votes": _to_int(card.css('span[data-hook="helpful-vote-statement"]::text').get()),
            }

            try:
                book.review_list.append(Review(**raw))
            except ValidationError:
                self.invalid_reviews += 1
        # No pagination follow here on purpose: page 2+ is behind the login wall.
        yield None


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def print_report(spider: KDPNicheSpider, threshold: int) -> None:
    books, bad = spider.valid_books, spider.invalid_records
    scraped = len(books) + len(bad)
    clean_rate = (len(books) / scraped * 100) if scraped else 0.0

    print("\n" + "=" * 60)
    print(f'  Niche: "{spider.keyword}" (Amazon {spider.marketplace.upper()} — {spider.domain})')
    print("=" * 60)
    if spider.total_results is not None:
        print(f"  Amazon reports ~{spider.total_results:,} competing books")
    print(f"  Collected {scraped} records -> {len(books)} VALID / {len(bad)} invalid ({clean_rate:.1f}% clean)")

    if books:
        priced = [b.price for b in books if b.price is not None]
        rated = [b.rating for b in books if b.rating is not None]
        low_review = sum(1 for b in books if b.reviews < 100)
        ku_count = sum(1 for b in books if b.kindle_unlimited)
        print(f"  In Kindle Unlimited: {ku_count}/{len(books)} ({ku_count / len(books) * 100:.0f}%)")
        if priced:
            print(f"  Avg buy price: {spider.currency}{sum(priced) / len(priced):.2f} (over {len(priced)} books showing one)")
        if rated:
            print(f"  Avg rating:  {sum(rated) / len(rated):.2f} / 5")
        print(f"  Avg reviews: {sum(b.reviews for b in books) / len(books):,.0f}")
        print(f"  Books under 100 reviews: {low_review}/{len(books)} ({low_review / len(books) * 100:.0f}%)")

    if spider.fetch_reviews:
        scraped_reviews = sum(len(b.review_list) for b in books)
        covered = sum(1 for b in books if b.review_list)
        print(f"  Review texts scraped: {scraped_reviews:,} across {covered}/{len(books)} books"
              f" ({spider.invalid_reviews} failed validation)")
        if spider.login_walled_asins:
            print(f"  Login wall hit on {len(spider.login_walled_asins)} books (anonymous limit reached)")

    print("-" * 60)
    total = spider.total_results
    if total is None:
        print("  Verdict: UNKNOWN — could not read the results count")
    elif total <= threshold:
        print(f"  Verdict: LOW competition — niche has <= {threshold} books")
    elif total <= threshold * 4:
        print(f"  Verdict: MODERATE competition ({total:,} books)")
    else:
        print(f"  Verdict: HIGH competition (more than {threshold} books in this niche)")
    print("=" * 60)

    if bad:
        print("\n  Sample validation failure:")
        raw, error = bad[0]
        print(f"    record: {raw}")
        print(f"    errors: {error.splitlines()[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="KDP niche discovery + validation with Scrapling")
    parser.add_argument("keyword", help='Search keyword, e.g. "dog training for beginners"')
    parser.add_argument("--store", choices=list(STORES), default="kindle", help="Amazon store to search")
    parser.add_argument("--marketplace", choices=list(MARKETPLACES), default="us",
                        help="Amazon marketplace: " + ", ".join(MARKETPLACES))
    parser.add_argument("--max-books", type=int, default=500, help="Stop after this many validated books")
    parser.add_argument("--threshold", type=int, default=500, help="Books count below which a niche is 'low competition'")
    parser.add_argument("--reviews", action="store_true", help="Also scrape review texts (up to ~50/book without login)")
    parser.add_argument("--max-reviews", type=int, default=50, help="Max reviews to keep per book")
    parser.add_argument("--impersonate", default="chrome", help="Browser TLS fingerprint to impersonate (chrome/edge/safari/firefox)")
    parser.add_argument("--plain-headers", action="store_true", help="Disable generated stealth headers (some setups get 503 with them)")
    parser.add_argument("--proxy", default=None,
                        help="Proxy URL (http://user:pass@host:port). Use a region-local residential proxy "
                             "for marketplaces that block your IP (UK/JP are strict with datacenter IPs)")
    parser.add_argument("--proxies", default=None,
                        help="Rotating proxy pool: comma-separated URLs, or @proxies.txt (one URL per line). "
                             "Requests cycle through the pool, and blocked/dead proxies auto-rotate to the next")
    args = parser.parse_args()

    proxy_pool: Optional[list[str]] = None
    if args.proxies:
        if args.proxies.startswith("@"):
            with open(args.proxies[1:]) as f:
                proxy_pool = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        else:
            proxy_pool = [p.strip() for p in args.proxies.split(",") if p.strip()]
        if not proxy_pool:
            sys.exit(f"No proxies found in {args.proxies!r}")

    spider = KDPNicheSpider(
        keyword=args.keyword,
        store=STORES[args.store],
        max_books=args.max_books,
        fetch_reviews=args.reviews,
        max_reviews_per_book=args.max_reviews,
        impersonate=args.impersonate,
        stealthy_headers=not args.plain_headers,
        marketplace=args.marketplace,
        proxy=args.proxy,
        proxies=proxy_pool,
    )
    spider.start()

    print_report(spider, args.threshold)
    if spider.valid_books:
        slug = re.sub(r"[^a-z0-9]+", "_", args.keyword.lower()).strip("_") or "keyword"
        out_file = f"kdp_{args.marketplace}_{slug}.json"
        with open(out_file, "wb") as f:
            f.write(orjson.dumps([b.model_dump() for b in spider.valid_books], option=orjson.OPT_INDENT_2))
        print(f"\n  Saved {len(spider.valid_books)} validated books (with reviews) to {out_file}\n")


if __name__ == "__main__":
    main()
