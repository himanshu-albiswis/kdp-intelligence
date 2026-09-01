"""Tests for concept extraction and gap scoring.

Stage 2 turns messy signals ("my 8yo was just diagnosed and I have no idea
what to do") into normalised book concepts, keeping links to the posts that
produced them. Stages 3-4 score demand and divide it by what Amazon already
supplies. The LLM is injected, so these run offline and deterministically.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.discovery import concepts, scoring


def signal(text, source="reddit", intensity=100.0, url="https://example.com/x",
           category="health", **extra):
    return {"text": text, "source": source, "intensity": intensity, "url": url,
            "category": category, **extra}


class TestBookIntentFilter:
    """Spend LLM tokens only on signals that could plausibly become a book."""

    def test_keeps_someone_asking_for_a_book(self):
        assert concepts.has_book_intent(
            signal("Is there a book about ADHD for newly diagnosed kids?")) is True

    def test_keeps_a_how_to_phrasing(self):
        assert concepts.has_book_intent(signal("how to meal prep on a budget")) is True

    def test_drops_a_news_cycle_item(self):
        # exactly what Google Trends served on the day this was written
        assert concepts.has_book_intent(signal("djokovic", source="google_trends")) is False

    def test_drops_a_bare_proper_noun(self):
        assert concepts.has_book_intent(signal("rays", source="google_trends")) is False

    def test_drops_something_far_too_short(self):
        assert concepts.has_book_intent(signal("adhd")) is False

    def test_keeps_a_struggle_described_in_plain_words(self):
        assert concepts.has_book_intent(
            signal("my 8yo was just diagnosed and I have no idea where to start")) is True


class TestConceptExtraction:
    signals = [
        signal("Is there a book about ADHD for newly diagnosed kids?", intensity=340),
        signal("my 8yo was just diagnosed, where do I start", intensity=120),
        signal("how to meal prep on a budget", source="youtube", intensity=0.9,
               category="cooking"),
    ]

    def test_uses_the_llm_when_one_is_supplied(self):
        def llm(prompt):
            return '[{"concept":"ADHD parenting guide for newly diagnosed kids",'\
                   '"audience":"parents","category":"health","signal_indexes":[0,1]}]'
        out = concepts.extract(self.signals, llm=llm)
        assert out[0]["concept"] == "ADHD parenting guide for newly diagnosed kids"
        assert out[0]["audience"] == "parents"

    def test_every_concept_keeps_links_to_its_evidence(self):
        def llm(prompt):
            return '[{"concept":"ADHD parenting guide","audience":"parents",' \
                   '"category":"health","signal_indexes":[0,1]}]'
        out = concepts.extract(self.signals, llm=llm)
        assert len(out[0]["evidence"]) == 2
        assert all("url" in e for e in out[0]["evidence"])

    def test_falls_back_to_deterministic_grouping_without_an_llm(self):
        out = concepts.extract(self.signals, llm=None)
        assert out, "must still produce concepts when no LLM key is configured"
        assert all(c["evidence"] for c in out)

    def test_says_which_method_produced_each_concept(self):
        out = concepts.extract(self.signals, llm=None)
        assert out[0]["method"] == "heuristic"

    def test_a_broken_llm_reply_degrades_to_the_fallback(self):
        out = concepts.extract(self.signals, llm=lambda p: "not json at all")
        assert out, "a bad model reply must not lose the harvest"
        assert out[0]["method"] == "heuristic"

    def test_ignores_signal_indexes_the_model_invented(self):
        def llm(prompt):
            return '[{"concept":"X","audience":"y","category":"health",' \
                   '"signal_indexes":[0,99]}]'
        out = concepts.extract(self.signals, llm=llm)
        assert len(out[0]["evidence"]) == 1

    def test_never_shows_a_concept_whose_evidence_was_all_invented(self):
        def llm(prompt):
            return '[{"concept":"X","audience":"y","category":"health","signal_indexes":[99]}]'
        out = concepts.extract(self.signals, llm=llm)
        assert all(c["concept"] != "X" for c in out), "cited nothing real; must not appear"
        # losing the whole harvest to one bad reply would be worse, so grouping runs
        assert all(c["method"] == "heuristic" for c in out)


class TestDemandScore:
    def test_more_independent_sources_scores_higher(self):
        one = {"evidence": [{"source": "reddit", "intensity": 100}]}
        two = {"evidence": [{"source": "reddit", "intensity": 100},
                            {"source": "youtube", "intensity": 100}]}
        assert scoring.demand(two) > scoring.demand(one)

    def test_higher_engagement_scores_higher(self):
        quiet = {"evidence": [{"source": "reddit", "intensity": 5}]}
        loud = {"evidence": [{"source": "reddit", "intensity": 900}]}
        assert scoring.demand(loud) > scoring.demand(quiet)

    def test_a_single_source_concept_is_demoted(self):
        single = {"evidence": [{"source": "reddit", "intensity": 900}]}
        assert scoring.demand(single) < 100
        assert scoring.is_corroborated(single) is False

    def test_two_distinct_sources_count_as_corroborated(self):
        both = {"evidence": [{"source": "reddit", "intensity": 10},
                             {"source": "amazon_new_releases", "intensity": 1}]}
        assert scoring.is_corroborated(both) is True

    def test_repeats_from_one_source_are_not_corroboration(self):
        same = {"evidence": [{"source": "reddit", "intensity": 10},
                             {"source": "reddit", "intensity": 10}]}
        assert scoring.is_corroborated(same) is False

    def test_no_evidence_scores_zero(self):
        assert scoring.demand({"evidence": []}) == 0


class TestGapScore:
    strong_demand = 80.0

    def test_thin_shelf_with_weak_moat_is_the_jackpot(self):
        gap = scoring.gap(self.strong_demand,
                          {"total_results": 120, "median_reviews": 8, "phrase_in_titles": 1})
        assert gap["score"] >= 70
        assert gap["verdict"].startswith("GOLDMINE")

    def test_crowded_shelf_with_deep_moat_is_avoided(self):
        gap = scoring.gap(self.strong_demand,
                          {"total_results": 60_000, "median_reviews": 4_000,
                           "phrase_in_titles": 12})
        assert gap["score"] < 40
        assert "AVOID" in gap["verdict"] or "CROWDED" in gap["verdict"]

    def test_more_competition_lowers_the_score(self):
        thin = scoring.gap(self.strong_demand, {"total_results": 200, "median_reviews": 10,
                                                "phrase_in_titles": 1})
        thick = scoring.gap(self.strong_demand, {"total_results": 20_000, "median_reviews": 10,
                                                 "phrase_in_titles": 1})
        assert thin["score"] > thick["score"]

    def test_a_deep_review_moat_lowers_the_score(self):
        easy = scoring.gap(self.strong_demand, {"total_results": 500, "median_reviews": 5,
                                                "phrase_in_titles": 1})
        hard = scoring.gap(self.strong_demand, {"total_results": 500, "median_reviews": 2_000,
                                               "phrase_in_titles": 1})
        assert easy["score"] > hard["score"]

    def test_missing_amazon_data_refuses_to_score(self):
        gap = scoring.gap(self.strong_demand, {})
        assert gap["score"] is None
        assert "VERIFY" in gap["verdict"]

    def test_weak_demand_cannot_produce_a_goldmine(self):
        gap = scoring.gap(3.0, {"total_results": 100, "median_reviews": 2,
                                "phrase_in_titles": 0})
        assert not gap["verdict"].startswith("GOLDMINE")


class TestExtractionReportsWhyItFellBack:
    """A silent fallback to grouping hid a working LLM behind bad concepts.

    When extraction degrades, the run must say so — otherwise the only clue
    is `method: heuristic` on the cards and no way to tell whether a key is
    missing, the model erred, or the reply was unparseable.
    """

    signals = [signal("is there a book about adhd for newly diagnosed kids")]

    def test_reports_no_provider_when_no_llm_is_configured(self):
        out, report = concepts.extract_with_report(self.signals, llm=None)
        assert report["method"] == "heuristic"
        assert report["reason"] == "no_llm_configured"

    def test_reports_the_exception_when_the_model_call_raises(self):
        def boom(prompt):
            raise RuntimeError("429 quota exceeded")

        out, report = concepts.extract_with_report(self.signals, llm=boom)
        assert report["method"] == "heuristic"
        assert "429 quota exceeded" in report["reason"]

    def test_reports_unparseable_replies_distinctly_from_errors(self):
        out, report = concepts.extract_with_report(self.signals, llm=lambda p: "prose, not json")
        assert report["method"] == "heuristic"
        assert "unparseable" in report["reason"]

    def test_reports_success_and_counts_the_batches(self):
        def llm(prompt):
            return '[{"concept":"ADHD guide","audience":"parents",' \
                   '"category":"health","signal_indexes":[0]}]'

        out, report = concepts.extract_with_report(self.signals, llm=llm)
        assert report["method"] == "llm"
        assert report["batches"] == 1
        assert out[0]["concept"] == "ADHD guide"

    def test_extract_still_returns_just_concepts_for_existing_callers(self):
        assert isinstance(concepts.extract(self.signals, llm=None), list)


class TestTruncatedReplyRecovery:
    """gemini-2.5-flash spends output budget on thinking before answering.

    On a large prompt the JSON array gets cut mid-array. Every complete object
    before the cut is still perfectly good data, so throwing the whole reply
    away wastes a call and silently degrades to keyword grouping.
    """

    truncated = '''[
      {"concept":"ADHD focus guide","audience":"adults","category":"health","signal_indexes":[0]},
      {"concept":"Menopause nutrition","audience":"women","category":"health","signal_indexes":[1]},
      {"concept":"Back pain at h'''

    def test_recovers_the_complete_objects_before_the_cut(self):
        parsed = concepts._parse_llm(self.truncated)
        assert parsed is not None, "a truncated array should not be a total loss"
        assert len(parsed) == 2
        assert parsed[0]["concept"] == "ADHD focus guide"

    def test_does_not_invent_the_incomplete_trailing_object(self):
        parsed = concepts._parse_llm(self.truncated)
        assert all(c["concept"] != "Back pain at h" for c in parsed)

    def test_still_returns_none_when_nothing_is_recoverable(self):
        assert concepts._parse_llm("I'm sorry, I cannot help with that.") is None

    def test_still_returns_none_for_an_empty_reply(self):
        assert concepts._parse_llm("") is None

    def test_intact_json_is_unaffected(self):
        good = '[{"concept":"X","audience":"a","category":"health","signal_indexes":[0]}]'
        assert len(concepts._parse_llm(good)) == 1


class TestSearchPhrase:
    """Concepts are descriptions; Amazon wants a phrase a buyer would type.

    Validating "Practical strategies for managing ADHD symptoms and improving
    daily life, including study habits, sleep, dopamine regulation, and
    medication considerations" against Amazon search returns nothing — and
    handing it to the research form as a seed exceeded the API's 120-char
    limit, which surfaced as an [object Object] alert. Every concept now
    carries a short search_phrase: the model's when it gives one, a derived
    fallback when it does not.
    """

    def test_the_model_provided_phrase_is_used(self):
        signals = [{"text": "is there a book about adhd for newly diagnosed kids",
                    "source": "reddit", "intensity": 300, "url": "https://r/x",
                    "category": "health", "asking": True}]

        def llm(prompt):
            return ('[{"concept":"ADHD parenting guide for newly diagnosed kids",'
                    '"search_phrase":"adhd parenting guide",'
                    '"audience":"parents","category":"health","signal_indexes":[0]}]')

        out = concepts.extract(signals, llm=llm)
        assert out[0]["search_phrase"] == "adhd parenting guide"

    def test_a_missing_phrase_falls_back_to_a_derivation(self):
        signals = [{"text": "is there a book about adhd for newly diagnosed kids",
                    "source": "reddit", "intensity": 300, "url": "https://r/x",
                    "category": "health", "asking": True}]

        def llm(prompt):
            return ('[{"concept":"ADHD parenting guide for newly diagnosed kids",'
                    '"audience":"parents","category":"health","signal_indexes":[0]}]')

        out = concepts.extract(signals, llm=llm)
        assert out[0]["search_phrase"], "no concept may ship without one"
        assert len(out[0]["search_phrase"]) <= 60

    def test_heuristic_concepts_also_carry_one(self):
        signals = [{"text": "how to meal prep on a budget", "source": "youtube",
                    "intensity": 0.9, "url": "https://y/z", "category": "cooking"}]
        out = concepts.extract(signals, llm=None)
        assert all(c.get("search_phrase") for c in out)

    def test_derivation_shortens_a_long_description(self):
        phrase = concepts.derive_search_phrase(
            "Practical strategies for managing ADHD symptoms and improving daily "
            "life, including study habits, sleep, dopamine regulation, and "
            "medication considerations")
        assert 0 < len(phrase) <= 60
        assert "adhd" in phrase.lower()

    def test_derivation_strips_filler_words_from_the_front(self):
        phrase = concepts.derive_search_phrase(
            "A comprehensive guide to understanding menopause nutrition")
        assert not phrase.lower().startswith(("a ", "the ", "comprehensive"))

    def test_a_short_concept_passes_through_unchanged(self):
        assert concepts.derive_search_phrase("adhd books for adults") == "adhd books for adults"


class TestSearchPhraseIsOnePhrase:
    """A live run got 'anxiety relief guide, how to worry less' — the model
    joined two phrases despite the prompt. Amazon gets exactly one."""

    def test_a_comma_joined_reply_keeps_only_the_first_phrase(self):
        signals = [{"text": "is there a book about anxiety relief", "source": "reddit",
                    "intensity": 300, "url": "https://r/x", "category": "health",
                    "asking": True}]

        def llm(prompt):
            return ('[{"concept":"Anxiety relief for adults",'
                    '"search_phrase":"anxiety relief guide, how to worry less",'
                    '"audience":"a","category":"health","signal_indexes":[0]}]')

        out = concepts.extract(signals, llm=llm)
        assert out[0]["search_phrase"] == "anxiety relief guide"

    def test_derivation_also_stops_at_the_first_comma(self):
        assert "," not in concepts.derive_search_phrase(
            "menopause nutrition, hormone balance, and weight management")
