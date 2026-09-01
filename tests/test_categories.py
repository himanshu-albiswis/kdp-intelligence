"""Tests for category intelligence — Publisher Rocket's killer feature, honestly.

Rocket sells "sales/day needed to hit #1 in each category". Nobody outside
Amazon knows that exactly unless they can see the current #1. What we CAN
observe: page-1 books of the niche carry their category ranks ("#2 in Latin
American Cooking") and their store-wide BSR. From a book observed at rank R
in category C, the entry bar for C is at least that book's estimated sales —
and when the observed book IS #1, the read is exact, not a floor.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import categories


def book(asin="B000000001", bsr=33_000, ranks=None, title="A Book"):
    return {"asin": asin, "title": title, "bsr": bsr,
            "category_ranks": ranks or []}


class TestExtraction:
    DETAIL = ("Best Sellers Rank: #33,125 in Kindle Store ( See Top 100 ) "
              "#2 in Latin American Cooking #5 in Latin American Cooking, Food & Wine "
              "#18 in Quick & Easy Cooking")

    def test_extracts_every_category_rank_not_just_the_first(self):
        ranks = categories.extract_category_ranks(self.DETAIL)
        assert len(ranks) == 3

    def test_each_entry_carries_rank_and_category(self):
        ranks = categories.extract_category_ranks(self.DETAIL)
        assert ranks[0] == {"rank": 2, "category": "Latin American Cooking"}

    def test_the_store_wide_rank_is_not_a_category(self):
        ranks = categories.extract_category_ranks(self.DETAIL)
        assert all("Kindle Store" not in r["category"] for r in ranks)

    def test_no_ranks_yields_an_empty_list(self):
        assert categories.extract_category_ranks("no ranks here") == []


class TestCategoryIntel:
    def intel(self):
        return categories.category_intel([
            book("B1", bsr=20_000, ranks=[{"rank": 2, "category": "Air Fryer Recipes"},
                                          {"rank": 9, "category": "Quick & Easy Cooking"}]),
            book("B2", bsr=45_000, ranks=[{"rank": 5, "category": "Air Fryer Recipes"}]),
            book("B3", bsr=300_000, ranks=[{"rank": 1, "category": "Small Appliance Recipes"}]),
        ], marketplace="us")

    def test_groups_books_by_category(self):
        cats = {c["category"]: c for c in self.intel()["categories"]}
        assert cats["Air Fryer Recipes"]["books_observed"] == 2
        assert cats["Quick & Easy Cooking"]["books_observed"] == 1

    def test_entry_bar_comes_from_the_best_ranked_observed_book(self):
        cats = {c["category"]: c for c in self.intel()["categories"]}
        c = cats["Air Fryer Recipes"]
        assert c["best_observed_rank"] == 2
        assert c["entry_sales_day"] is not None and c["entry_sales_day"] > 0

    def test_rank_one_observed_is_an_exact_read_not_a_floor(self):
        cats = {c["category"]: c for c in self.intel()["categories"]}
        assert cats["Small Appliance Recipes"]["exact"] is True
        assert cats["Air Fryer Recipes"]["exact"] is False

    def test_the_floor_is_labelled_at_least_when_not_exact(self):
        cats = {c["category"]: c for c in self.intel()["categories"]}
        assert "at least" in cats["Air Fryer Recipes"]["read"].lower()
        assert "at least" not in cats["Small Appliance Recipes"]["read"].lower()

    def test_recommends_up_to_three_categories(self):
        picks = self.intel()["picks"]
        assert 1 <= len(picks) <= 3
        assert all(p["why"] for p in picks)

    def test_an_easier_badge_ranks_ahead_of_a_harder_one(self):
        # B3 at rank #1 with a deep BSR: cheap badge. It should outrank a
        # category whose observed leader sells far more.
        picks = self.intel()["picks"]
        assert picks[0]["category"] == "Small Appliance Recipes"

    def test_no_category_data_says_so_rather_than_crashing(self):
        out = categories.category_intel([book(ranks=[])], marketplace="us")
        assert out["categories"] == []
        assert "no category" in out["note"].lower()

    def test_books_without_bsr_still_count_for_presence(self):
        out = categories.category_intel(
            [book("B1", bsr=None, ranks=[{"rank": 3, "category": "X"}])],
            marketplace="us")
        cats = {c["category"]: c for c in out["categories"]}
        assert cats["X"]["books_observed"] == 1
        assert cats["X"]["entry_sales_day"] is None
