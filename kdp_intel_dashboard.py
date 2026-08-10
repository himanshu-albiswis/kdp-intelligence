"""KDP Intelligence Dashboard - the full pipeline in one command.

Runs every stage of KDP niche research for a seed keyword and renders a
single self-contained HTML dashboard:

    1. LONG-TAIL MINING     Amazon autocomplete -> real buyer searches
    2. KEYWORD VALIDATION   competition / moat / entry / relevance scores
    3. NICHE SCAN           books of the best keyword (covers, prices, KU)
    4. MONEY PROOF          product pages -> BSR -> est. sales/day and
                            est. monthly royalty per book (auditable curve)
    5. RELEASE VELOCITY     publication dates -> is the niche alive?
    6. TITLE-GAP FINDER     demanded phrases page-1 titles don't use yet
    7. COMPLAINT MINER      1-2 star reviews of top books -> what buyers hate

Requirements:
    pip install "scrapling[fetchers]" pydantic
Usage:
    python kdp_intel_dashboard.py "air fryer"
    python kdp_intel_dashboard.py "air fryer" --max-keywords 12 --books 30 --deep-dive 10
    python kdp_intel_dashboard.py "keto" --marketplace uk --proxies @proxies.txt

Outputs: kdp_dashboard_<mkt>_<slug>.html + kdp_intel_<mkt>_<slug>.json

Estimates disclaimer: BSR->sales uses a public log-interpolated curve
(anchors in _BSR_ANCHORS); treat outputs as order-of-magnitude evidence,
not accounting. Same ToS / proxy caveats as the sibling scripts.
"""

import argparse
import base64
import bisect
import html as html_mod
import logging
import math
import os
import re
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote_plus
from urllib.request import urlopen

import orjson

try:
    from pydantic import BaseModel, Field
except ImportError:
    sys.exit("This script needs pydantic. Run: pip install pydantic")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kdp_longtail_finder import (  # noqa: E402
    MARKETPLACES,
    STORES,
    KeywordMetrics,
    LongTailSpider,
    _parse_total_results,
    _to_count,
    _to_float,
    mine_suggestions,
)
from kdp_niche_validator import Book, KDPNicheSpider  # noqa: E402

from scrapling.fetchers import FetcherSession, ProxyRotator  # noqa: E402
from scrapling.spiders import Request, Response, Spider  # noqa: E402

# Public BSR -> sales/day anchors (Kindle Store), log-log interpolated.
# Order-of-magnitude estimates in the spirit of the widely used calculators.
_BSR_ANCHORS = [
    (1, 5000.0), (10, 1500.0), (100, 350.0), (1_000, 100.0), (5_000, 30.0),
    (10_000, 15.0), (50_000, 3.0), (100_000, 1.0), (500_000, 0.15), (1_000_000, 0.03),
]

_TITLE_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "with", "your",
    "on", "that", "this", "you", "how", "is", "are", "&", "-", "|", "by",
}


def est_sales_per_day(bsr: Optional[int]) -> Optional[float]:
    if not bsr or bsr <= 0:
        return None
    xs = [a[0] for a in _BSR_ANCHORS]
    if bsr <= xs[0]:
        return _BSR_ANCHORS[0][1]
    if bsr >= xs[-1]:
        return _BSR_ANCHORS[-1][1]
    i = bisect.bisect_left(xs, bsr)
    x0, y0 = _BSR_ANCHORS[i - 1]
    x1, y1 = _BSR_ANCHORS[i]
    t = (math.log10(bsr) - math.log10(x0)) / (math.log10(x1) - math.log10(x0))
    return 10 ** (math.log10(y0) + t * (math.log10(y1) - math.log10(y0)))


def kdp_royalty_per_sale(price: Optional[float]) -> Optional[float]:
    """KDP ebook royalty: 70% in the $2.99-$9.99 band (minus ~$0.06 typical
    delivery fee), 35% outside it."""
    if price is None or price <= 0:
        return None
    if 2.99 <= price <= 9.99:
        return round(max(0.0, price * 0.70 - 0.06), 2)
    return round(price * 0.35, 2)


class BookIntel(BaseModel):
    """A niche book enriched with product-page money metrics."""

    asin: str
    title: str
    url: str
    image_url: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    kindle_unlimited: bool = False
    rating: Optional[float] = None
    reviews: int = 0
    bsr: Optional[int] = Field(default=None, ge=1)
    category_rank: str = ""
    publication_date: Optional[str] = None  # ISO date
    pages: Optional[int] = None
    est_sales_per_day: Optional[float] = None
    est_monthly_royalty: Optional[float] = None


