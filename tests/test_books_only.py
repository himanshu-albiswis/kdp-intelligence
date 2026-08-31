"""Trend Radar must return book niches, not product searches.

A live seedless scan returned "forza horizon 6" and "forever living products"
as GO opportunities, alongside "formal shoes for men". Every one of them
starts with "fo", which gave the cause away: with no topic, the autocomplete
collector was expanding an empty string, so it asked Google to complete the
bare words "for", "book", "how to", "guide" and "vs". Google answered with
its most popular completions — bookmyshow, forex factory, fortuner, formal
shoes for men.

That is not something a junk filter can fix; the harvest itself was
meaningless. Seedless harvesting now runs over the category panel, and a
books-only guard rejects the product searches that still slip through.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import trends
from server.sources import autocomplete, base


class FakeResponse:
    def __init__(self, status=200, body='["x",["something"]]'):
        self.status = status
        self.body = body


class TestAutocompleteRefusesAnEmptyTopic:
    """Asking Google to complete "for" is not a demand signal."""

    def test_an_empty_topic_is_refused_rather_than_queried(self):
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            return FakeResponse()

        result = autocomplete.google("", fetch=fetch)
        assert result.status != "ok"
        assert calls == [], "no request should be made for an empty topic"

    def test_a_whitespace_topic_is_also_refused(self):
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            return FakeResponse()

        autocomplete.google("   ", fetch=fetch)
        assert calls == []

    def test_the_refusal_explains_itself(self):
        assert "topic" in autocomplete.google("", fetch=lambda *a, **k: FakeResponse()).detail.lower()

    def test_a_real_topic_still_queries(self):
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            return FakeResponse()

        autocomplete.google("air fryer", fetch=fetch)
        assert calls, "a real topic must still be queried"


class TestBooksOnlyGuard:
    """Phrases that describe a physical product, brand, game or app."""

    product_searches = [
        "formal shoes for men", "forza horizon 6", "forever living products",
        "fortuner price", "samsung galaxy s24 case", "best running shoes",
        "iphone 17 pro max", "sony wh-1000xm5", "office chair under 10000",
    ]

    book_niches = [
        "air fryer cookbook for beginners", "adhd for beginners",
        "how to meal prep on a budget", "beginner yoga for back pain",
        "anti-inflammatory recipes for two", "learn python for data analysis",
    ]

    @pytest.mark.parametrize("phrase", product_searches)
    def test_product_searches_are_rejected(self, phrase):
        assert trends.is_book_shaped(phrase) is False, f"{phrase!r} is not a book niche"

    @pytest.mark.parametrize("phrase", book_niches)
    def test_book_niches_are_kept(self, phrase):
        assert trends.is_book_shaped(phrase) is True, f"{phrase!r} should have survived"

    def test_the_guard_runs_during_seedless_mining(self):
        sources = {"google": base.ok("google",
                   [{"phrase": p, "position": i} for i, p in enumerate(
                       ["formal shoes for men", "air fryer cookbook for beginners"])])}
        phrases = {c["phrase"] for c in trends._mine_candidates("", sources)}
        assert "air fryer cookbook for beginners" in phrases
        assert "formal shoes for men" not in phrases

    def test_an_explicit_topic_still_bypasses_the_guard(self):
        # the user asking about shoes has asserted relevance
        sources = {"google": base.ok("google", [{"phrase": "formal shoes for men", "position": 0}])}
        assert trends._mine_candidates("shoes", sources)


class TestSeedlessHarvestUsesTheCategoryPanel:
    def test_seedless_harvest_asks_for_real_seeds_not_an_empty_string(self):
        asked = []

        def spy_collect(topic, **kw):
            asked.append(topic)
            return {}

        trends._harvest_sources("", spy_collect)
        assert "" not in asked, "an empty topic must never reach the collectors"
        assert asked, "seedless mode must still harvest something"

    def test_seedless_harvest_covers_several_categories(self):
        asked = []

        def spy_collect(topic, **kw):
            asked.append(topic)
            return {}

        trends._harvest_sources("", spy_collect)
        assert len(set(asked)) >= 3, f"only harvested {asked}"

    def test_a_seeded_harvest_asks_for_exactly_that_topic(self):
        asked = []

        def spy_collect(topic, **kw):
            asked.append(topic)
            return {}

        trends._harvest_sources("air fryer", spy_collect)
        assert asked == ["air fryer"]


class TestMiningReadsEveryHarvestedSource:
    """Seedless harvesting returns one entry per category seed, not one per source.

    _harvest_sources keys results as "google:health:adhd" so several seeds can
    contribute. _mine_candidates looked those up as sources["google"] exactly,
    found nothing, and a live seedless scan mined zero candidates — the filters
    were blamed, but the lookup was the fault.
    """

    def _panel(self):
        return {
            "google:health:adhd": base.ok(
                "google", [{"phrase": "adhd symptoms in adults", "position": 0}]),
            "google:cooking:meal prep": base.ok(
                "google", [{"phrase": "meal prep for the week", "position": 0}]),
            "youtube:health:anxiety": base.ok(
                "youtube", [{"phrase": "how to calm anxiety fast", "position": 0}]),
        }

    def test_candidates_come_from_every_keyed_source(self):
        phrases = {c["phrase"] for c in trends._mine_candidates("", self._panel())}
        assert "adhd symptoms in adults" in phrases
        assert "meal prep for the week" in phrases
        assert "how to calm anxiety fast" in phrases

    def test_the_source_label_is_the_engine_not_the_key(self):
        found = trends._mine_candidates("", self._panel())
        labels = {s for c in found for s in c["sources"]}
        assert labels <= {"google", "youtube"}, f"leaked keys into source labels: {labels}"

    def test_breadth_counts_distinct_engines_not_seeds(self):
        panel = {
            "google:health:adhd": base.ok("google", [{"phrase": "adhd focus tips", "position": 0}]),
            "google:health:focus": base.ok("google", [{"phrase": "adhd focus tips", "position": 0}]),
        }
        found = trends._mine_candidates("", panel)
        assert found[0]["breadth"] == 1, "the same engine twice is not corroboration"

    def test_the_plain_single_source_shape_still_works(self):
        simple = {"google": base.ok("google", [{"phrase": "adhd focus tips", "position": 0}])}
        assert trends._mine_candidates("", simple)
