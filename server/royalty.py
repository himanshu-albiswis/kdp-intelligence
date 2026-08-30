"""KDP royalty & break-even engine.

Encodes Amazon's payout rules per format and inverts the BSR→sales curve,
so instead of only "this book earns ~$X" the platform can answer the real
question: "to earn $GOAL/month here, you need N sales/day ≈ BSR B — and
here is how that compares to the niche's live shelf."

All estimates are order-of-magnitude research signals, not accounting.
Rates encoded from the current KDP/ACX schedules; update the constants
when Amazon changes its tables.
"""

import bisect
import math
from typing import Any, Optional

# BSR -> sales/day anchor curves (public approximations, log-log interpolated)
KINDLE_ANCHORS = [
    (1, 5000.0), (10, 1500.0), (100, 350.0), (1_000, 100.0), (5_000, 30.0),
    (10_000, 15.0), (50_000, 3.0), (100_000, 1.0), (500_000, 0.15), (1_000_000, 0.03),
]
BOOKS_ANCHORS = [
    (100, 400.0), (1_000, 65.0), (5_000, 20.0), (10_000, 10.0), (25_000, 5.0),
    (50_000, 2.5), (100_000, 1.0), (500_000, 0.1),
]

EBOOK_DELIVERY_PER_MB = 0.15
KENP_RATE = 0.0045          # $/page read; Amazon sets it monthly, ~0.004-0.005
PRINT_FIXED_BW = 0.85       # + per-page
PRINT_PER_PAGE_BW = 0.012
PRINT_PER_PAGE_COLOR = 0.065
HARDCOVER_FIXED = 5.65


def _anchors(store: str) -> list[tuple[int, float]]:
    return KINDLE_ANCHORS if store == "kindle" else BOOKS_ANCHORS


def sales_per_day(bsr: Optional[int], store: str = "kindle") -> Optional[float]:
    if not bsr or bsr <= 0:
        return None
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


def bsr_for_sales(sales_day: float, store: str = "kindle") -> Optional[int]:
    """Inverse of the curve: what BSR does a sales rate correspond to?"""
    if sales_day <= 0:
        return None
    anchors = _anchors(store)
    ys = [a[1] for a in anchors]
    if sales_day >= ys[0]:
        return anchors[0][0]
    if sales_day <= ys[-1]:
        return anchors[-1][0]
    # ys descend; find the bracketing pair
    for i in range(1, len(anchors)):
        if ys[i] <= sales_day:
            (x0, y0), (x1, y1) = anchors[i - 1], anchors[i]
            t = (math.log10(sales_day) - math.log10(y0)) / (math.log10(y1) - math.log10(y0))
            return int(round(10 ** (math.log10(x0) + t * (math.log10(x1) - math.log10(x0)))))
    return anchors[-1][0]


def royalty_per_sale(
    fmt: str,
    price: float,
    pages: int = 120,
    color: bool = False,
    file_mb: float = 2.0,
) -> dict[str, Any]:
    """Royalty per sale for a format. fmt: ebook | paperback | hardcover | audiobook_acx | audiobook_wide."""
    notes: list[str] = []
    if fmt == "ebook":
        if 2.99 <= price <= 9.99:
            delivery = round(file_mb * EBOOK_DELIVERY_PER_MB, 2)
            r = 0.70 * price - delivery
            plan = "70%"
            notes.append(f"70% plan, minus ${delivery:.2f} delivery ({file_mb}MB)")
        else:
            r = 0.35 * price
            plan = "35%"
            notes.append("outside the $2.99–$9.99 band -> 35% plan, no delivery fee")
        return {"royalty": round(max(0.0, r), 2), "plan": plan, "print_cost": None, "notes": notes}

    if fmt in ("paperback", "hardcover"):
        fixed = HARDCOVER_FIXED if fmt == "hardcover" else PRINT_FIXED_BW
        per_page = PRINT_PER_PAGE_COLOR if color else PRINT_PER_PAGE_BW
        print_cost = round(fixed + pages * per_page, 2)
        r = 0.60 * price - print_cost
        if r < 0:
            notes.append(f"price below break-even — minimum viable list is ${print_cost / 0.60:.2f}")
        if color:
            notes.append("premium color printing — verify against the KDP calculator for your trim")
        return {"royalty": round(max(0.0, r), 2), "plan": "60% − printing", "print_cost": print_cost, "notes": notes}

    if fmt == "audiobook_acx":
        return {"royalty": round(0.40 * price, 2), "plan": "40% (Audible-exclusive)", "print_cost": None,
                "notes": ["ACX exclusive is a 7-year commitment; royalty-share narration halves your side"]}
    if fmt == "audiobook_wide":
        return {"royalty": round(0.25 * price, 2), "plan": "25% (wide)", "print_cost": None, "notes": []}

    raise ValueError(f"unknown format {fmt!r}")


def ku_read_payout(kenp_pages: int) -> float:
    """Payout for one full KU read-through of a book with this KENP length."""
    return round(kenp_pages * KENP_RATE, 2)


def break_even(
    goal_month: float,
    fmt: str,
    price: float,
    pages: int = 120,
    color: bool = False,
    file_mb: float = 2.0,
    store: Optional[str] = None,
    niche_bsrs: Optional[list[int]] = None,
) -> dict[str, Any]:
    """Goal $/month -> required sales/day -> required BSR -> live-shelf feasibility."""
    store = store or ("kindle" if fmt == "ebook" else "books")
    per_sale = royalty_per_sale(fmt, price, pages=pages, color=color, file_mb=file_mb)
    r = per_sale["royalty"]
    if r <= 0:
        return {"error": "Royalty per sale is $0 at these settings — raise the price or cut printing cost",
                **per_sale}

    sales_needed_day = goal_month / 30 / r
    bsr_needed = bsr_for_sales(sales_needed_day, store)

    feasibility = None
    if niche_bsrs:
        bsrs = sorted(b for b in niche_bsrs if b)
        if bsrs:
            achieving = sum(1 for b in bsrs if b <= (bsr_needed or 0))
            feasibility = {
                "niche_best_bsr": bsrs[0],
                "niche_median_bsr": bsrs[len(bsrs) // 2],
                "niche_worst_bsr": bsrs[-1],
                "incumbents_at_or_above_goal": achieving,
                "incumbents_checked": len(bsrs),
                "read": (
                    f"{achieving} of {len(bsrs)} deep-dived incumbents already sit at BSR "
                    f"{bsr_needed:,} or better — "
                    + ("goal is proven achievable on this shelf." if achieving
                       else "nobody on this shelf currently sells at that rate; goal may be optimistic.")
                ) if bsr_needed else "could not invert the curve for this goal",
            }

    return {
        "goal_month": goal_month,
        "format": fmt,
        "store": store,
        "price": price,
        "royalty_per_sale": r,
        "plan": per_sale["plan"],
        "print_cost": per_sale["print_cost"],
        "sales_needed_per_day": round(sales_needed_day, 1),
        "sales_needed_per_month": int(round(sales_needed_day * 30)),
        "bsr_needed": bsr_needed,
        "feasibility": feasibility,
        "ku_note": (f"If KU-enrolled: one full read of ~{pages * 2} KENP pages pays ~${ku_read_payout(pages * 2):.2f} "
                    f"on top of sales") if fmt == "ebook" else None,
        "notes": per_sale["notes"],
    }
