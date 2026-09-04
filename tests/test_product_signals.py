"""Product-page signals: listing quality, on-page praise, also-viewed graph.

Fixtures are cut from amazon.com/dp/B08JCQKGXZ as served on 2026-09-05:
  * the also-viewed carousel ships its ASINs inside a JSON blob in
    data-a-carousel-options -> ajax.id_list (each entry itself JSON-encoded)
  * series membership renders as "Book 1 of 1: <title>"
  * A+ content is an aplus_feature_div that may be present yet EMPTY — the
    div existed with zero modules on this page, so presence != content
  * the paginated 5-star review pages are login-walled for anonymous
    visitors (200, zero cards), but the product page itself renders ~13
    reviews with ratings, 12 of them 4-5 stars
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import product_signals as ps

CAROUSEL = (
    '<div data-a-carousel-options="{&quot;ajax&quot;:{&quot;id_list&quot;:['
    '&quot;{\\&quot;id\\&quot;:\\&quot;B0BNJQPG1K\\&quot;,\\&quot;linkParameters\\&quot;:{}}&quot;,'
    '&quot;{\\&quot;id\\&quot;:\\&quot;B08PCW42YK\\&quot;}&quot;,'
    '&quot;{\\&quot;id\\&quot;:\\&quot;B08MHC4SBS\\&quot;}&quot;],'
    '&quot;name&quot;:&quot;p13n-sc-shoveler_8n7khy0ycsr&quot;},&quot;set_size&quot;:59}">'
    '<h2 class="a-carousel-heading">Customers who viewed this item also viewed</h2></div>'
)

PAGE = (
    '<div id="bookDescription_feature_div"><div><span>' + ("Crispy meals fast. " * 90) +
    '</span></div></div>'
    '<div id="aplus_feature_div"></div>'
    '<span>Book 1 of 1: Air Fryer Cookbook</span>'
    '<div class="litb-canvas"></div>'
    '"hiRes":"https://m.media-amazon.com/images/I/cover.jpg"'
    + CAROUSEL +
    '<div data-hook="review"><i data-hook="review-star-rating"><span>5.0 out of 5 stars</span></i>'
    '<span data-hook="review-body"><span>Easy to follow and the photos are beautiful</span></span></div>'
    '<div data-hook="review"><i data-hook="review-star-rating"><span>2.0 out of 5 stars</span></i>'
    '<span data-hook="review-body"><span>Recipes are hard to follow</span></span></div>'
    '<div data-hook="review"><i data-hook="cmps-review-star-rating"><span>4.0 out of 5 stars</span></i>'
    '<div data-hook="reviewRichContentContainer">Great photos, easy to follow</div></div>'
)


class TestAlsoViewed:
    def test_extracts_every_asin_from_the_carousel_json(self):
        assert ps.also_viewed_asins(PAGE) == ["B0BNJQPG1K", "B08PCW42YK", "B08MHC4SBS"]

    def test_excludes_the_page_own_asin(self):
        html = PAGE.replace("B08MHC4SBS", "B08JCQKGXZ")
        assert "B08JCQKGXZ" not in ps.also_viewed_asins(html, own_asin="B08JCQKGXZ")

    def test_no_carousel_yields_an_empty_list(self):
        assert ps.also_viewed_asins("<html></html>") == []

    def test_malformed_carousel_json_does_not_raise(self):
        assert ps.also_viewed_asins('<div data-a-carousel-options="{not json">') == []


class TestOnPageReviews:
    def test_reads_rating_and_body_from_both_hook_styles(self):
        found = ps.onpage_reviews(PAGE)
        assert len(found) == 3
        assert {r["rating"] for r in found} == {5.0, 2.0, 4.0}

    def test_bodies_are_plain_text(self):
        bodies = [r["body"] for r in ps.onpage_reviews(PAGE)]
        assert "Easy to follow and the photos are beautiful" in bodies
        assert all("<" not in b for b in bodies)


class TestListingQuality:
    def test_reads_every_signal(self):
        q = ps.listing_quality(PAGE)
        assert q["description_chars"] > 1000
        assert q["series"] == {"book": 1, "of": 1, "name": "Air Fryer Cookbook"}
        assert q["look_inside"] is True
        assert q["images"] >= 1

    def test_an_empty_aplus_div_is_not_a_plus_content(self):
        assert ps.listing_quality(PAGE)["aplus"] is False

    def test_aplus_modules_count_as_a_plus_content(self):
        html = PAGE.replace('<div id="aplus_feature_div"></div>',
                            '<div id="aplus_feature_div"><div class="aplus-module aplus-v2">x</div></div>')
        assert ps.listing_quality(html)["aplus"] is True

    def test_no_series_is_none_not_a_crash(self):
        html = PAGE.replace("Book 1 of 1: Air Fryer Cookbook", "")
        assert ps.listing_quality(html)["series"] is None

    def test_score_rewards_a_polished_listing(self):
        thin = ps.listing_quality("<html></html>")["score"]
        rich = ps.listing_quality(PAGE.replace('<div id="aplus_feature_div"></div>',
                                               '<div class="aplus-module">x</div>'))["score"]
        assert 0 <= thin < rich <= 100

    def test_shelf_benchmark_averages_and_reports_a_plus_share(self):
        books = [{"quality": {"score": 80, "aplus": True, "description_chars": 1500,
                              "look_inside": True, "series": None}},
                 {"quality": {"score": 40, "aplus": False, "description_chars": 300,
                              "look_inside": False, "series": None}}]
        bench = ps.shelf_benchmark(books)
        assert bench["avg_score"] == 60
        assert bench["aplus_share_pct"] == 50
        assert "books" in bench["read"].lower()
