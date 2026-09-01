"""Single source of truth for BSR → sales → royalty maths.

Every module that turns a Best Sellers Rank into money imports from here.
Before this module existed the curve and the ebook royalty rule were
copy-pasted into `kdp_intel_dashboard.py` and `server/royalty.py`, and the
two had already diverged: the dashboard subtracted a flat $0.06 delivery
fee while the royalty engine subtracted $0.15/MB. The same book earned two
different numbers depending on which endpoint you asked. Keeping one
implementation makes that class of bug impossible rather than merely fixed.

Honesty rules encoded here:

  * A BSR→sales conversion is an order-of-magnitude inference from a public
    curve, not a measurement. So `sales_per_day` returns an `Estimate` band
    (low/mid/high) with a confidence label instead of a bare float that
    invites false precision downstream.
  * Confidence is never "high". No public curve earns that.
  * The curve is calibrated on the US store. Other marketplaces are scaled
    by rough relative Kindle market size — a correction that is crude but
    strictly better than pretending a UK BSR of 10,000 means what a US one
    does.
"""

import bisect
import math
import re
from collections import Counter
from dataclasses import dataclass, asdict
from typing import Any, Optional

# --- BSR -> sales/day anchors (US store), log-log interpolated -------------
# Public approximations in the spirit of the widely used calculators.
KINDLE_ANCHORS: list[tuple[int, float]] = [
    (1, 5000.0), (10, 1500.0), (100, 350.0), (1_000, 100.0), (5_000, 30.0),
    (10_000, 15.0), (50_000, 3.0), (100_000, 1.0), (500_000, 0.15), (1_000_000, 0.03),
]
BOOKS_ANCHORS: list[tuple[int, float]] = [
    (100, 400.0), (1_000, 65.0), (5_000, 20.0), (10_000, 10.0), (25_000, 5.0),
    (50_000, 2.5), (100_000, 1.0), (500_000, 0.1),
]

# Rough relative Kindle store size vs the US (US = 1.0). Same rank means
# very different volume in a smaller store; ignoring this overstated every
# non-US marketplace by up to 20x.
MARKETPLACE_SCALE: dict[str, float] = {
    "us": 1.00, "uk": 0.35, "de": 0.30, "ca": 0.12, "au": 0.10, "fr": 0.09,
    "it": 0.07, "es": 0.07, "jp": 0.20, "br": 0.05, "mx": 0.04, "in": 0.04,
    "nl": 0.04, "se": 0.03,
}

# Payout constants (update when Amazon changes its schedules)
KENP_RATE = 0.0045          # $/page read; Amazon sets it monthly, ~0.004–0.005
EBOOK_DELIVERY_PER_MB = 0.15
PRINT_FIXED_BW = 0.85
PRINT_PER_PAGE_BW = 0.012
PRINT_PER_PAGE_COLOR = 0.065
HARDCOVER_FIXED = 5.65
KU_READ_THROUGH = 0.70      # share of a borrow actually read, on average

# Symbols and ISO codes that denote the same currency. Amazon renders the
# same price as "$4.99", "USD 4.99" or "US$4.99" depending on locale.
CURRENCY_ALIASES: list[set[str]] = [
    {"$", "USD", "US$"}, {"₹", "INR"}, {"£", "GBP"}, {"€", "EUR"},
    {"¥", "JPY", "CNY"}, {"CDN$", "CAD", "C$"}, {"AU$", "AUD", "A$"},
    {"R$", "BRL"}, {"MX$", "MXN"}, {"kr", "SEK"},
]

_CURRENCY_TOKEN = re.compile(r"^\s*([^\d\s.,]{1,3}|[A-Z]{3})\s*(?=[\d.,])")


def observed_currency(price_texts: list[str]) -> Optional[str]:
    """The currency Amazon actually rendered, read off the price strings.

    `_to_float` throws the symbol away, which is how amazon.com serving INR
    to an Indian IP produced `avg_buy_price: $1593.95` — rupees priced as
    dollars, with royalties computed on top.
    """
    found: list[str] = []
    for text in price_texts or []:
        match = _CURRENCY_TOKEN.match(text or "")
        if match:
            found.append(match.group(1).strip())
    if not found:
        return None
    return Counter(found).most_common(1)[0][0]