class ReviewSnippet(BaseModel):
    asin: str
    book_title: str
    rating: float = Field(ge=1, le=5)
    title: str = ""
    body: str = Field(min_length=1)


class DeepDiveSpider(Spider):
    """Visits product pages of already-scraped books and extracts BSR,
    publication date, and page count, then computes revenue estimates."""

    name = "kdp-deepdive"
    concurrent_requests = 2
    download_delay = 1.5
    max_blocked_retries = 3
    logging_level = logging.INFO

    def __init__(self, books: list[Book], session_kwargs: dict[str, Any], **kwargs):
        self.books = books
        self.session_kwargs = session_kwargs
        self.intel: list[BookIntel] = []
        # Reviews shown on the product page itself - reachable without the
        # login wall that guards the dedicated review listing
        self.review_snippets: list[ReviewSnippet] = []
        super().__init__(**kwargs)

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(**self.session_kwargs), default=True)

    async def start_requests(self):
        for book in self.books:
            yield Request(book.url, sid="http", meta={"book": book})

    async def is_blocked(self, response: Response) -> bool:
        if await super().is_blocked(response) or response.status == 202:
            return True
        return bool(response.css('form[action*="validateCaptcha"]'))

    async def parse(self, response: Response):
        book: Book = response.request.meta["book"] if response.request else None
        if book is None:
            return

        detail = " ".join(
            response.css(
                "#detailBullets_feature_div ::text, #detailBulletsWrapper_feature_div ::text, "
                "#productDetails_detailBullets_sections1 ::text, #prodDetails ::text"
            ).getall()
        )
        detail = re.sub(r"\s+", " ", detail)

        bsr_match = re.search(r"Best Sellers Rank[:\s#]*([\d,]+)", detail)
        bsr = _to_count(bsr_match.group(1)) if bsr_match else None

        category_rank = ""
        for rank, cat in re.findall(r"#([\d,]+) in ([A-Za-z'&, -]+)", detail):
            cat = cat.strip()
            if cat.split()[0] not in ("Kindle", "Books"):  # skip the store-wide ranks
                category_rank = f"#{rank} in {cat[:40]}"
                break

        pub_date_iso = None
        date_match = re.search(r"Publication date[^A-Za-z0-9]*([A-Za-z]+ \d{1,2}, \d{4})", detail)
        if date_match:
            try:
                pub_date_iso = datetime.strptime(date_match.group(1), "%B %d, %Y").date().isoformat()
            except ValueError:
                pass

        for card in response.css('div[data-hook="review"]'):
            rating = _to_float(card.css('i[data-hook*="review-star-rating"] span::text').get()) or 0
            # dedicated review pages use review-body; product pages use
            # camelCase hooks (reviewRichContentContainer / reviewTitle)
            body = " ".join(
                card.css(
                    'span[data-hook="review-body"] ::text, [data-hook="reviewRichContentContainer"] ::text'
                ).getall()
            ).strip()
            titles = [
                t.strip()
                for t in card.css('[data-hook="review-title"] span::text, [data-hook="reviewTitle"] ::text').getall()
                if t.strip() and "out of 5 stars" not in t
            ]
            if body and 1 <= rating <= 3:
                self.review_snippets.append(
                    ReviewSnippet(
                        asin=book.asin,
                        book_title=book.title[:60],
                        rating=rating,
                        title=titles[-1] if titles else "",
                        body=body[:400],
                    )
                )

        pages_match = re.search(r"Print length[^0-9]*(\d+)\s*pages", detail)
        sales = est_sales_per_day(bsr)
        royalty = kdp_royalty_per_sale(book.price)

        self.intel.append(
            BookIntel(
                asin=book.asin,
                title=book.title,
                url=book.url,
                image_url=book.image_url,
                price=book.price,
                kindle_unlimited=book.kindle_unlimited,
                rating=book.rating,
                reviews=book.reviews,
                bsr=bsr,
                category_rank=category_rank,
                publication_date=pub_date_iso,
                pages=int(pages_match.group(1)) if pages_match else None,
                est_sales_per_day=round(sales, 2) if sales is not None else None,
                est_monthly_royalty=(
                    round(sales * 30 * royalty, 2) if sales is not None and royalty is not None else None
                ),
            )
        )
        yield None


