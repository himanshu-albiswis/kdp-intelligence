"""Tests for Discovery orchestration: harvest -> concepts -> gap -> cards.

Collectors and the Amazon validator are injected, so the whole pipeline runs
offline. What is under test is the wiring and the guarantees: failures stay
isolated, uncorroborated concepts get demoted, only the top concepts cost an
Amazon request, and every card can be traced back to real evidence.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.discovery import pipeline as dpipe
from server.sources import base


def sig(text, source="reddit", intensity=200.0, category="health"):
    return {"text": text, "source": source, "intensity": intensity,
            "url": f"https://example.com/{abs(hash(text)) % 999}", "category": category,
            "asking": True}


def collector_ok(name, signals):
    return lambda **kw: base.ok(name, signals, detail=f"{len(signals)} signals")


def collector_dead(name):
    def boom(**kw):
        raise RuntimeError("collector exploded")
    return boom


PROGRESS = lambda stage, pct, msg: None


def fake_validator(phrases):
    """Pretend Amazon: a thin shelf for everything."""
    return {p: {"total_results": 150, "median_reviews": 6, "phrase_in_titles": 1,
                "url": f"https://amazon.com/s?k={p}"} for p in phrases}


class TestHarvestIsolation:
    def test_one_dead_collector_does_not_kill_the_run(self):
        out = dpipe.run_discovery(
            {"window": "7d"}, PROGRESS,
            collectors_map={
                "good": collector_ok("good", [sig("is there a book about adhd for kids")]),
                "bad": collector_dead("bad"),
            },
            validator=fake_validator, llm=None)
        assert out["source_health"]["ok"] >= 1
        assert "bad" in out["source_health"]["degraded"]

    def test_every_source_is_reported_even_when_it_failed(self):
        out = dpipe.run_discovery(
            {"window": "7d"}, PROGRESS,
            collectors_map={"bad": collector_dead("bad")},
            validator=fake_validator, llm=None)
        assert "bad" in out["sources"]
        assert out["sources"]["bad"]["status"] == "unavailable"

    def test_rejects_a_window_we_do_not_offer(self):
        with pytest.raises(ValueError):
            dpipe.run_discovery({"window": "forever"}, PROGRESS,
                                collectors_map={}, validator=fake_validator, llm=None)


class TestCards:
    signals_a = [sig("is there a book about adhd for newly diagnosed kids")]
    signals_b = [sig("how to parent a newly diagnosed adhd kid", source="youtube",
                     intensity=0.9)]

    def build(self, **kw):
        return dpipe.run_discovery(
            {"window": "7d", "validate_top": 5}, PROGRESS,
            collectors_map={
                "reddit_panel": collector_ok("reddit_panel", self.signals_a),
                "youtube": collector_ok("youtube", self.signals_b),
            },
            validator=fake_validator, llm=None, **kw)

    def test_produces_ranked_opportunity_cards(self):
        out = self.build()
        assert out["cards"], "expected at least one opportunity card"
        scores = [c["gap"]["score"] for c in out["cards"] if c["gap"]["score"] is not None]
        assert scores == sorted(scores, reverse=True)

    def test_each_card_carries_its_evidence_links(self):
        for card in self.build()["cards"]:
            assert card["evidence"], "a card with no receipts is not allowed"
            assert all(e.get("url") for e in card["evidence"])

    def test_each_card_reports_demand_supply_and_gap(self):
        card = self.build()["cards"][0]
        assert "demand" in card and "gap" in card
        assert "verdict" in card["gap"]

    def test_cards_expose_a_phrase_to_hand_to_full_validation(self):
        card = self.build()["cards"][0]
        assert card["validate_phrase"], "the 'Run full validation' button needs a seed"

    def test_window_is_echoed_back_for_the_header(self):
        assert self.build()["window"] == "7d"


class TestCostControl:
    def test_only_the_top_concepts_cost_an_amazon_request(self):
        many = [sig(f"is there a book about topic number {i} for beginners")
                for i in range(20)]
        asked = []

        def counting_validator(phrases):
            asked.extend(phrases)
            return fake_validator(phrases)

        dpipe.run_discovery(
            {"window": "7d", "validate_top": 4}, PROGRESS,
            collectors_map={"reddit_panel": collector_ok("reddit_panel", many)},
            validator=counting_validator, llm=None)
        assert len(asked) <= 4, f"validated {len(asked)} concepts against Amazon"

    def test_no_concepts_means_no_amazon_calls_at_all(self):
        asked = []

        def counting_validator(phrases):
            asked.extend(phrases)
            return {}

        out = dpipe.run_discovery(
            {"window": "7d"}, PROGRESS,
            collectors_map={"reddit_panel": collector_ok("reddit_panel", [])},
            validator=counting_validator, llm=None)
        assert asked == []
        assert out["cards"] == []