def currency_matches(expected: str, observed: Optional[str]) -> bool:
    """False only on positive evidence of a different currency."""
    if not observed or not expected:
        return True
    if observed == expected:
        return True
    for group in CURRENCY_ALIASES:
        if expected in group and observed in group:
            return True
    return False


# Band multipliers: how much slop to admit around the curve's midpoint.
_BAND = {"medium": 1.6, "low": 2.5}


@dataclass(frozen=True)
class Estimate:
    """An order-of-magnitude estimate with its uncertainty made explicit."""

    low: float
    mid: float
    high: float
    confidence: str          # "medium" | "low" — never "high"
    basis: str = "public BSR→sales curve"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def display(self, currency: str = "", decimals: int = 0) -> str:
        """Render as a range, so no reader mistakes this for accounting."""
        fmt = f"{{:,.{decimals}f}}"
        return f"{currency}{fmt.format(self.low)}–{currency}{fmt.format(self.high)}"


def _anchors(store: str) -> list[tuple[int, float]]:
    return KINDLE_ANCHORS if store == "kindle" else BOOKS_ANCHORS


def _confidence(bsr: int, store: str) -> str:
    """The curve is best sampled in the mid-list. Edges are guesswork."""
    anchors = _anchors(store)
    lo, hi = anchors[0][0], anchors[-1][0]
    if bsr < max(lo * 100, 1_000) or bsr > 100_000:
        return "low"
    return "medium"


def _raw_sales_per_day(bsr: int, store: str) -> float:
    """Log-log interpolation across the anchor table."""
    anchors = _anchors(store)
    xs = [a[0] for a in anchors]
    if bsr <= xs[0]:
        return anchors[0][1]
    if bsr >= xs[-1]:
        return anchors[-1][1]
    i = bisect.bisect_left(xs, bsr)
    (x0, y0), (x1, y1) = anchors[i - 1], anchors[i]
    t = (math.log10(bsr) - math.log10(x0)) / (math.log10(x1) - math.log10(x0))
    return 10 ** (math.log10(y0) + t * (math.log10(y1) - math.log10(y0)))


def sales_per_day(
    bsr: Optional[int],
    store: str = "kindle",
    marketplace: str = "us",
) -> Optional[Estimate]:
    """Estimated units/day for a rank, as a band. None when there is no rank."""
    if not bsr or bsr <= 0:
        return None
    mid = _raw_sales_per_day(int(bsr), store) * MARKETPLACE_SCALE.get(marketplace, 1.0)
    conf = _confidence(int(bsr), store)
    spread = _BAND[conf]
    return Estimate(
        low=round(mid / spread, 3),
        mid=round(mid, 3),
        high=round(mid * spread, 3),
        confidence=conf,
        basis=f"public BSR→sales curve, {store} store, {marketplace.upper()} scale",
    )


def bsr_for_sales(sales_day: float, store: str = "kindle") -> Optional[int]:
    """Inverse of the curve: what rank does a sales rate imply?"""
    if not sales_day or sales_day <= 0:
        return None
    anchors = _anchors(store)
    ys = [a[1] for a in anchors]
    if sales_day >= ys[0]:
        return anchors[0][0]
    if sales_day <= ys[-1]:
        return anchors[-1][0]
    for i in range(1, len(anchors)):
        if ys[i] <= sales_day:
            (x0, y0), (x1, y1) = anchors[i - 1], anchors[i]
            t = (math.log10(sales_day) - math.log10(y0)) / (math.log10(y1) - math.log10(y0))
            return int(round(10 ** (math.log10(x0) + t * (math.log10(x1) - math.log10(x0)))))
    return anchors[-1][0]


