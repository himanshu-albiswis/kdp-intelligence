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


class TestPicksPreferCategoriesTheNicheActuallyLivesIn:
    """A live scan for an air-fryer niche recommended "Juicer Recipes": one
    deep-tail book sat there at rank #40, making the badge look cheap. A
    category a single book wandered into is not where the niche lives."""

    def _intel(self):
        return categories.category_intel([
            book("B1", bsr=30_000, ranks=[{"rank": 3, "category": "Air Fryer Recipes"}]),
            book("B2", bsr=45_000, ranks=[{"rank": 6, "category": "Air Fryer Recipes"}]),
            book("B3", bsr=50_000, ranks=[{"rank": 8, "category": "Air Fryer Recipes"}]),
            book("B4", bsr=900_000, ranks=[{"rank": 40, "category": "Juicer Recipes"}]),
        ], marketplace="us")

    def test_a_category_seen_once_does_not_outrank_one_seen_thrice(self):
        picks = [p["category"] for p in self._intel()["picks"]]
        assert picks[0] == "Air Fryer Recipes"

    def test_singletons_are_still_listed_when_nothing_else_exists(self):
        out = categories.category_intel(
            [book("B4", bsr=900_000, ranks=[{"rank": 40, "category": "Juicer Recipes"}])],
            marketplace="us")
        assert out["picks"][0]["category"] == "Juicer Recipes"

    def test_picks_explain_presence(self):
        assert "3 niche book" in self._intel()["picks"][0]["why"]


class TestRealDetailTextTerminators:
    """Amazon's detail text runs straight into the next section. Captured
    live: '#26 in Fryer Recipes Customer Reviews: 4.8 4.8 out of 5 stars'.
    The dashboard's copy of the regex leaked 'Customer Reviews' into the
    category name; this module's version dropped the entry entirely."""

    DETAIL = ("Best Sellers Rank: #32,606 in Kindle Store ( See Top 100 in Kindle Store ) "
              "#2 in Latin American Cooking #5 in Latin American Cooking, Food & Wine "
              "#26 in Fryer Recipes Customer Reviews: 4.8 4.8 out of 5 stars (491)")

    def test_the_last_category_is_kept_and_clean(self):
        names = [r["category"] for r in categories.extract_category_ranks(self.DETAIL)]
        assert "Fryer Recipes" in names
        assert not any("Customer Reviews" in n for n in names)

    def test_all_three_shelves_are_read(self):
        assert len(categories.extract_category_ranks(self.DETAIL)) == 3

    def test_the_dashboard_uses_the_same_parser(self):
        # The dashboard imports the flat module while tests import server.*,
        # so identity differs; origin and behaviour are what matter.
        import kdp_intel_dashboard as dash
        assert dash.extract_category_ranks.__module__.endswith("categories")
        assert (dash.extract_category_ranks(self.DETAIL)
                == categories.extract_category_ranks(self.DETAIL))
