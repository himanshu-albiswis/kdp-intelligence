"""Category intelligence — which shelves to publish on, and what a badge costs.

Publisher Rocket's headline claim is "sales/day needed to hit #1 in each
category". Nobody outside Amazon can know that exactly without seeing the
current #1, so this module states what it can actually defend:

  * Page-1 books of the niche carry their category ranks. A book observed at
    rank R in category C, with a store-wide BSR, gives an *entry bar*: taking
    the badge requires outselling at least that book.
  * When the observed book IS the category's #1, the read is exact — the
    current badge-holder's own sales rate — and it says so.

The recommendation logic prefers categories where the niche is genuinely
present and the badge is cheap: small shelves the niche's own books already
sit on, not big shelves an optimist could nominate.
"""

import re
from typing import Any, Optional

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kdp_estimates  # noqa: E402

# "#2 in Latin American Cooking" — as rendered in tag-stripped detail text.
# Terminators: the next rank, a parenthesis, the next labelled section
# ("Customer Reviews:"), or end of text. Amazon runs sections together.
_RANK = re.compile(
    r"#([\d,]+)\s+in\s+([A-Za-z][A-Za-z'&,\- ]{2,60}?)"
    r"(?=\s+#|\s*\(|\s+Customer Reviews|$)")


def extract_category_ranks(detail_text: str) -> list[dict[str, Any]]:
    """Every category rank in a product page's detail text, structured.

    The store-wide ranks ("#33,125 in Kindle Store") are positions in the
    whole shop, not shelves you can badge on, so they are excluded.
    """
    ranks: list[dict[str, Any]] = []
    for rank, category in _RANK.findall(detail_text or ""):
        name = category.strip().rstrip(",")
        head = name.split()[0] if name else ""
        if head in ("Kindle", "Books"):
            continue
        ranks.append({"rank": int(rank.replace(",", "")), "category": name})
    return ranks


def category_intel(books: list[dict[str, Any]], marketplace: str = "us",
                   store: str = "kindle") -> dict[str, Any]:
    """Aggregate observed category ranks into shelf-level advice."""
    shelves: dict[str, dict[str, Any]] = {}

    for book in books:
        bsr = book.get("bsr")
        sales = kdp_estimates.sales_per_day(bsr, store=store, marketplace=marketplace)
        for entry in book.get("category_ranks") or []:
            name = entry["category"]
            shelf = shelves.setdefault(name, {
                "category": name, "books_observed": 0,
                "best_observed_rank": None, "entry_sales_day": None,
                "exact": False, "witness": None,
            })
            shelf["books_observed"] += 1
            rank = entry["rank"]
            if shelf["best_observed_rank"] is None or rank < shelf["best_observed_rank"]:
                shelf["best_observed_rank"] = rank
                shelf["entry_sales_day"] = round(sales.mid, 2) if sales else None
                shelf["exact"] = rank == 1
                shelf["witness"] = {"asin": book.get("asin"),
                                    "title": (book.get("title") or "")[:60],
                                    "bsr": bsr}

    if not shelves:
        return {"categories": [], "picks": [],
                "note": ("No category ranks were observed on the deep-dived pages, "
                         "so no shelf advice is possible for this scan.")}

    for shelf in shelves.values():
        rank = shelf["best_observed_rank"]
        sales_text = (f"≈{shelf['entry_sales_day']:g} sales/day"
                      if shelf["entry_sales_day"] is not None else "unknown sales")
        if shelf["exact"]:
            shelf["read"] = (f"the current #1 sells {sales_text} — beat that and "
                             f"the badge is yours")
        else:
            shelf["read"] = (f"the #{rank} book sells {sales_text}, so #1 takes "
                             f"at least that")

    # Presence first, then price of the badge. A category one book wandered
    # into can look cheap to badge and still be the wrong shelf; a live scan
    # for an air-fryer niche recommended "Juicer Recipes" on that basis.
    # An exact rank-1 witness is hard evidence of the badge's cost and is
    # never demoted; the presence rule applies to floors read off deeper ranks.
    strongest = max(s["books_observed"] for s in shelves.values())
    ordered = sorted(shelves.values(),
                     key=lambda s: (not s["exact"] and s["books_observed"] < min(2, strongest),
                                    s["entry_sales_day"] is None,
                                    not s["exact"],
                                    s["entry_sales_day"] or 0,
                                    -s["books_observed"]))

    picks: list[dict[str, Any]] = []
    for shelf in ordered:
        if shelf["entry_sales_day"] is None:
            continue
        picks.append({
            "category": shelf["category"],
            "why": (f"{shelf['books_observed']} niche book(s) already shelve here and "
                    f"{shelf['read']}"),
            "entry_sales_day": shelf["entry_sales_day"],
            "exact": shelf["exact"],
        })
        if len(picks) == 3:
            break

    return {"categories": ordered, "picks": picks,
            "note": (f"{len(ordered)} categories observed across "
                     f"{sum(s['books_observed'] for s in ordered)} shelf placements. "
                     f"Entry bars are read from observed books; only a rank-1 "
                     f"witness makes them exact.")}