def royalty_per_sale(
    fmt: str,
    price: Optional[float],
    pages: int = 120,
    color: bool = False,
    file_mb: float = 2.0,
) -> dict[str, Any]:
    """Royalty for one sale. fmt: ebook | paperback | hardcover | audiobook_acx | audiobook_wide."""
    if fmt not in ("ebook", "paperback", "hardcover", "audiobook_acx", "audiobook_wide"):
        raise ValueError(f"unknown format {fmt!r}")
    if price is None or price <= 0:
        return {"royalty": None, "plan": None, "print_cost": None,
                "notes": ["no price available on the listing"]}

    notes: list[str] = []
    if fmt == "ebook":
        if 2.99 <= price <= 9.99:
            delivery = round(file_mb * EBOOK_DELIVERY_PER_MB, 2)
            royalty = 0.70 * price - delivery
            plan = "70%"
            notes.append(f"70% plan, minus ${delivery:.2f} delivery ({file_mb}MB)")
        else:
            royalty = 0.35 * price
            plan = "35%"
            notes.append("outside the $2.99–$9.99 band → 35% plan, no delivery fee")
        return {"royalty": round(max(0.0, royalty), 2), "plan": plan,
                "print_cost": None, "notes": notes}

    if fmt in ("paperback", "hardcover"):
        fixed = HARDCOVER_FIXED if fmt == "hardcover" else PRINT_FIXED_BW
        per_page = PRINT_PER_PAGE_COLOR if color else PRINT_PER_PAGE_BW
        print_cost = round(fixed + pages * per_page, 2)
        royalty = 0.60 * price - print_cost
        if royalty < 0:
            notes.append(f"price below break-even — minimum viable list is ${print_cost / 0.60:.2f}")
        if color:
            notes.append("premium color printing — verify against the KDP calculator for your trim")
        return {"royalty": round(max(0.0, royalty), 2), "plan": "60% − printing",
                "print_cost": print_cost, "notes": notes}

    if fmt == "audiobook_acx":
        return {"royalty": round(0.40 * price, 2), "plan": "40% (Audible-exclusive)",
                "print_cost": None,
                "notes": ["ACX exclusive is a 7-year commitment; royalty-share narration halves your side"]}

    return {"royalty": round(0.25 * price, 2), "plan": "25% (wide)",
            "print_cost": None, "notes": []}


def ku_payout_per_read(kenp_pages: int) -> float:
    """Payout for one full KU read-through of a book of this KENP length."""
    return round(kenp_pages * KENP_RATE, 2)


def ku_monthly_income(
    sales_per_day: float,
    ku_share: float,
    kenp_pages: int = 300,
    read_through: float = KU_READ_THROUGH,
) -> float:
    """Monthly KU page-read income alongside paid sales.

    Ignoring this systematically understated revenue in exactly the niches
    KDP authors care about: `ku_share` above 50% is common, and those
    borrows pay per page rather than per sale.
    """
    if not sales_per_day or ku_share <= 0:
        return 0.0
    borrows_per_day = sales_per_day * ku_share
    return round(borrows_per_day * 30 * kenp_pages * KENP_RATE * read_through, 2)


