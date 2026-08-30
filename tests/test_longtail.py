"""Tests for long-tail mining, including the broadening fallback.

Measured against Amazon's autocomplete on 2026-08-31:

    prefix "air fryer"           -> 12 usable candidates
    prefix "adhd"                -> 12 usable candidates
    prefix "adhd for beginners"  -> 1 suggestion, the phrase itself
    prefix "adhd for beginners b/w/…" -> 0 suggestions

A seed that is already a long-tail phrase has nothing beneath it to mine, so
the run produced a keyword table with a single row and no explanation — it
read as a broken page rather than an exhausted one. Mining now falls back to
the head term, and says that it did.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kdp_longtail_finder import head_terms, mine_suggestions


class FakeResponse:
    def __init__(self, values):
        self._values = values

    def json(self):
        return {"suggestions": [{"value": v} for v in self._values]}


def fake_fetch(by_term):
    """Serve suggestions for whichever known term the prefix starts with."""
    seen = []

    def fetch(url, **kwargs):
        from urllib.parse import parse_qs, urlparse
        prefix = parse_qs(urlparse(url).query)["prefix"][0].strip().lower()
        seen.append(prefix)
        for term in sorted(by_term, key=len, reverse=True):
            if prefix.startswith(term):
                return FakeResponse(by_term[term])
        return FakeResponse([])

    fetch.seen = seen
    return fetch


class TestHeadTerms:
    def test_the_full_seed_comes_first(self):
        assert head_terms("adhd for beginners")[0] == "adhd for beginners"

    def test_drops_trailing_words_to_reach_the_head(self):
        assert "adhd" in head_terms("adhd for beginners")

    def test_skips_fragments_that_end_in_a_stopword(self):
        # "adhd for" is not a query anyone types
        assert "adhd for" not in head_terms("adhd for beginners")

    def test_keeps_meaningful_intermediate_phrases(self):
        terms = head_terms("air fryer cookbook for weight loss")
        assert "air fryer cookbook" in terms
        assert "air fryer" in terms

    def test_a_single_word_seed_has_nowhere_to_broaden(self):
        assert head_terms("adhd") == ["adhd"]

    def test_terms_get_progressively_shorter(self):
        terms = head_terms("air fryer cookbook for weight loss")
        assert terms == sorted(terms, key=len, reverse=True)

    def test_handles_extra_whitespace(self):
        assert head_terms("  adhd   for  beginners ")[0] == "adhd for beginners"


class TestMiningWithoutBroadening:
    def test_uses_the_seed_when_the_seed_yields_enough(self):
        fetch = fake_fetch({"air fryer": [
            "air fryer cookbook", "air fryer recipes", "air fryer for beginners",
        ]})
        found, note = mine_suggestions("air fryer", "us", "edge", 10, fetch=fetch)
        assert "air fryer cookbook" in found
        assert note is None, "no broadening happened, so nothing to report"

    def test_drops_the_bare_seed_from_its_own_results(self):
        fetch = fake_fetch({"air fryer": ["air fryer", "air fryer cookbook"]})
        found, _ = mine_suggestions("air fryer", "us", "edge", 10, fetch=fetch)
        assert "air fryer" not in found

    def test_respects_the_limit(self):
        fetch = fake_fetch({"adhd": [f"adhd book {i}" for i in range(30)]})
        found, _ = mine_suggestions("adhd", "us", "edge", 5, fetch=fetch)
        assert len(found) == 5


class TestBroadeningFallback:
    """The behaviour that was missing: an exhausted seed should broaden."""

    def test_falls_back_to_the_head_term_when_the_seed_is_exhausted(self):
        fetch = fake_fetch({
            "adhd for beginners": ["adhd for beginners"],   # only itself
            "adhd": ["adhd women", "adhd 2.0", "adhd books"],
        })
        found, note = mine_suggestions("adhd for beginners", "us", "edge", 10, fetch=fetch)
        assert found, "should have broadened to the head term rather than give up"
        assert "adhd women" in found

    def test_says_that_it_broadened_and_to_what(self):
        fetch = fake_fetch({
            "adhd for beginners": [],
            "adhd": ["adhd women", "adhd books"],
        })
        _, note = mine_suggestions("adhd for beginners", "us", "edge", 10, fetch=fetch)
        assert note is not None
        assert "adhd" in note
        assert "adhd for beginners" in note

    def test_does_not_broaden_when_the_seed_already_works(self):
        fetch = fake_fetch({
            "adhd for beginners": ["adhd for beginners workbook",
                                   "adhd for beginners guide"],
            "adhd": ["adhd women"],
        })
        found, note = mine_suggestions("adhd for beginners", "us", "edge", 10, fetch=fetch)
        assert note is None
        assert "adhd women" not in found

    def test_returns_empty_without_crashing_when_nothing_anywhere(self):
        found, note = mine_suggestions("zzz qqq", "us", "edge", 10, fetch=fake_fetch({}))
        assert found == {}

    def test_a_network_error_does_not_escape(self):
        def boom(url, **kwargs):
            raise RuntimeError("connection reset")

        found, _ = mine_suggestions("adhd for beginners", "us", "edge", 10, fetch=boom)
        assert found == {}


class TestExhaustedTermBailsOutEarly:
    """Broadening must not cost 30 dead requests before it kicks in.

    EXPANSIONS has 30 entries. A seed Amazon has nothing for returns empty for
    every one of them, so the naive loop spent 30 delayed requests proving a
    negative before falling back — the job sat at 5% for minutes.
    """

    def test_gives_up_on_a_term_after_a_few_empty_expansions(self):
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            return FakeResponse([])

        mine_suggestions("adhd for beginners", "us", "edge", 10, fetch=fetch)
        assert len(calls) <= 12, f"probed {len(calls)} times for two dead terms"

    def test_still_explores_fully_when_the_term_is_productive(self):
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            return FakeResponse(["air fryer cookbook", "air fryer recipes"])

        found, _ = mine_suggestions("air fryer", "us", "edge", 10, fetch=fetch)
        assert found, "a productive term must not be cut short"
        assert len(calls) >= 2
