"""Tests for reverse-ASIN teardown: one competitor book -> one structured row.

Fixtures are taken from the real amazon.com/dp/B08JCQKGXZ page on
2026-08-31, including the details that defeated naive patterns:

  * "Best Sellers Rank: #33,125 in Kindle Store" — the colon and the hash
    sit between the label and the number.
  * "Publication date September 16, 2020" — only visible once tags are
    stripped; the raw HTML interleaves markup through the label.
  * A Kindle-only title has no ISBN at all, so a blank there is a fact
    about the book, not a parse failure.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import teardown

PAGE_TEXT = (
    "Air Fryer Cookbook: 500 Simple Air Fryer Recipes for Beginners "
    "Rosemary King 4.5 out of 5 stars 62 ratings "
    "Print length 534 pages Publication date September 16, 2020 "
    "File size 4.4 MB Language English "
    "Best Sellers Rank: #33,125 in Kindle Store ( See Top 100 in Kindle Store ) "
    "#2 in Latin American Cooking #5 in Latin American Cooking, Food & Wine"
)

PAGE_HTML = (
    '<span id="productTitle">Air Fryer Cookbook: 500 Simple Air Fryer Recipes '
    'for Beginners   </span>'
    '<a class="a-link-normal" href="/Rosemary-King/e/B084WP5KB7/ref=x">Rosemary King</a>'
    '<span>Kindle</span><span>Paperback</span>'
)

PRINT_TEXT = PAGE_TEXT + " ISBN-13 : 978-1-234567-89-0 ISBN-10 : 1234567890"


class TestIdentifierParsing:
    @pytest.mark.parametrize("token,expected", [
        ("B08JCQKGXZ", "B08JCQKGXZ"),
        ("  b08jcqkgxz  ", "B08JCQKGXZ"),
        ("https://www.amazon.com/dp/B08JCQKGXZ", "B08JCQKGXZ"),
        ("https://www.amazon.com/Air-Fryer-Cookbook/dp/B08JCQKGXZ/ref=sr_1_1?x=y", "B08JCQKGXZ"),
        ("https://www.amazon.co.uk/gp/product/B08JCQKGXZ", "B08JCQKGXZ"),
    ])
    def test_extracts_the_asin(self, token, expected):
        assert teardown.parse_asin(token) == expected

    @pytest.mark.parametrize("token", ["", "   ", "not-an-asin", "https://example.com/x"])
    def test_rejects_what_is_not_an_asin(self, token):
        assert teardown.parse_asin(token) is None

    def test_reads_a_mixed_paste_of_asins_and_links(self):
        pasted = """
        B08JCQKGXZ
        https://www.amazon.com/dp/B0CTFW6JLD
        garbage line
        B0FFNQ7W9J, B08JCQKGXZ
        """
        assert teardown.parse_identifiers(pasted) == ["B08JCQKGXZ", "B0CTFW6JLD", "B0FFNQ7W9J"]

    def test_deduplicates_while_preserving_order(self):
        assert teardown.parse_identifiers("B08JCQKGXZ B08JCQKGXZ") == ["B08JCQKGXZ"]


class TestPageParsing:
    def test_splits_title_from_subtitle(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert row["title"] == "Air Fryer Cookbook"
        assert row["subtitle"] == "500 Simple Air Fryer Recipes for Beginners"

    def test_subtitle_is_kept_short(self):
        long_sub = PAGE_TEXT.replace("500 Simple", "x" * 200 + " 500 Simple")
        html = PAGE_HTML.replace("500 Simple", "x" * 200 + " 500 Simple")
        row = teardown.parse_book(html, long_sub, "B08JCQKGXZ")
        assert len(row["subtitle"]) <= 120

    def test_a_title_without_a_colon_has_no_subtitle(self):
        row = teardown.parse_book('<span id="productTitle">Just A Title</span>',
                                  "Just A Title", "B0TEST00001")
        assert row["title"] == "Just A Title"
        assert row["subtitle"] is None

    def test_reads_the_author(self):
        assert teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")["author"] == "Rosemary King"

    def test_reads_rating_and_review_count(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert row["rating"] == 4.5
        assert row["reviews"] == 62

    def test_reads_pages_and_publication_year(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert row["pages"] == 534
        assert row["year"] == 2020

    def test_reads_the_store_wide_bsr_not_a_category_rank(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert row["bsr"] == 33125, "must not pick up the #2 category rank"

    def test_reads_the_first_category_rank(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert "Latin American Cooking" in (row["category_rank"] or "")

    def test_reads_available_formats(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert "Kindle" in row["formats"] and "Paperback" in row["formats"]

    def test_a_kindle_only_book_reports_no_isbn(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert row["paperback_isbn"] is None, "absent ISBN is a fact, not a failure"

    def test_reads_an_isbn_when_the_book_has_one(self):
        row = teardown.parse_book(PAGE_HTML, PRINT_TEXT, "B08JCQKGXZ")
        assert row["paperback_isbn"] == "978-1-234567-89-0"

    def test_carries_the_source_url(self):
        row = teardown.parse_book(PAGE_HTML, PAGE_TEXT, "B08JCQKGXZ")
        assert row["source_url"] == "https://www.amazon.com/dp/B08JCQKGXZ"

    def test_an_unparseable_page_yields_nulls_not_guesses(self):
        row = teardown.parse_book("<html></html>", "nothing useful here", "B0TEST00001")
        assert row["title"] is None and row["bsr"] is None and row["rating"] is None


class TestBsrStatus:
    def test_a_read_rank_reports_when_and_how_confident(self):
        status = teardown.bsr_status(33125, observed_at="2026-08-31")
        assert "2026-08-31" in status
        assert "confidence" in status.lower()

    def test_a_missing_rank_says_why_rather_than_leaving_a_blank(self):
        status = teardown.bsr_status(None, observed_at="2026-08-31")
        assert status
        assert "unavailable" in status.lower() or "not ranked" in status.lower()

    def test_a_deep_rank_is_lower_confidence_than_a_shallow_one(self):
        assert "low" in teardown.bsr_status(900_000, observed_at="2026-08-31").lower()
        assert "medium" in teardown.bsr_status(20_000, observed_at="2026-08-31").lower()


PROGRESS = lambda stage, pct, msg: None


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self.body = body


def page_for(asin, title="Air Fryer Cookbook: 500 Recipes for Beginners"):
    html = (f'<span id="productTitle">{title}</span>'
            f'<a class="a-link-normal" href="/Rosemary-King/e/B084WP5KB7/x">Rosemary King</a>'
            f'<span>Kindle</span>')
    text = (f"{title} Rosemary King 4.5 out of 5 stars 62 ratings "
            f"Print length 534 pages Publication date September 16, 2020 "
            f"Best Sellers Rank: #33,125 in Kindle Store #2 in Cooking")
    return FakeResponse(200, html + " " + text)


def fetch_ok(url, **kw):
    return page_for(teardown.parse_asin(url) or "B0TEST00001")


def validator_thin(phrases):
    return {p: {"total_results": 140, "median_reviews": 7, "phrase_in_titles": 1,
                "url": "https://amazon.com/s"} for p in phrases}


class TestTeardownRows:
    def test_returns_one_row_per_asin_in_the_order_given(self):
        out = teardown.run_teardown(
            {"identifiers": "B08JCQKGXZ B0CTFW6JLD"}, PROGRESS,
            fetch=fetch_ok, llm=None, validator=validator_thin)
        assert [r["asin"] for r in out["rows"]] == ["B08JCQKGXZ", "B0CTFW6JLD"]

    def test_every_column_the_brief_asked_for_is_present(self):
        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=fetch_ok, llm=None, validator=validator_thin)
        row = out["rows"][0]
        for column in ("subtitle", "author", "credential", "sub_niche", "formats",
                       "asin", "paperback_isbn", "year", "pages", "bsr",
                       "bsr_status", "reviews", "rating", "positioning",
                       "crowdedness", "source_url"):
            assert column in row, f"missing column: {column}"

    def test_a_blocked_page_yields_a_row_that_says_so(self):
        def blocked(url, **kw):
            return FakeResponse(503, "")

        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=blocked, llm=None, validator=validator_thin)
        row = out["rows"][0]
        assert row["error"], "a blocked page must be reported, not silently blank"
        assert row["title"] is None

    def test_a_transport_exception_does_not_lose_the_other_rows(self):
        def flaky(url, **kw):
            if "B0CTFW6JLD" in url:
                raise RuntimeError("connection reset")
            return page_for("B08JCQKGXZ")

        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ B0CTFW6JLD"}, PROGRESS,
                                    fetch=flaky, llm=None, validator=validator_thin)
        assert len(out["rows"]) == 2
        assert out["rows"][0]["title"] and out["rows"][1]["error"]

    def test_no_identifiers_is_reported_rather_than_crashing(self):
        out = teardown.run_teardown({"identifiers": "nonsense"}, PROGRESS,
                                    fetch=fetch_ok, llm=None, validator=validator_thin)
        assert out["rows"] == []
        assert out["warnings"]


class TestSoftFields:
    def test_without_a_model_the_soft_fields_stay_empty(self):
        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=fetch_ok, llm=None, validator=validator_thin)
        row = out["rows"][0]
        assert row["credential"] is None
        assert row["positioning"] is None
        assert any("GEMINI_API_KEY" in w or "no model" in w.lower() for w in out["warnings"])

    def test_the_model_fills_credential_subniche_and_positioning(self):
        def llm(prompt):
            return ('[{"asin":"B08JCQKGXZ","credential":"Home cook, 500-recipe author",'
                    '"sub_niche":"air fryer cookbooks for beginners",'
                    '"positioning":"Volume play: 500 recipes at a low price."}]')

        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=fetch_ok, llm=llm, validator=validator_thin)
        row = out["rows"][0]
        assert row["credential"] == "Home cook, 500-recipe author"
        assert row["sub_niche"] == "air fryer cookbooks for beginners"
        assert row["positioning"].startswith("Volume play")

    def test_a_model_row_for_an_asin_we_never_asked_about_is_ignored(self):
        def llm(prompt):
            return '[{"asin":"B0NOTREAL1","credential":"invented","sub_niche":"x","positioning":"y"}]'

        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=fetch_ok, llm=llm, validator=validator_thin)
        assert out["rows"][0]["credential"] is None

    def test_an_unparseable_model_reply_leaves_the_hard_fields_intact(self):
        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=fetch_ok, llm=lambda p: "sorry, no",
                                    validator=validator_thin)
        row = out["rows"][0]
        assert row["bsr"] == 33125 and row["rating"] == 4.5
        assert row["credential"] is None


class TestCrowdedness:
    def test_the_verdict_comes_from_a_live_search_of_the_sub_niche(self):
        asked = []

        def spy(phrases):
            asked.extend(phrases)
            return validator_thin(phrases)

        def llm(prompt):
            return ('[{"asin":"B08JCQKGXZ","credential":"c",'
                    '"sub_niche":"air fryer cookbooks for beginners","positioning":"p"}]')

        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=fetch_ok, llm=llm, validator=spy)
        assert "air fryer cookbooks for beginners" in asked
        assert out["rows"][0]["crowdedness"]["verdict"]

    def test_a_thin_shelf_reads_as_less_crowded_than_a_deep_one(self):
        def crowded(phrases):
            return {p: {"total_results": 80_000, "median_reviews": 5_000,
                        "phrase_in_titles": 9, "url": "u"} for p in phrases}

        thin = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                     fetch=fetch_ok, llm=None, validator=validator_thin)
        deep = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                     fetch=fetch_ok, llm=None, validator=crowded)
        assert thin["rows"][0]["crowdedness"]["score"] > deep["rows"][0]["crowdedness"]["score"]

    def test_falls_back_to_the_title_when_no_sub_niche_was_inferred(self):
        asked = []

        def spy(phrases):
            asked.extend(phrases)
            return validator_thin(phrases)

        teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                              fetch=fetch_ok, llm=None, validator=spy)
        assert asked, "something must be searched even with no model"

    def test_a_failed_amazon_check_says_verify_rather_than_guessing(self):
        out = teardown.run_teardown({"identifiers": "B08JCQKGXZ"}, PROGRESS,
                                    fetch=fetch_ok, llm=None, validator=lambda p: {})
        assert "VERIFY" in out["rows"][0]["crowdedness"]["verdict"]


class TestLiveDefectsFound:
    """Three defects a live run exposed that the fixtures did not."""

    def test_reads_global_ratings_wording(self):
        # the real page says "4.8 out of 5 491 global ratings"; the fixture said
        # "62 ratings", which came from a related-products carousel
        text = "Air Fryer Cookbook 4.8 out of 5 stars 4.8 out of 5 491 global ratings 5 star"
        row = teardown.parse_book('<span id="productTitle">Air Fryer Cookbook</span>',
                                  text, "B08JCQKGXZ")
        assert row["reviews"] == 491

    def test_still_reads_the_plain_ratings_wording(self):
        row = teardown.parse_book('<span id="productTitle">X</span>',
                                  "X 4.5 out of 5 stars 62 ratings", "B0TEST00001")
        assert row["reviews"] == 62

    def test_html_entities_are_decoded_in_the_title(self):
        html = '<span id="productTitle">Crispy &amp; Easy: Meals That Heal &#39;Fast&#39;</span>'
        row = teardown.parse_book(html, "x", "B0TEST00001")
        assert "&amp;" not in row["title"]
        assert "&" in row["title"]
        assert "&#39;" not in (row["subtitle"] or "")


class TestCrowdednessSpeaksAboutTheShelf:
    """The verdict must not borrow Discovery's demand vocabulary.

    A live teardown returned "GOLDMINE — strong multi-source demand, thin
    shelf" for a single book. No demand was measured at all: crowdedness
    passes a fixed reference value so verdicts stay comparable. Claiming
    multi-source demand there is simply false.
    """

    def test_the_verdict_never_claims_demand_was_measured(self):
        for total, reviews in ((120, 5), (6_000, 70), (80_000, 5_000)):
            verdict = teardown.crowdedness_verdict(
                {"total_results": total, "median_reviews": reviews})["verdict"]
            assert "demand" not in verdict.lower(), verdict
            assert "GOLDMINE" not in verdict

    def test_a_thin_shelf_with_a_weak_moat_reads_as_open(self):
        v = teardown.crowdedness_verdict({"total_results": 258, "median_reviews": 4})
        assert v["verdict"].startswith("OPEN")

    def test_a_deep_shelf_reads_as_crowded(self):
        v = teardown.crowdedness_verdict({"total_results": 80_000, "median_reviews": 5_000})
        assert v["verdict"].startswith("CROWDED")

    def test_the_middle_is_contested(self):
        v = teardown.crowdedness_verdict({"total_results": 6_000, "median_reviews": 70})
        assert v["verdict"].startswith("CONTESTED")

    def test_missing_amazon_data_says_verify(self):
        v = teardown.crowdedness_verdict({})
        assert v["verdict"].startswith("VERIFY")
        assert v["score"] is None

    def test_the_verdict_explains_what_it_looked_at(self):
        v = teardown.crowdedness_verdict({"total_results": 258, "median_reviews": 4})
        assert "258" in v["basis"] and "review" in v["basis"].lower()
