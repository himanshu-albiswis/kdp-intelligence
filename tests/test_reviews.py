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
