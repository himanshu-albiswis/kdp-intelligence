"""KDP metadata guidelines checker — catch the rejection before Amazon does.

Self Publishing Titans sells this standalone. The rules are KDP's published
metadata guidelines: length limits, prohibited promotional and time-
sensitive words, keyword slot rules, description HTML subset, category cap.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import guidelines


def listing(**over):
    base = {
        "title": "Air Fryer Cookbook for Beginners",
        "subtitle": "500 Simple Recipes for Busy Weeknights",
        "author": "Rosemary King",
        "description": "<p>Crispy meals, <b>fast</b>.</p>" + " Great recipes." * 20,
        "keywords": ["air fryer recipes", "healthy air fryer", "quick dinners",
                     "air fryer for two", "weeknight meals", "family cooking", "crispy"],
        "categories": ["Cooking > Air Fryer", "Cooking > Quick & Easy"],
    }
    base.update(over)
    return base


def issues_for(field=None, **over):
    out = guidelines.check_listing(listing(**over))
    return [i for i in out["issues"] if field is None or i["field"] == field]


class TestCleanListingPasses:
    def test_a_clean_listing_passes_with_a_high_score(self):
        out = guidelines.check_listing(listing())
        assert out["passed"] is True
        assert out["score"] >= 90
        assert not [i for i in out["issues"] if i["severity"] == "error"]


class TestTitleRules:
    def test_title_is_required(self):
        assert any(i["severity"] == "error" for i in issues_for("title", title=""))

    def test_title_over_200_chars_is_an_error(self):
        assert any(i["severity"] == "error" for i in issues_for("title", title="x" * 201))

    @pytest.mark.parametrize("word", ["free", "bestseller", "best seller", "#1", "on sale", "kindle unlimited"])
    def test_promotional_claims_in_the_title_are_errors(self, word):
        hits = issues_for("title", title=f"The {word} Air Fryer Cookbook")
        assert any(i["severity"] == "error" for i in hits), word

    @pytest.mark.parametrize("word", ["new", "today", "2026 edition"])
    def test_time_sensitive_words_are_warned(self, word):
        assert issues_for("title", title=f"The {word} Air Fryer Cookbook")

    def test_html_in_the_title_is_an_error(self):
        assert any(i["severity"] == "error" for i in issues_for("title", title="<b>Air</b> Fryer"))


class TestKeywordRules:
    def test_more_than_seven_slots_is_an_error(self):
        hits = issues_for("keywords", keywords=["k%d" % i for i in range(8)])
        assert any(i["severity"] == "error" for i in hits)

    def test_a_slot_over_50_chars_is_an_error(self):
        hits = issues_for("keywords", keywords=["x" * 51])
        assert any(i["severity"] == "error" for i in hits)

    def test_quotation_marks_in_a_slot_are_an_error(self):
        assert any(i["severity"] == "error" for i in issues_for("keywords", keywords=['"air fryer"']))

    def test_duplicate_slots_are_warned(self):
        assert issues_for("keywords", keywords=["air fryer", "air fryer"])

    def test_repeating_title_words_wastes_a_slot(self):
        hits = issues_for("keywords", keywords=["air fryer cookbook"])
        assert any("title" in i["message"].lower() for i in hits)

    def test_empty_slots_are_warned_as_unused(self):
        hits = issues_for("keywords", keywords=["air fryer recipes"])
        assert any("unused" in i["message"].lower() or "slot" in i["message"].lower() for i in hits)


class TestDescriptionRules:
    def test_over_4000_chars_is_an_error(self):
        assert any(i["severity"] == "error" for i in issues_for("description", description="x" * 4001))

    def test_disallowed_html_is_an_error(self):
        hits = issues_for("description", description='<a href="x">link</a> ' * 30)
        assert any(i["severity"] == "error" for i in hits)

    def test_allowed_html_passes(self):
        assert not [i for i in issues_for("description",
                                          description="<h2>Hi</h2><p><b>bold</b> <i>it</i></p><ul><li>a</li></ul>" * 10)
                    if i["severity"] == "error"]

    def test_a_thin_description_is_warned(self):
        assert issues_for("description", description="Short.")


class TestCategoryRules:
    def test_more_than_three_categories_is_an_error(self):
        hits = issues_for("categories", categories=["a", "b", "c", "d"])
        assert any(i["severity"] == "error" for i in hits)

    def test_no_categories_is_warned(self):
        assert issues_for("categories", categories=[])


class TestScoring:
    def test_errors_cost_more_than_warnings(self):
        err = guidelines.check_listing(listing(title="x" * 201))["score"]
        warn = guidelines.check_listing(listing(categories=[]))["score"]
        assert err < warn < 100

    def test_score_never_goes_below_zero(self):
        awful = listing(title="", subtitle="x" * 300, description="<script>x</script>",
                        keywords=['"a"'] * 9, categories=list("abcdef"))
        assert guidelines.check_listing(awful)["score"] >= 0
