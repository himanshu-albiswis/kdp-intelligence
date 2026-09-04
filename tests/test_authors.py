"""Author tracker — who owns this shelf, and how prolific are they.

Fixture cut from amazon.com/Rosemary-King/e/B084WP5KB7 on 2026-09-05. The
catalog is an embedded JSON of book records; titles ride in the tile links'
aria-label. Publication dates are not on the page, so cadence is derived
only from dates we already know from deep-dives, never invented.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import authors

AUTHOR_HTML = (
    '<title>Amazon.com: Rosemary King: books, biography, latest update</title>'
    '<a href="/Slow-Cooker-Cookbook-Recipes-Everyday-ebook/dp/B08PCW42YK?ref_=ast_author_mpb" '
    'aria-label="Slow Cooker Cookbook: 500 Simple and Tasty Recipes for Everyday Cooking"></a>'
    '{"customerReviewsSummary":{"rating":{"value":4.8},"count":{"value":491}},'
    '"detailPageLinkURL":"/Air-Fryer-Cookbook-Recipes-Beginners-ebook/dp/B08JCQKGXZ","title":"Air Fryer Cookbook: 500 Simple Air Fryer Recipes for Beginners"}'
    '{"customerReviewsSummary":{"rating":{"value":4.6},"count":{"value":120}},'
    '"detailPageLinkURL":"/Slow-Cooker-Cookbook-Recipes-Everyday-ebook/dp/B08PCW42YK","title":"Slow Cooker Cookbook: 500 Simple and Tasty Recipes for Everyday Cooking"}'
    '{"customerReviewsSummary":{"rating":{"value":4.5},"count":{"value":40}},'
    '"detailPageLinkURL":"/Keto-Diet-Book-2-ebook/dp/B08CL1P97Y","title":"Keto Diet for Beginners (Healthy Living Book 2)"}'
)


class TestParseAuthorPage:
    def test_reads_the_author_name_from_the_title(self):
        assert authors.parse_author_page(AUTHOR_HTML)["name"] == "Rosemary King"

    def test_builds_the_catalog_from_the_embedded_records(self):
        catalog = authors.parse_author_page(AUTHOR_HTML)["catalog"]
        assert {b["asin"] for b in catalog} == {"B08JCQKGXZ", "B08PCW42YK", "B08CL1P97Y"}

    def test_each_book_carries_title_rating_and_reviews(self):
        by = {b["asin"]: b for b in authors.parse_author_page(AUTHOR_HTML)["catalog"]}
        assert by["B08JCQKGXZ"]["title"].startswith("Air Fryer Cookbook")
        assert by["B08JCQKGXZ"]["rating"] == 4.8
        assert by["B08JCQKGXZ"]["reviews"] == 491

    def test_a_title_is_recovered_from_the_slug_when_no_record_has_it(self):
        html = '<a href="/Vegan-Baking-Made-Easy-ebook/dp/B0TEST00001"></a>'
        cat = authors.parse_author_page(html)["catalog"]
        assert cat and "Vegan Baking Made Easy" in cat[0]["title"]

    def test_bio_is_none_when_the_page_has_none(self):
        assert authors.parse_author_page(AUTHOR_HTML)["bio"] is None

    def test_empty_page_yields_an_empty_catalog(self):
        assert authors.parse_author_page("")["catalog"] == []


class TestProfile:
    def test_counts_catalog_and_series_and_sums_reviews(self):
        page = authors.parse_author_page(AUTHOR_HTML)
        profile = authors.profile(page)
        assert profile["catalog_size"] == 3
        assert profile["total_reviews"] == 651
        assert profile["series_titles"] == 1

    def test_cadence_uses_known_dates_only(self):
        page = authors.parse_author_page(AUTHOR_HTML)
        known = {"B08JCQKGXZ": "2020-09-16", "B08PCW42YK": "2021-03-01", "B08CL1P97Y": "2021-09-01"}
        profile = authors.profile(page, known_dates=known)
        assert profile["cadence"]["books_dated"] == 3
        assert profile["cadence"]["months_between"] == pytest.approx(5.8, abs=0.5)

    def test_cadence_is_unknown_without_two_dates(self):
        page = authors.parse_author_page(AUTHOR_HTML)
        assert authors.profile(page, known_dates={"B08JCQKGXZ": "2020-09-16"})["cadence"]["months_between"] is None

    def test_reads_as_a_sentence_a_client_can_act_on(self):
        read = authors.profile(authors.parse_author_page(AUTHOR_HTML))["read"]
        assert "3" in read and "book" in read.lower()
