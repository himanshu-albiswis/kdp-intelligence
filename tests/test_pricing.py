"""Pricing intelligence — what page-1 charges, and where the money clusters.

We already scrape every price and BSR on page 1 and never analyse them. No
flagship tool does this well, which is odd: the price band you enter at is
the one decision that changes both conversion and the royalty plan (35% vs
70%), and the shelf already shows what buyers accept.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import pricing


def book(price, bsr=None):
    return {"price": price, "bsr": bsr}


SHELF = [book(0.99, 90_000), book(2.99, 40_000), book(3.99, 12_000), book(4.99, 9_000),
         book(4.99, 15_000), book(5.99, 30_000), book(7.99, 60_000), book(12.99, 200_000),
         book(None, 5_000)]


class TestDistribution:
    def test_counts_only_priced_books(self):
        assert pricing.price_intel(SHELF)["priced"] == 8

    def test_reports_quartiles_and_extremes(self):
        out = pricing.price_intel(SHELF)
        assert out["min"] == 0.99 and out["max"] == 12.99
        assert out["q1"] <= out["median"] <= out["q3"]

    def test_bands_follow_the_kdp_royalty_boundaries(self):
        labels = [b["label"] for b in pricing.price_intel(SHELF)["bands"]]
        assert any("2.99" in l for l in labels), "the 70%-plan floor must be a boundary"
        assert any("9.99" in l for l in labels), "the 70%-plan ceiling must be a boundary"

    def test_each_band_counts_its_books(self):
        bands = {b["label"]: b for b in pricing.price_intel(SHELF)["bands"]}
        total = sum(b["count"] for b in bands.values())
        assert total == 8

    def test_empty_shelf_reports_nothing_rather_than_crashing(self):
        out = pricing.price_intel([])
        assert out["priced"] == 0 and out["sweet_spot"] is None


class TestSweetSpot:
    def test_picks_the_band_where_books_rank_best(self):
        out = pricing.price_intel(SHELF)
        spot = out["sweet_spot"]
        assert spot is not None
        assert spot["low"] <= 4.99 <= spot["high"], "the 3.99-4.99 books rank best here"

    def test_a_band_with_one_book_cannot_be_the_sweet_spot(self):
        lonely = [book(0.99, 100), book(4.99, 50_000), book(5.49, 60_000), book(5.99, 55_000)]
        spot = pricing.price_intel(lonely)["sweet_spot"]
        assert not (spot["low"] <= 0.99 <= spot["high"]), \
            "one outlier at #100 is not evidence for a band"

    def test_explains_itself(self):
        spot = pricing.price_intel(SHELF)["sweet_spot"]
        assert "book" in spot["why"].lower() and "rank" in spot["why"].lower()


class TestPriceRankRelation:
    def test_detects_cheaper_sells_better(self):
        shelf = [book(p, bsr) for p, bsr in ((2.99, 1_000), (4.99, 5_000), (6.99, 20_000),
                                             (8.99, 80_000), (12.99, 300_000))]
        assert pricing.price_intel(shelf)["price_rank_relation"]["direction"] == "cheaper sells better"

    def test_detects_pricier_sells_better(self):
        shelf = [book(p, bsr) for p, bsr in ((2.99, 300_000), (4.99, 80_000), (6.99, 20_000),
                                             (8.99, 5_000), (12.99, 1_000))]
        assert pricing.price_intel(shelf)["price_rank_relation"]["direction"] == "pricier sells better"

    def test_reports_no_relation_when_there_is_none(self):
        shelf = [book(p, bsr) for p, bsr in ((2.99, 50_000), (4.99, 1_000), (6.99, 90_000),
                                             (8.99, 2_000), (12.99, 60_000))]
        assert pricing.price_intel(shelf)["price_rank_relation"]["direction"] == "no clear relation"

    def test_too_few_ranked_books_says_so(self):
        out = pricing.price_intel([book(2.99, 1_000), book(4.99, None)])
        assert out["price_rank_relation"]["direction"] == "not enough ranked books"


class TestCurrencyGuardRespected:
    def test_refuses_when_prices_are_in_the_wrong_currency(self):
        out = pricing.price_intel(SHELF, currency_ok=False)
        assert out["priced"] == 0
        assert "currency" in out["note"].lower()
