"""Demand scoring and the supply-gap check.

Demand is deliberately breadth-first: a concept that only one source surfaced
is demoted no matter how loud it was there, because one enthusiastic thread is
not a market. Corroboration means *distinct* sources — two posts in the same
subreddit is one source saying something twice.

The gap score is demand divided by what Amazon already supplies. The jackpot
row is strong multi-source demand against a thin shelf with weak review moats:
people are looking for a book that effectively is not there.
"""

import math
from typing import Any, Optional

# How much each source is trusted as evidence of book demand. Google Trends is
# low because its daily feed is news-shaped (measured: "rays", "djokovic").
SOURCE_WEIGHT = {
    "reddit": 1.0,
    "youtube": 0.7,
    "amazon_new_releases": 0.5,
    "google_trends": 0.25,
}

CORROBORATION_BONUS = 22.0   # per additional distinct source


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalise(intensity: float, source: str) -> float:
    """Compress wildly different scales (upvotes vs autocomplete rank) to 0..1."""
    if source == "reddit":
        return min(1.0, math.log10(max(intensity, 1) + 1) / 3.0)   # ~1000 upvotes -> 1.0
    if source == "google_trends":
        return min(1.0, math.log10(max(intensity, 1) + 1) / 5.0)   # ~100k -> 1.0
    return min(1.0, max(0.0, float(intensity)))


def distinct_sources(concept: dict[str, Any]) -> set[str]:
    return {e.get("source") for e in concept.get("evidence") or [] if e.get("source")}


def is_corroborated(concept: dict[str, Any]) -> bool:
    """Two or more *different* sources surfaced this."""
    return len(distinct_sources(concept)) >= 2


def demand(concept: dict[str, Any]) -> float:
    """0..100. Single-source concepts are capped below 100 by construction."""
    evidence = concept.get("evidence") or []
    if not evidence:
        return 0.0

    best_per_source: dict[str, float] = {}
    for item in evidence:
        source = item.get("source") or "unknown"
        value = _normalise(float(item.get("intensity") or 0), source) * \
            SOURCE_WEIGHT.get(source, 0.4)
        best_per_source[source] = max(best_per_source.get(source, 0.0), value)

    intensity_part = sum(best_per_source.values()) * 45.0
    breadth_part = (len(best_per_source) - 1) * CORROBORATION_BONUS
    score = intensity_part + breadth_part
    if len(best_per_source) < 2:
        # One source is an anecdote, not a market. Hard ceiling.
        score = min(score, 55.0)
    return round(min(100.0, score), 1)


def gap(demand_score: float, supply: dict[str, Any]) -> dict[str, Any]:
    """Demand ÷ supply. Returns score None when Amazon gave us nothing to divide by."""
    total = supply.get("total_results")
    reviews = supply.get("median_reviews")
    if total is None or reviews is None:
        return {"score": None, "verdict": "VERIFY — no clean Amazon read yet",
                "supply": supply,
                "basis": "Amazon returned no usable results page for this concept"}

    # Both compress logarithmically, anchored on real shelves: the difference
    # between 100 and 1,000 competitors matters far more than 50,000 vs 51,000.
    #   competition: 100 books -> 0.0,  1k -> 0.33,  10k -> 0.67,  100k -> 1.0
    #   moat:          5 reviews -> 0.0,  50 -> 0.40,  500 -> 0.80, 5k -> 1.0
    competition = _clamp((math.log10(max(float(total), 1)) - 2.0) / 3.0)
    moat = _clamp((math.log10(max(float(reviews), 1)) - 0.7) / 2.5)
    supply_pressure = 0.6 * competition + 0.4 * moat

    score = round(max(0.0, demand_score * (1.0 - supply_pressure)), 1)

    if score >= 70 and demand_score >= 55:
        verdict = "GOLDMINE — strong multi-source demand, thin shelf"
    elif score >= 50:
        verdict = "GO — real demand the shelf under-serves"
    elif score >= 30:
        verdict = "ANGLE — enter only with clear differentiation"
    else:
        verdict = "CROWDED — AVOID unless you have an unfair advantage"

    return {"score": score, "verdict": verdict, "supply": supply,
            "basis": (f"demand {demand_score} against {int(float(total)):,} competing books "
                      f"and a median review moat of {int(float(reviews)):,}")}
