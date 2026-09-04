"""Pricing intelligence — what page-1 charges, and where the money clusters.

Every price and BSR on page 1 is already scraped; this reads them. Bands
follow KDP's royalty boundaries because they are the decision that matters:
under $2.99 and over $9.99 the plan drops from 70% to 35%, so the shelf's
behaviour inside that window is what a new entrant needs to know.

The sweet spot is the band whose books rank best, with a floor of two books
per band — one outlier at #100 is an anecdote, not evidence for a price.
"""

import math
import statistics
from typing import Any, Optional

# (low, high, label). Boundaries are KDP's 70%-plan window.
BANDS: list[tuple[float, float, str]] = [
    (0.00, 2.98, "under $2.99 (35% plan)"),
    (2.99, 4.99, "$2.99–4.99"),
    (5.00, 6.99, "$5.00–6.99"),
    (7.00, 9.99, "$7.00–9.99"),
    (10.00, math.inf, "$10+ (35% plan)"),
]

MIN_BOOKS_FOR_SPOT = 2
MIN_RANKED_FOR_RELATION = 4


def _band_for(price: float) -> tuple[float, float, str]:
    for low, high, label in BANDS:
        if low <= price <= high:
            return low, high, label
    return BANDS[-1]


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation; robust to the wild spread of BSR values."""
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        for rank, index in enumerate(order):
            out[index] = float(rank)
        return out

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mean_x, mean_y = sum(rx) / n, sum(ry) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


def price_intel(books: list[dict[str, Any]], currency_ok: bool = True) -> dict[str, Any]:
    if not currency_ok:
        return {"priced": 0, "bands": [], "sweet_spot": None,
                "price_rank_relation": {"direction": "not computed", "rho": None},
                "note": "Prices are in a different currency than this marketplace bills "
                        "in, so no pricing analysis is possible for this scan."}

    priced = [b for b in books if b.get("price") is not None]
    if not priced:
        return {"priced": 0, "bands": [], "sweet_spot": None,
                "price_rank_relation": {"direction": "not enough ranked books", "rho": None},
                "note": "No priced books on this shelf."}

    prices = sorted(float(b["price"]) for b in priced)
    quartiles = statistics.quantiles(prices, n=4) if len(prices) >= 2 else [prices[0]] * 3

    bands: list[dict[str, Any]] = []
    for low, high, label in BANDS:
        members = [b for b in priced if low <= float(b["price"]) <= high]
        ranked = [int(b["bsr"]) for b in members if b.get("bsr")]
        bands.append({
            "label": label, "low": low, "high": None if high == math.inf else high,
            "count": len(members),
            "median_bsr": int(statistics.median(ranked)) if ranked else None,
        })

    eligible = [b for b in bands if b["count"] >= MIN_BOOKS_FOR_SPOT and b["median_bsr"]]
    sweet_spot: Optional[dict[str, Any]] = None
    if eligible:
        best = min(eligible, key=lambda b: (b["median_bsr"], -b["count"]))
        sweet_spot = {
            "low": best["low"], "high": best["high"], "label": best["label"],
            "why": (f"{best['count']} book(s) in this band with a median rank of "
                    f"#{best['median_bsr']:,} — the best-ranking band with at least "
                    f"{MIN_BOOKS_FOR_SPOT} books behind it"),
        }

    ranked_pairs = [(float(b["price"]), float(b["bsr"])) for b in priced if b.get("bsr")]
    if len(ranked_pairs) < MIN_RANKED_FOR_RELATION:
        relation = {"direction": "not enough ranked books", "rho": None}
    else:
        rho = _spearman([p for p, _ in ranked_pairs], [r for _, r in ranked_pairs])
        # BSR is lower-is-better: positive rho means pricier books rank worse.
        if rho >= 0.4:
            direction = "cheaper sells better"
        elif rho <= -0.4:
            direction = "pricier sells better"
        else:
            direction = "no clear relation"
        relation = {"direction": direction, "rho": round(rho, 2)}

    return {
        "priced": len(priced),
        "min": prices[0], "q1": round(quartiles[0], 2), "median": round(quartiles[1], 2),
        "q3": round(quartiles[2], 2), "max": prices[-1],
        "bands": bands,
        "sweet_spot": sweet_spot,
        "price_rank_relation": relation,
        "note": f"{len(priced)} priced books; bands follow KDP's 70% royalty window.",
    }
