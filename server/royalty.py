"""KDP royalty & break-even engine.

Encodes Amazon's payout rules per format and inverts the BSR→sales curve,
so instead of only "this book earns ~$X" the platform can answer the real
question: "to earn $GOAL/month here, you need N sales/day ≈ BSR B — and
here is how that compares to the niche's live shelf."

All estimates are order-of-magnitude research signals, not accounting.
Rates encoded from the current KDP/ACX schedules; update the constants
when Amazon changes its tables.
"""

import os
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kdp_estimates  # noqa: E402

# All BSR->sales and royalty maths come from the shared engine; this module
# owns only the break-even inversion built on top of them.
KINDLE_ANCHORS = kdp_estimates.KINDLE_ANCHORS
BOOKS_ANCHORS = kdp_estimates.BOOKS_ANCHORS
KENP_RATE = kdp_estimates.KENP_RATE


def sales_per_day(bsr: Optional[int], store: str = "kindle",
                  marketplace: str = "us") -> Optional[float]:
    e = kdp_estimates.sales_per_day(bsr, store=store, marketplace=marketplace)
    return e.mid if e else None


def sales_band(bsr: Optional[int], store: str = "kindle",
               marketplace: str = "us"):
    """The full uncertainty band, for callers that should not show a point value."""
    return kdp_estimates.sales_per_day(bsr, store=store, marketplace=marketplace)


def bsr_for_sales(sales_day: float, store: str = "kindle") -> Optional[int]:
    return kdp_estimates.bsr_for_sales(sales_day, store)


def royalty_per_sale(fmt: str, price: float, pages: int = 120, color: bool = False,
                     file_mb: float = 2.0) -> dict[str, Any]:
    return kdp_estimates.royalty_per_sale(fmt, price, pages=pages, color=color,
                                          file_mb=file_mb)


def ku_read_payout(kenp_pages: int) -> float:
    return kdp_estimates.ku_payout_per_read(kenp_pages)


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
