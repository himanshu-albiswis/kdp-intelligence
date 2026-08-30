"""Trend Radar should not demand a topic.

Discovery harvests with no seed; Trend Radar requiring one is an
inconsistency, not a design choice. With a topic it narrows to that topic;
without one it reports what is trending across the whole category panel.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import trends
from server.sources import base

PROGRESS = lambda stage, pct, msg: None


def sources_with(phrases, topic_source="google"):
    return {topic_source: base.ok(topic_source,
            [{"phrase": p, "position": i} for i, p in enumerate(phrases)],
            detail="stub")}


class TestCandidateMining:
    phrases = ["adhd for beginners", "meal prep for shift workers", "adhd focus tips"]

    def test_a_topic_narrows_candidates_to_that_topic(self):
        found = trends._mine_candidates("adhd", sources_with(self.phrases))
        assert all("adhd" in c["phrase"] for c in found)

    def test_no_topic_keeps_everything_it_harvested(self):
        found = trends._mine_candidates("", sources_with(self.phrases))
        phrases = {c["phrase"] for c in found}
        assert "meal prep for shift workers" in phrases
        assert "adhd for beginners" in phrases

    def test_none_topic_behaves_like_no_topic(self):
        assert trends._mine_candidates(None, sources_with(self.phrases))

    def test_junk_is_still_filtered_without_a_topic(self):
        found = trends._mine_candidates("", sources_with(["adhd pdf free download"]))
        assert found == []


class TestRequestModel:
    def test_the_api_accepts_a_request_with_no_seed(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "server"))
        from app import TrendRequest
        assert TrendRequest().seed in (None, "")

    def test_the_api_still_accepts_a_seed(self):
        from app import TrendRequest
        assert TrendRequest(seed="adhd").seed == "adhd"

    def test_a_one_character_seed_is_still_rejected(self):
        from app import TrendRequest
        with pytest.raises(Exception):
            TrendRequest(seed="a")


class TestSeedlessRun:
    def test_running_without_a_seed_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(trends.registry, "collect_all",
                            lambda topic, **kw: sources_with(["adhd for beginners"]))
        monkeypatch.setattr(trends, "_validate_against_amazon", lambda *a, **kw: ({}, []))
        out = trends.run_trend_radar({}, PROGRESS)
        assert out["topic"] in (None, "")
        assert "candidates" in out

    def test_seedless_runs_are_labelled_for_the_header(self, monkeypatch):
        monkeypatch.setattr(trends.registry, "collect_all",
                            lambda topic, **kw: sources_with(["adhd for beginners"]))
        monkeypatch.setattr(trends, "_validate_against_amazon", lambda *a, **kw: ({}, []))
        out = trends.run_trend_radar({}, PROGRESS)
        assert out.get("scope") == "all categories"

    def test_a_seeded_run_is_labelled_with_its_topic(self, monkeypatch):
        monkeypatch.setattr(trends.registry, "collect_all",
                            lambda topic, **kw: sources_with(["adhd for beginners"]))
        monkeypatch.setattr(trends, "_validate_against_amazon", lambda *a, **kw: ({}, []))
        out = trends.run_trend_radar({"seed": "adhd"}, PROGRESS)
        assert out["scope"] == "adhd"


class TestSeedlessLabelIsNotATopic:
    """The jobs list needs a label; the miner must not treat it as a topic.

    Setting params["seed"] = "all categories" to label the job would make
    "all" and "categories" the words every candidate has to match, which
    silently returns nothing.
    """

    def test_the_label_never_leaks_into_candidate_filtering(self):
        phrases = ["adhd for beginners", "meal prep for shift workers"]
        found = trends._mine_candidates("all categories", sources_with(phrases))
        assert found == [], "this is why the label must not be used as the topic"

    def test_a_seedless_request_keeps_its_topic_empty(self):
        from app import TrendRequest
        import app as app_mod
        params = app_mod._trend_params(TrendRequest())
        assert not params.get("seed"), "topic must stay empty for a seedless scan"
        assert params.get("label") == "all categories"

    def test_a_seeded_request_labels_itself_with_the_topic(self):
        from app import TrendRequest
        import app as app_mod
        params = app_mod._trend_params(TrendRequest(seed="adhd"))
        assert params["seed"] == "adhd"
        assert params["label"] == "adhd"


class TestSeedlessModeRejectsNewsNoise:
    """A live seedless scan returned "bookmyshow" as a GO opportunity.

    Google Trends' daily feed is news-shaped ("rays", "djokovic", "formula 1").
    With a topic those get filtered out by the shared-word rule; without one
    nothing stopped them, so news headlines were validated against Amazon and
    presented as book niches. Seedless mining must require book intent — the
    same bar Discovery already applies.
    """

    news = ["bookmyshow", "formula 1", "djokovic", "rays"]
    real = ["adhd for beginners", "how to meal prep on a budget",
            "beginner yoga for back pain"]

    def test_news_headlines_are_not_book_niches(self):
        found = trends._mine_candidates("", sources_with(self.news))
        assert found == [], f"news leaked through: {[c['phrase'] for c in found]}"

    def test_genuine_learnable_phrases_survive(self):
        found = trends._mine_candidates("", sources_with(self.real))
        phrases = {c["phrase"] for c in found}
        assert "adhd for beginners" in phrases
        assert "how to meal prep on a budget" in phrases

    def test_a_bare_product_name_is_rejected(self):
        assert trends._mine_candidates("", sources_with(["bookmyshow"])) == []

    def test_the_intent_bar_does_not_apply_when_a_topic_is_given(self):
        # with an explicit topic the user has already asserted relevance
        found = trends._mine_candidates("formula", sources_with(["formula 1 racing"]))
        assert found, "an explicit topic should not be second-guessed"


class TestTransactionalSearchesAreNotBookNiches:
    """A live seedless scan surfaced "how to delete instagram account" as GO.

    Phrases that are shaped like something learnable but are really an
    account action, a booking, or a local lookup are not niches anyone
    publishes a book into. They arrive from Google Trends' regional feed.
    """

    junk = ["how to delete instagram account", "book my ticket", "book my hsrp",
            "customer care number for airtel", "iphone 17 price in india",
            "best restaurants near me", "sbi net banking login"]

    real = ["how to meal prep on a budget", "beginner yoga for back pain",
            "adhd for beginners", "air fryer cookbook for two"]

    @pytest.mark.parametrize("phrase", junk)
    def test_transactional_phrases_are_rejected(self, phrase):
        found = trends._mine_candidates("", sources_with([phrase]))
        assert found == [], f"{phrase!r} is not a book niche"

    @pytest.mark.parametrize("phrase", real)
    def test_genuine_niches_still_pass(self, phrase):
        found = trends._mine_candidates("", sources_with([phrase]))
        assert found, f"{phrase!r} should have survived"