class ComplaintSpider(Spider):
    """Fetches the 1-2 star review pages of the given books (first page per
    star - the anonymous limit) to surface what buyers complain about."""

    name = "kdp-complaints"
    concurrent_requests = 2
    download_delay = 1.5
    max_blocked_retries = 2
    logging_level = logging.INFO

    def __init__(self, books: list[Book], domain: str, session_kwargs: dict[str, Any], **kwargs):
        self.books = books
        self.domain = domain
        self.session_kwargs = session_kwargs
        self.snippets: list[ReviewSnippet] = []
        super().__init__(**kwargs)

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(**self.session_kwargs), default=True)

    async def start_requests(self):
        for book in self.books:
            for star in ("one_star", "two_star"):
                yield Request(
                    f"https://{self.domain}/product-reviews/{book.asin}/?filterByStar={star}&sortBy=helpful",
                    sid="http",
                    meta={"book": book},
                )

    async def is_blocked(self, response: Response) -> bool:
        if await super().is_blocked(response) or response.status == 202:
            return True
        return bool(response.css('form[action*="validateCaptcha"]'))

    async def parse(self, response: Response):
        book: Book = response.request.meta["book"] if response.request else None
        if book is None or "/ap/signin" in response.url:
            return
        for card in response.css('div[data-hook="review"]'):
            rating = _to_float(card.css('i[data-hook*="review-star-rating"] span::text').get()) or 0
            body = " ".join(card.css('span[data-hook="review-body"] ::text').getall()).strip()
            titles = [
                t.strip()
                for t in card.css('[data-hook="review-title"] span::text').getall()
                if t.strip() and "out of 5 stars" not in t
            ]
            if body and 1 <= rating <= 2:
                self.snippets.append(
                    ReviewSnippet(
                        asin=book.asin,
                        book_title=book.title[:60],
                        rating=rating,
                        title=titles[-1] if titles else "",
                        body=body[:400],
                    )
                )
        yield None


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def release_velocity(intel: list[BookIntel]) -> dict[str, Any]:
    dated = [b for b in intel if b.publication_date]
    if not dated:
        return {"dated": 0, "last_90d": 0, "last_365d": 0, "pct_90d": 0.0, "pct_365d": 0.0}
    today = datetime.now().date()
    d90 = sum(1 for b in dated if (today - datetime.fromisoformat(b.publication_date).date()).days <= 90)
    d365 = sum(1 for b in dated if (today - datetime.fromisoformat(b.publication_date).date()).days <= 365)
    return {
        "dated": len(dated),
        "last_90d": d90,
        "last_365d": d365,
        "pct_90d": round(d90 / len(dated) * 100, 1),
        "pct_365d": round(d365 / len(dated) * 100, 1),
    }


def title_ngrams(titles: list[str], top: int = 12) -> list[tuple[str, int]]:
    counts: Counter = Counter()
    for title in titles:
        words = [w for w in re.findall(r"[a-z][a-z']+", title.lower()) if w not in _TITLE_STOPWORDS]
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                counts[" ".join(words[i : i + n])] += 1
    return [(g, c) for g, c in counts.most_common(top * 3) if c >= 2][:top]


def find_title_gaps(keywords: list[KeywordMetrics]) -> list[KeywordMetrics]:
    """Demanded phrases (real autocomplete searches) that few page-1 titles
    use and that face modest competition = closest thing to free shelf space."""
    gaps = [
        m
        for m in keywords
        if "PRODUCT-INTENT" not in m.verdict
        and m.page1_books > 0
        and m.phrase_in_titles <= 2
        and (m.total_results or 0) <= 3000
    ]
    return sorted(gaps, key=lambda m: (m.phrase_in_titles, -(m.opportunity)))


