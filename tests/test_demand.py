"""Tests for the per-keyword demand index.

Publisher Rocket's most-quoted feature is "estimated searches/month" — a
number nobody outside Amazon can actually know. This index answers the same
buyer question ("which of these keywords do people type more?") without the
false precision: it measures how eagerly Amazon's own autocomplete surfaces
the phrase, which Amazon only does for things people genuinely type.

Two observable signals, both from Amazon itself:
  * prefix depth — how few typed characters make the phrase appear.
    "air fryer cookbook" appearing after 3 characters is mass demand;
    appearing only once fully typed is thin.
  * position — where in the 10-slot dropdown it appears at that depth.

The output is a 0-100 index plus a plain-language band and a basis string
that says exactly what was measured, so nobody mistakes it for volume.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kdp_estimates as est
from kdp_longtail_finder import probe_prefix_depth


class TestDemandIndexScoring:
    def test_shallow_prefix_beats_deep_prefix(self):
        early = est.demand_index(prefix_len=3, keyword_len=18, position=0, suggest_rank=0)
        late = est.demand_index(prefix_len=15, keyword_len=18, position=0, suggest_rank=0)
        assert early["index"] > late["index"]

    def test_top_of_dropdown_beats_bottom(self):
        top = est.demand_index(prefix_len=5, keyword_len=18, position=0, suggest_rank=0)
        bottom = est.demand_index(prefix_len=5, keyword_len=18, position=9, suggest_rank=0)
        assert top["index"] > bottom["index"]

    def test_never_surfacing_scores_zero_band_thin(self):
        d = est.demand_index(prefix_len=None, keyword_len=18, position=None, suggest_rank=0)
        assert d["index"] == 0
        assert d["band"] == "thin"

    def test_index_stays_in_range(self):
        best = est.demand_index(prefix_len=2, keyword_len=30, position=0, suggest_rank=0)
        assert 0 <= best["index"] <= 100

    def test_bands_are_monotonic_with_the_index(self):
        order = ["thin", "niche", "moderate", "high", "very high"]
        seen = [est.demand_index(prefix_len=p, keyword_len=20, position=0, suggest_rank=0)["band"]
                for p in (None, 18, 12, 7, 3)]
        assert [order.index(b) for b in seen] == sorted(order.index(b) for b in seen)

    def test_the_basis_says_what_was_measured_not_volume(self):
        d = est.demand_index(prefix_len=4, keyword_len=18, position=1, suggest_rank=0)
        assert "4" in d["basis"] and "character" in d["basis"]
        assert "searches per month" not in d["basis"].lower()

    def test_full_length_prefix_is_the_weakest_nonzero_signal(self):
        only_full = est.demand_index(prefix_len=18, keyword_len=18, position=0, suggest_rank=0)
        assert 0 < only_full["index"] < 40


class TestPrefixProbe:
    """probe_prefix_depth asks autocomplete with growing prefixes."""

    class R:
        def __init__(self, suggestions):
            self._s = suggestions

        def json(self):
            return {"suggestions": [{"value": v} for v in self._s]}

    def test_finds_the_shallowest_prefix_that_surfaces_the_keyword(self):
        def fetch(url, **kw):
            from urllib.parse import parse_qs, urlparse
            prefix = parse_qs(urlparse(url).query)["prefix"][0]
            if len(prefix) >= 5:
                return self.R(["air fryer cookbook", "air fryer recipes"])
            return self.R(["airpods", "air max"])

        depth, position = probe_prefix_depth("air fryer cookbook", "us", "edge", fetch=fetch)
        assert depth == 5
        assert position == 0

    def test_reports_none_when_the_keyword_never_surfaces(self):
        depth, position = probe_prefix_depth("zzz qqq xxx", "us", "edge",
                                             fetch=lambda url, **kw: self.R(["unrelated"]))
        assert depth is None and position is None

    def test_probes_are_bounded_not_one_per_character(self):
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            return self.R([])

        probe_prefix_depth("a very long keyword phrase indeed", "us", "edge", fetch=fetch)
        assert len(calls) <= 5, f"{len(calls)} probes for one keyword is too chatty"

    def test_a_network_error_degrades_to_unknown(self):
        def boom(url, **kw):
            raise RuntimeError("reset")

        assert probe_prefix_depth("air fryer", "us", "edge", fetch=boom) == (None, None)

    def test_matching_is_case_and_space_insensitive(self):
        def fetch(url, **kw):
            return self.R(["Air  Fryer Cookbook"])

        depth, _ = probe_prefix_depth("air fryer cookbook", "us", "edge", fetch=fetch)
        assert depth is not None


class TestAttachDemand:
    """Demand is probed for every validated keyword, concurrently and safely."""

    class R:
        def __init__(self, suggestions):
            self._s = suggestions

        def json(self):
            return {"suggestions": [{"value": v} for v in self._s]}

    def _metric(self, kw):
        from kdp_longtail_finder import KeywordMetrics
        return KeywordMetrics(keyword=kw, suggest_rank=0, page1_books=5,
                              phrase_in_titles=3)

    def test_every_keyword_gets_a_demand_reading(self):
        from kdp_longtail_finder import attach_demand
        metrics = [self._metric("air fryer cookbook"), self._metric("adhd books")]
        attach_demand(metrics, "us", "edge",
                      fetch=lambda url, **kw: self.R(["air fryer cookbook", "adhd books"]))
        assert all(m.demand_index > 0 for m in metrics)
        assert all(m.demand_band for m in metrics)

    def test_a_keyword_amazon_never_suggests_reads_thin_not_blank(self):
        from kdp_longtail_finder import attach_demand
        metrics = [self._metric("zzz qqq unheard of")]
        attach_demand(metrics, "us", "edge", fetch=lambda url, **kw: self.R(["other"]))
        assert metrics[0].demand_index == 0
        assert metrics[0].demand_band == "thin"

    def test_one_failing_probe_does_not_lose_the_rest(self):
        from kdp_longtail_finder import attach_demand
        calls = {"n": 0}

        def flaky(url, **kw):
            calls["n"] += 1
            if "adhd" in url:
                raise RuntimeError("reset")
            return self.R(["air fryer cookbook"])

        metrics = [self._metric("air fryer cookbook"), self._metric("adhd books")]
        attach_demand(metrics, "us", "edge", fetch=flaky)
        assert metrics[0].demand_index > 0
        assert metrics[1].demand_band == "thin"
