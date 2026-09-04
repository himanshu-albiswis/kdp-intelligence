"""Review velocity and praise mining.

We mine 1-3 star reviews for gaps and ignore what 5-star reviews reward —
the must-have features a new entrant cannot skip. Velocity (reviews per
month since publication) is the launch-strength signal every tracker sells.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import reviews


TODAY = date(2026, 9, 5)


class TestVelocity:
    def test_reviews_per_month_since_publication(self):
        v = reviews.velocity(reviews=120, publication_date="2025-09-05", today=TODAY)
        assert v["per_month"] == pytest.approx(10.0, rel=0.05)
        assert v["months"] == pytest.approx(12, abs=0.2)

    def test_a_brand_new_book_is_not_divided_by_zero(self):
        v = reviews.velocity(reviews=3, publication_date="2026-09-01", today=TODAY)
        assert v["per_month"] is not None and v["per_month"] > 0

    def test_missing_date_means_unknown_not_zero(self):
        v = reviews.velocity(reviews=50, publication_date=None, today=TODAY)
        assert v["per_month"] is None and v["band"] == "unknown"

    @pytest.mark.parametrize("per_month,band", [(0.2, "stale"), (2, "slow"), (8, "steady"), (40, "surging")])
    def test_bands_describe_launch_strength(self, per_month, band):
        assert reviews.band_for(per_month) == band

    def test_shelf_velocity_is_the_median_of_books_with_dates(self):
        intel = [{"reviews": 120, "publication_date": "2025-09-05"},
                 {"reviews": 12, "publication_date": "2025-09-05"},
                 {"reviews": 999, "publication_date": None}]
        out = reviews.shelf_velocity(intel, today=TODAY)
        assert out["books_measured"] == 2
        assert 1 < out["median_per_month"] < 10


class TestPraiseMining:
    praise = [
        {"rating": 5, "body": "The recipes are so easy to follow and the photos are beautiful"},
        {"rating": 5, "body": "Easy to follow recipes, great photos, perfect for beginners"},
        {"rating": 4, "body": "Loved how easy to follow everything was. Beautiful photos."},
        {"rating": 2, "body": "Recipes are hard to follow and the photos are missing"},
    ]

    def test_only_four_and_five_star_reviews_count_as_praise(self):
        themes = reviews.praise_themes(self.praise)
        assert all(t["mentions"] <= 3 for t in themes), "the 2-star review must not count"

    def test_surfaces_repeated_phrases(self):
        themes = {t["theme"] for t in reviews.praise_themes(self.praise)}
        assert any("easy to follow" in t for t in themes)
        assert any("photos" in t for t in themes)

    def test_each_theme_carries_an_example_quote(self):
        for theme in reviews.praise_themes(self.praise):
            assert theme["example"]

    def test_no_praise_returns_empty_not_crash(self):
        assert reviews.praise_themes([{"rating": 1, "body": "awful"}]) == []

    def test_themes_are_ranked_by_mentions(self):
        themes = reviews.praise_themes(self.praise)
        counts = [t["mentions"] for t in themes]
        assert counts == sorted(counts, reverse=True)


class TestPraiseIgnoresTheNicheOwnWords:
    """A live scan for "air fryer cookbook" returned praise themes of
    'recipes' x35, 'fryer' x22, 'air fryer' x21 — the topic itself, not the
    features buyers reward. Words from the seed are excluded from themes."""

    praise = [
        {"rating": 5, "body": "air fryer recipes that are easy to follow with beautiful photos"},
        {"rating": 5, "body": "The air fryer cookbook has easy to follow recipes and great photos"},
        {"rating": 4, "body": "Air fryer recipes, easy to follow, photos are beautiful"},
    ]

    def test_seed_words_never_become_themes(self):
        themes = {t["theme"] for t in reviews.praise_themes(self.praise, exclude="air fryer cookbook recipes")}
        assert "recipes" not in themes and "air fryer" not in themes and "fryer" not in themes

    def test_real_features_still_surface(self):
        themes = {t["theme"] for t in reviews.praise_themes(self.praise, exclude="air fryer cookbook recipes")}
        assert any("easy to follow" in t for t in themes)
        assert any("photos" in t for t in themes)

    def test_a_phrase_mixing_seed_and_feature_words_keeps_the_feature(self):
        themes = reviews.praise_themes(self.praise, exclude="air fryer")
        assert all(t["theme"] not in ("air", "fryer", "air fryer") for t in themes)


class TestNicheVocabulary:
    """Words that appear across most page-1 titles are the niche's own
    vocabulary ("recipes", "cookbook"), not praise. A live scan still ranked
    'recipes' x36 first after excluding the seed, because the seed was
    "air fryer cookbook" and every title says "recipes"."""

    titles = ["Air Fryer Cookbook: 500 Recipes", "Easy Air Fryer Recipes for Two",
              "The Complete Air Fryer Cookbook", "Air Fryer Recipes Made Simple",
              "Crispy Air Fryer Meals: 75 Recipes"]

    def test_words_in_most_titles_are_vocabulary(self):
        vocab = reviews.niche_vocabulary(self.titles)
        assert {"air", "fryer", "recipes"} <= vocab

    def test_rare_title_words_are_not(self):
        vocab = reviews.niche_vocabulary(self.titles)
        assert "crispy" not in vocab and "complete" not in vocab

    def test_empty_titles_yield_an_empty_set(self):
        assert reviews.niche_vocabulary([]) == set()


class TestPraiseQuality:
    """Live praise after seed exclusion read: 'recipe' x12, 'simple', 'meals',
    'make', 'every', 'most' — a plural/singular leak and filler verbs."""

    praise = [{"rating": 5, "body": f"Great recipe {i}, so easy to follow and quick to make every night"}
              for i in range(6)] + [{"rating": 5, "body": "photos are beautiful, easy to follow"}]

    def test_singular_of_an_excluded_plural_is_excluded_too(self):
        themes = {t["theme"] for t in reviews.praise_themes(self.praise, exclude="recipes")}
        assert "recipe" not in themes

    def test_filler_words_are_not_themes(self):
        themes = {t["theme"] for t in reviews.praise_themes(self.praise)}
        assert not {"every", "most", "make", "night"} & themes

    def test_a_phrase_outranks_a_single_word_of_similar_count(self):
        themes = reviews.praise_themes(self.praise)
        assert themes[0]["theme"] == "easy to follow"