# ---------------------------------------------------------------------------
# Dashboard rendering (single self-contained HTML file)
# ---------------------------------------------------------------------------
def _fetch_cover_b64(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        return "data:image/jpeg;base64," + base64.b64encode(urlopen(url, timeout=20).read()).decode()
    except Exception:
        return None


def _esc(text: Any) -> str:
    return html_mod.escape(str(text))


def _bar(value: float, max_value: float, color: str) -> str:
    width = 0 if max_value <= 0 else min(100, value / max_value * 100)
    return (
        f'<div class="bar"><div class="fill" style="width:{width:.0f}%;background:{color}"></div>'
        f"</div>"
    )


def build_dashboard(
    seed: str,
    marketplace: str,
    currency: str,
    keywords: list[KeywordMetrics],
    best_keyword: str,
    books: list[Book],
    intel: list[BookIntel],
    velocity: dict[str, Any],
    gaps: list[KeywordMetrics],
    ngrams: list[tuple[str, int]],
    complaints: list[ReviewSnippet],
    timings: dict[str, float],
    total_results: Optional[int],
) -> str:
    ranked = sorted(keywords, key=lambda m: m.opportunity, reverse=True)
    money = sorted((b for b in intel if b.est_monthly_royalty), key=lambda b: -(b.est_monthly_royalty or 0))
    royalty_pool = sum(b.est_monthly_royalty or 0 for b in intel)
    ku_share = round(sum(1 for b in books if b.kindle_unlimited) / len(books) * 100) if books else 0
    med_reviews = statistics.median([b.reviews for b in books]) if books else 0
    priced = [b.price for b in books if b.price is not None]

    with ThreadPoolExecutor(max_workers=12) as pool:
        covers = dict(zip((b.asin for b in books[:24]), pool.map(_fetch_cover_b64, (b.image_url for b in books[:24]))))

    kw_rows = ""
    for i, m in enumerate(ranked, 1):
        total = f"{m.total_results:,}" if m.total_results is not None else "?"
        kw_rows += (
            f"<tr><td>{i}</td><td class='kw'><a href='{_esc(m.url)}' target='_blank'>{_esc(m.keyword)}</a></td>"
            f"<td class='num'>{total}</td><td class='num'>{m.median_reviews:.0f}</td>"
            f"<td class='num'>{m.pct_under_100:.0f}%</td><td class='num'>{m.ku_share:.0f}%</td>"
            f"<td class='num'>{m.phrase_in_titles}</td>"
            f"<td class='num'><b>{m.opportunity}</b>{_bar(m.opportunity, 100, '#2e7d32' if m.opportunity >= 55 else '#f9a825')}</td>"
            f"<td>{_esc(m.verdict)}</td></tr>"
        )

    money_rows = ""
    for b in money:
        price = f"{currency}{b.price:.2f}" if b.price is not None else ("KU" if b.kindle_unlimited else "—")
        bsr = f"{b.bsr:,}" if b.bsr else "?"
        money_rows += (
            f"<tr><td class='kw'><a href='{_esc(b.url)}' target='_blank'>{_esc(b.title[:64])}</a></td>"
            f"<td class='num'>{bsr}</td><td class='small'>{_esc(b.category_rank or '—')}</td>"
            f"<td class='num'>{price}</td><td class='num'>{b.est_sales_per_day or '—'}</td>"
            f"<td class='num'><b>{currency}{b.est_monthly_royalty:,.0f}</b></td>"
            f"<td class='num'>{_esc(b.publication_date or '?')}</td></tr>"
        )

    gap_items = "".join(
        f"<li><b>{_esc(m.keyword)}</b> — {m.total_results:,} results, only {m.phrase_in_titles} page-1 "
        f"title(s) use the phrase (score {m.opportunity})</li>"
        for m in gaps[:8]
        if m.total_results is not None
    ) or "<li>No obvious gaps in this batch — try more keywords (--max-keywords).</li>"

    ngram_items = "".join(f'<span class="tag">{_esc(g)} ×{c}</span>' for g, c in ngrams)

    complaint_items = "".join(
        f'<div class="complaint"><div class="chead">{"★" * int(c.rating)} <b>{_esc(c.title or "(no title)")}</b>'
        f' <span class="small">— on “{_esc(c.book_title)}”</span></div><div>{_esc(c.body)}</div></div>'
        for c in complaints[:12]
    ) or "<p class='small'>No 1-2★ review texts were reachable (login wall or none exist).</p>"

    cards = ""
    for b in books[:24]:
        img = covers.get(b.asin)
        img_tag = f'<img src="{img}" alt="">' if img else '<div class="noimg">no cover</div>'
        price = f"{currency}{b.price:.2f}" if b.price is not None else ("KU only" if b.kindle_unlimited else "—")
        ku = '<span class="ku">KU</span> ' if b.kindle_unlimited else ""
        cards += (
            f'<a class="card" href="{_esc(b.url)}" target="_blank">{img_tag}<div>'
            f'<div class="t">{_esc(b.title[:80])}</div>'
            f'<div class="small">{ku}{price} · {b.rating or "—"}★ · {b.reviews:,} reviews</div></div></a>'
        )

    total_disp = f"~{total_results:,}" if total_results else "?"
    avg_price_disp = f"{currency}{sum(priced) / len(priced):.2f}" if priced else "—"
    return f"""<meta charset="utf-8"><title>KDP Intel: {_esc(seed)}</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:24px;background:#f6f7f9;color:#16181d;max-width:1300px}}
 h1{{font-size:24px;margin-bottom:4px}} h2{{font-size:17px;margin:28px 0 10px;border-bottom:2px solid #e2e5ea;padding-bottom:6px}}
 .sub{{color:#5b6472;margin-bottom:18px}}
 .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
 .kpi{{background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:12px 14px}}
 .kpi .v{{font-size:21px;font-weight:700}} .kpi .l{{font-size:11.5px;color:#5b6472;margin-top:2px}}
 table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e5ea;border-radius:10px;overflow:hidden;font-size:13px}}
 th{{background:#eef0f4;text-align:left;padding:8px 10px;font-size:11.5px;text-transform:uppercase;letter-spacing:.03em;color:#444}}
 td{{padding:7px 10px;border-top:1px solid #eef0f4;vertical-align:top}}
 .num{{text-align:right;white-space:nowrap}} .kw a{{color:#0b57d0;text-decoration:none}} .small{{font-size:11.5px;color:#5b6472}}
 .bar{{height:5px;background:#e9ebef;border-radius:3px;margin-top:4px;min-width:70px}} .fill{{height:5px;border-radius:3px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}}
 .card{{display:flex;gap:10px;background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:9px;text-decoration:none;color:inherit}}
 .card img,.noimg{{width:56px;height:82px;object-fit:cover;border-radius:4px;flex-shrink:0}}
 .noimg{{background:#eee;font-size:9px;color:#999;display:flex;align-items:center;justify-content:center}}
 .card .t{{font-size:12.5px;font-weight:600;line-height:1.3;margin-bottom:4px}}
 .ku{{background:#ff9900;color:#fff;font-size:9px;font-weight:700;padding:1px 4px;border-radius:3px}}
 .tag{{display:inline-block;background:#fff;border:1px solid #e2e5ea;border-radius:20px;padding:3px 10px;margin:3px;font-size:12px}}
 .complaint{{background:#fff;border:1px solid #e2e5ea;border-left:4px solid #c0392b;border-radius:8px;padding:10px 12px;margin-bottom:8px;font-size:13px}}
 .chead{{margin-bottom:4px;color:#c0392b}}
 .note{{background:#fffbe6;border:1px solid #f0e6b8;border-radius:8px;padding:10px 14px;font-size:12.5px;margin-top:26px;line-height:1.6}}
</style>
<h1>📊 KDP Intelligence: “{_esc(seed)}”</h1>
<div class="sub">Amazon {marketplace.upper()} · focus keyword: <b>“{_esc(best_keyword)}”</b> · generated {datetime.now().strftime("%Y-%m-%d %H:%M")}
 · pipeline time: {sum(timings.values()):.0f}s ({", ".join(f"{k} {v:.0f}s" for k, v in timings.items())})</div>
<div class="kpis">
 <div class="kpi"><div class="v">{total_disp}</div><div class="l">competing books (focus keyword)</div></div>
 <div class="kpi"><div class="v">{currency}{royalty_pool:,.0f}/mo</div><div class="l">est. royalty pool of {len(money)} deep-dived books</div></div>
 <div class="kpi"><div class="v">{med_reviews:.0f}</div><div class="l">median reviews (scanned books)</div></div>
 <div class="kpi"><div class="v">{ku_share}%</div><div class="l">in Kindle Unlimited</div></div>
 <div class="kpi"><div class="v">{avg_price_disp}</div><div class="l">avg buy price</div></div>
 <div class="kpi"><div class="v">{velocity["pct_90d"]:.0f}%</div><div class="l">of deep-dived top sellers published in last 90 days</div></div>
</div>

<h2>1 · Long-tail keyword opportunities <span class="small">(mined from Amazon autocomplete = real buyer searches)</span></h2>
<table><tr><th>#</th><th>Keyword</th><th>Results</th><th>Med. reviews</th><th>&lt;100 rev</th><th>KU%</th><th>Titles using phrase</th><th>Score</th><th>Verdict</th></tr>{kw_rows}</table>

<h2>2 · Money proof <span class="small">(product-page BSR → estimated sales &amp; royalties, “{_esc(best_keyword)}” page 1)</span></h2>
<table><tr><th>Book</th><th>BSR (Kindle)</th><th>Category rank</th><th>Price</th><th>Est. sales/day</th><th>Est. royalty/mo</th><th>Published</th></tr>{money_rows}</table>
<p class="small">Release velocity: {velocity["last_90d"]}/{velocity["dated"]} dated books published in the last 90 days
 ({velocity["pct_90d"]}%), {velocity["last_365d"]}/{velocity["dated"]} in the last year ({velocity["pct_365d"]}%).</p>

<h2>3 · Title gaps <span class="small">(searched phrases few page-1 titles use — closest thing to free shelf space)</span></h2>
<ul>{gap_items}</ul>

<h2>4 · What page-1 titles are made of <span class="small">(n-gram frequency across scanned titles)</span></h2>
<div>{ngram_items}</div>

<h2>5 · What buyers complain about <span class="small">(1-2★ reviews of the top books — your differentiation checklist)</span></h2>
{complaint_items}

<h2>6 · Top books in the niche</h2>
<div class="grid">{cards}</div>

<div class="note"><b>Methodology / proofs.</b>
 Demand: every keyword comes from Amazon's own autocomplete (only real buyer searches are suggested); its rank is the position surfaced.
 Competition: live result counts and page-1 review medians. Relevance: count of page-1 titles containing the exact phrase.
 Money: BSR read from each product page, converted with a public log-interpolated BSR→sales curve; royalty = 70% of price (−$0.06) in the
 {currency}2.99–{currency}9.99 band, else 35%. KU page-read income is NOT included, so KU-heavy niches earn more than shown.
 All estimates are order-of-magnitude research signals, not accounting. Data scraped live from Amazon; respect their ToS in your usage.</div>
"""


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="KDP Intelligence Dashboard - full pipeline")
    parser.add_argument("seed", help='Seed keyword, e.g. "air fryer"')
    parser.add_argument("--marketplace", choices=list(MARKETPLACES), default="us")
    parser.add_argument("--store", choices=list(STORES), default="kindle")
    parser.add_argument("--max-keywords", type=int, default=12)
    parser.add_argument("--books", type=int, default=30, help="Books to scan for the focus keyword")
    parser.add_argument("--deep-dive", type=int, default=10, help="Books to visit for BSR/money metrics")
    parser.add_argument("--complaint-books", type=int, default=3, help="Top books to mine 1-2 star reviews from")
    parser.add_argument("--focus", default=None, help="Skip auto-pick and use this keyword for stages 3-7")
    parser.add_argument("--impersonate", default="chrome")
    parser.add_argument("--plain-headers", action="store_true")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--proxies", default=None)
    args = parser.parse_args()

    proxy_pool: Optional[list[str]] = None
    if args.proxies:
        if args.proxies.startswith("@"):
            with open(args.proxies[1:]) as f:
                proxy_pool = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        else:
            proxy_pool = [p.strip() for p in args.proxies.split(",") if p.strip()]

    domain, currency, _mid = MARKETPLACES[args.marketplace]
    session_kwargs: dict[str, Any] = dict(
        impersonate=args.impersonate,
        stealthy_headers=not args.plain_headers,
        proxy=args.proxy,
        proxy_rotator=ProxyRotator(proxy_pool) if proxy_pool else None,
    )
    timings: dict[str, float] = {}

    def timed(label):
        class _T:
            def __enter__(self):
                self.t0 = datetime.now()

            def __exit__(self, *a):
                timings[label] = (datetime.now() - self.t0).total_seconds()

        return _T()

    # 1+2 - long-tail mining + validation
    print(f'[1/5] Mining + validating long-tails for "{args.seed}"...')
    with timed("keywords"):
        candidates = mine_suggestions(args.seed, args.marketplace, args.impersonate, args.max_keywords)
        lt_spider = LongTailSpider(
            candidates=candidates or {args.seed: 0},
            store=STORES[args.store],
            marketplace=args.marketplace,
            impersonate=args.impersonate,
            stealthy_headers=not args.plain_headers,
            proxy=args.proxy,
            proxies=proxy_pool,
        )
        lt_spider.start()
    keywords = sorted(lt_spider.results, key=lambda m: m.opportunity, reverse=True)

    book_niches = [m for m in keywords if "PRODUCT-INTENT" not in m.verdict and m.phrase_in_titles >= 2]
    best_keyword = args.focus or (book_niches[0].keyword if book_niches else args.seed)
    focus_metrics = next((m for m in keywords if m.keyword == best_keyword), None)
    print(f'      -> {len(keywords)} keywords validated; focus: "{best_keyword}"')

    # 3 - niche scan
    print(f'[2/5] Scanning up to {args.books} books for "{best_keyword}"...')
    with timed("niche scan"):
        niche_spider = KDPNicheSpider(
            keyword=best_keyword,
            store=STORES[args.store],
            max_books=args.books,
            marketplace=args.marketplace,
            impersonate=args.impersonate,
            stealthy_headers=not args.plain_headers,
            proxy=args.proxy,
            proxies=proxy_pool,
        )
        niche_spider.start()
    books = niche_spider.valid_books
    print(f"      -> {len(books)} validated books")

    # 4+5 - product-page deep dive
    deep_targets = sorted(books, key=lambda b: -b.reviews)[: args.deep_dive]
    print(f"[3/5] Deep-diving {len(deep_targets)} product pages (BSR, dates, money)...")
    with timed("deep dive"):
        dd_spider = DeepDiveSpider(books=deep_targets, session_kwargs=session_kwargs)
        dd_spider.start()
    intel = dd_spider.intel
    velocity = release_velocity(intel)
    print(f"      -> {len(intel)} books enriched; {velocity['pct_90d']}% published in last 90 days")

    # 7 - complaints
    complaint_targets = deep_targets[: args.complaint_books]
    print(f"[4/5] Mining 1-2 star reviews of top {len(complaint_targets)} books...")
    with timed("complaints"):
        c_spider = ComplaintSpider(books=complaint_targets, domain=domain, session_kwargs=session_kwargs)
        c_spider.start()
    # Merge the filtered-page snippets with those harvested from product
    # pages (the latter survive Amazon's review login wall), deduped by body
    seen_bodies: set[str] = set()
    complaints = []
    for snippet in [*c_spider.snippets, *dd_spider.review_snippets]:
        key = f"{snippet.asin}:{snippet.body[:60]}"
        if key not in seen_bodies:
            seen_bodies.add(key)
            complaints.append(snippet)
    complaints.sort(key=lambda s: s.rating)
    print(f"      -> {len(complaints)} complaint snippets")

    # 6 - analytics + dashboard
    print("[5/5] Building dashboard...")
    gaps = find_title_gaps(keywords)
    ngrams = title_ngrams([b.title for b in books])
    page = build_dashboard(
        seed=args.seed,
        marketplace=args.marketplace,
        currency=currency,
        keywords=keywords,
        best_keyword=best_keyword,
        books=books,
        intel=intel,
        velocity=velocity,
        gaps=gaps,
        ngrams=ngrams,
        complaints=complaints,
        timings=timings,
        total_results=focus_metrics.total_results if focus_metrics else None,
    )

    slug = re.sub(r"[^a-z0-9]+", "_", args.seed.lower()).strip("_") or "seed"
    html_file = f"kdp_dashboard_{args.marketplace}_{slug}.html"
    json_file = f"kdp_intel_{args.marketplace}_{slug}.json"
    with open(html_file, "w") as f:
        f.write(page)
    with open(json_file, "wb") as f:
        f.write(
            orjson.dumps(
                {
                    "seed": args.seed,
                    "marketplace": args.marketplace,
                    "focus_keyword": best_keyword,
                    "keywords": [m.model_dump() for m in keywords],
                    "books": [b.model_dump() for b in books],
                    "book_intel": [b.model_dump() for b in intel],
                    "release_velocity": velocity,
                    "title_gaps": [m.keyword for m in gaps],
                    "title_ngrams": ngrams,
                    "complaints": [c.model_dump() for c in complaints],
                    "timings_seconds": timings,
                },
                option=orjson.OPT_INDENT_2,
            )
        )
    royalty_pool = sum(b.est_monthly_royalty or 0 for b in intel)
    print(f"\n  Dashboard: {html_file}")
    print(f"  Data:      {json_file}")
    print(f"  Est. monthly royalty pool of deep-dived books: {currency}{royalty_pool:,.0f}")


if __name__ == "__main__":
    main()
