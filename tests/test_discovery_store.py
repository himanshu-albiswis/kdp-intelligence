"""Tests for the Discovery observation history.

Momentum ("growing every scan for nine days") is the whole point of the time
window, and it needs history. This store records every scored concept on every
run so that momentum becomes computable later — and reports honestly that it
cannot compute momentum yet when only one observation exists.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.discovery.store import DiscoveryStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield DiscoveryStore(os.path.join(tmp, "discovery.db"))


def card(concept="adhd parenting guide", demand=60.0, gap=70.0):
    return {"concept": concept, "demand": demand, "category": "health",
            "gap": {"score": gap, "verdict": "GO — real demand"}}


class TestRecording:
    def test_records_each_card_as_an_observation(self, store):
        store.record("7d", [card(), card("meal prep for shift workers")])
        assert store.count() == 2

    def test_repeat_scans_accumulate_rather_than_overwrite(self, store):
        store.record("7d", [card(demand=60)])
        store.record("7d", [card(demand=75)])
        assert store.count() == 2
        assert len(store.history("adhd parenting guide")) == 2

    def test_history_is_returned_oldest_first(self, store):
        store.record("7d", [card(demand=10)])
        store.record("7d", [card(demand=90)])
        demands = [h["demand"] for h in store.history("adhd parenting guide")]
        assert demands == [10, 90]

    def test_an_unseen_concept_has_no_history(self, store):
        assert store.history("nothing here") == []


class TestMomentum:
    def test_refuses_to_call_one_observation_a_trend(self, store):
        store.record("7d", [card(demand=60)])
        m = store.momentum("adhd parenting guide")
        assert m["status"] == "insufficient_history"
        assert m["change"] is None

    def test_reports_growth_across_scans(self, store):
        store.record("7d", [card(demand=40)])
        store.record("7d", [card(demand=70)])
        m = store.momentum("adhd parenting guide")
        assert m["status"] == "rising"
        assert m["change"] > 0

    def test_reports_decline(self, store):
        store.record("7d", [card(demand=80)])
        store.record("7d", [card(demand=30)])
        assert store.momentum("adhd parenting guide")["status"] == "falling"

    def test_flat_demand_is_neither_rising_nor_falling(self, store):
        store.record("7d", [card(demand=50)])
        store.record("7d", [card(demand=50)])
        assert store.momentum("adhd parenting guide")["status"] == "flat"

    def test_counts_how_many_scans_have_seen_it(self, store):
        for _ in range(3):
            store.record("7d", [card()])
        assert store.momentum("adhd parenting guide")["observations"] == 3


class TestDurability:
    def test_survives_reopening_the_database(self, store):
        store.record("7d", [card()])
        reopened = DiscoveryStore(store.path)
        assert reopened.count() == 1

    def test_a_card_without_a_gap_score_is_still_recorded(self, store):
        store.record("7d", [{"concept": "x", "demand": 20.0, "category": "health",
                             "gap": {"score": None, "verdict": "VERIFY"}}])
        assert store.count() == 1