def niche_income(
    books: list[dict[str, Any]],
    ku_share: float,
    marketplace: str = "us",
    store: str = "kindle",
    kenp_pages: int = 300,
    currency_ok: bool = True,
) -> dict[str, Any]:
    """Roll a niche's books up into a monthly income band.

    Two corrections over the old `royalty_pool_month` single number:

      * KU page-read income is included. It used to be dropped entirely,
        which understated every KU-heavy niche — and `ku_share` above 50%
        is the norm in the categories this tool is pointed at.
      * The result is a band with a confidence label, because it is built
        from per-book bands. Summing midpoints and printing cents implied a
        precision the underlying curve cannot support.
    """
    if not currency_ok:
        # Prices are denominated in a currency this marketplace does not use,
        # so every royalty rule below would be applied to the wrong numbers.
        # Refusing beats reporting a confident wrong figure.
        return {"paid_low": 0.0, "paid_mid": 0.0, "paid_high": 0.0,
                "ku_mid": 0.0, "total_low": 0.0, "total_mid": 0.0,
                "total_high": 0.0, "confidence": "none", "books_counted": 0,
                "basis": "not computed — the listed prices are in a different "
                         "currency than this marketplace uses"}

    paid = {"low": 0.0, "mid": 0.0, "high": 0.0}
    ku_mid = 0.0
    counted = 0
    confidences: list[str] = []

    for book in books:
        bsr, price = book.get("bsr"), book.get("price")
        if not bsr or price is None:
            continue
        band = sales_per_day(bsr, store=store, marketplace=marketplace)
        per_sale = royalty_per_sale("ebook" if store == "kindle" else "paperback",
                                    price)["royalty"]
        if band is None or per_sale is None:
            continue
        counted += 1
        confidences.append(band.confidence)
        for key in paid:
            paid[key] += getattr(band, key) * 30 * per_sale
        ku_mid += ku_monthly_income(band.mid, ku_share, kenp_pages)

    if not counted:
        return {"paid_low": 0.0, "paid_mid": 0.0, "paid_high": 0.0,
                "ku_mid": 0.0, "total_low": 0.0, "total_mid": 0.0,
                "total_high": 0.0, "confidence": "none", "books_counted": 0,
                "basis": "no book had both a rank and a price"}

    confidence = "low" if "low" in confidences else "medium"
    return {
        "paid_low": round(paid["low"], 2),
        "paid_mid": round(paid["mid"], 2),
        "paid_high": round(paid["high"], 2),
        "ku_mid": round(ku_mid, 2),
        "total_low": round(paid["low"] + ku_mid, 2),
        "total_mid": round(paid["mid"] + ku_mid, 2),
        "total_high": round(paid["high"] + ku_mid, 2),
        "confidence": confidence,
        "books_counted": counted,
        "basis": (f"{counted} book(s) with rank and price, {marketplace.upper()} scale, "
                  f"KU share {ku_share:.0%} at {KENP_RATE}/page"),
    }


# --------------------------------------------------------------------------
# Demand index — the honest answer to "estimated searches per month"
# --------------------------------------------------------------------------
# Nobody outside Amazon knows search volume; tools that print one are curve-
# fitting a guess. What is observable is how eagerly Amazon's own
# autocomplete surfaces a phrase — Amazon only suggests what people type.
# Two measurements, both from Amazon itself:
#
#   prefix depth  how few typed characters make the phrase appear.
#                 Appearing after 3 characters is mass demand; appearing
#                 only when fully typed is thin.
#   position      where in the ten-slot dropdown it appears at that depth.
#
# The result is an index for comparing keywords, published with its basis so
# nobody mistakes it for a volume figure.

DEMAND_BANDS = [(75, "very high"), (55, "high"), (35, "moderate"), (15, "niche")]


def demand_index(prefix_len: Optional[int], keyword_len: int,
                 position: Optional[int], suggest_rank: int) -> dict[str, Any]:
    """0-100 comparability index from autocomplete observations."""
    if prefix_len is None or keyword_len <= 0:
        return {"index": 0, "band": "thin",
                "basis": "never surfaced by Amazon autocomplete, even fully typed"}

    # Depth: how much of the phrase had to be typed. 0.15 -> ~1.0 as the
    # required prefix shrinks; typing everything earns the floor, not zero —
    # surfacing at all still separates it from phrases Amazon never suggests.
    typed_share = min(1.0, max(0.0, prefix_len / max(keyword_len, 1)))
    depth_part = 0.15 + 0.85 * (1.0 - typed_share) ** 1.5

    # Position scales the depth signal rather than adding to it: slot 1 of a
    # fully-typed phrase is still a weak signal, and treating position as an
    # independent term inflated exactly that case.
    pos = position if position is not None else 9
    position_factor = 0.72 + 0.28 * max(0.3, 1.0 - pos * 0.078)

    # Mining rank (which expansion surfaced it first) is a small tiebreak.
    rank_bonus = 3.0 * max(0.0, 1.0 - suggest_rank / 300.0)

    score = round(100 * depth_part * position_factor + rank_bonus)
    score = max(1, min(100, score))

    band = "thin"
    for floor, name in DEMAND_BANDS:
        if score >= floor:
            band = name
            break

    return {
        "index": score,
        "band": band,
        "basis": (f"Amazon autocomplete surfaces this after {prefix_len} typed "
                  f"character(s), at dropdown position {pos + 1}. This compares "
                  f"keywords against each other; it is not a volume estimate."),
    }
