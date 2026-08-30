"""Tests for harvest concurrency and timeouts.

A live run sat at 8% for minutes: collectors ran one after another, and
`youtube_rising` alone issues categories x seeds x prefixes sequential
requests. Nothing bounded a slow collector either, so one stalled host held
the whole scan.

Concurrency is asserted by observing overlap rather than by timing, so these
tests do not go flaky on a loaded machine.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.discovery import collectors, pipeline as dpipe
from server.sources import base


class OverlapTracker:
    """Records the greatest number of calls in flight at the same moment."""

    def __init__(self, hold=0.05):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.hold = hold

    def enter(self):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.hold)
        with self.lock:
            self.active -= 1


PROGRESS = lambda stage, pct, msg: None


class TestCollectorsRunConcurrently:
    def test_collectors_overlap_instead_of_queueing(self):
        tracker = OverlapTracker()

        def slow(name):
            def collect(**kw):
                tracker.enter()
                return base.ok(name, [{"text": f"signal from {name}", "source": name,
                                       "intensity": 1, "url": "https://x/y"}])
            return collect

        dpipe.run_discovery(
            {"window": "7d"}, PROGRESS,
            collectors_map={n: slow(n) for n in ("a", "b", "c", "d")},
            validator=lambda p: {}, llm=None)
        assert tracker.peak > 1, "collectors still ran one at a time"

    def test_a_failing_collector_still_reports_unavailable_when_parallel(self):
        def boom(**kw):
            raise RuntimeError("kaboom")

        out = dpipe.run_discovery(
            {"window": "7d"}, PROGRESS,
            collectors_map={"bad": boom, "ok": lambda **kw: base.ok("ok", [])},
            validator=lambda p: {}, llm=None)
        assert out["sources"]["bad"]["status"] == "unavailable"
        assert "kaboom" in out["sources"]["bad"]["detail"]


class TestCollectorTimeout:
    def test_a_stalled_collector_is_cut_off_rather_than_holding_the_scan(self):
        def hangs(**kw):
            time.sleep(5)
            return base.ok("hangs", [])

        started = time.monotonic()
        out = dpipe.run_discovery(
            {"window": "7d", "collector_timeout": 0.3}, PROGRESS,
            collectors_map={"hangs": hangs}, validator=lambda p: {}, llm=None)
        elapsed = time.monotonic() - started
        assert elapsed < 4, f"waited {elapsed:.1f}s for a stalled collector"
        assert out["sources"]["hangs"]["status"] == "unavailable"
        assert "timed out" in out["sources"]["hangs"]["detail"].lower()

    def test_fast_collectors_are_unaffected_by_the_timeout(self):
        out = dpipe.run_discovery(
            {"window": "7d", "collector_timeout": 5}, PROGRESS,
            collectors_map={"quick": lambda **kw: base.ok(
                "quick", [{"text": "how to meal prep on a budget", "source": "youtube",
                           "intensity": 1, "url": "https://x/y"}])},
            validator=lambda p: {}, llm=None)
        assert out["sources"]["quick"]["status"] == "ok"


class TestAutocompleteFansOut:
    def test_youtube_issues_its_many_requests_concurrently(self):
        tracker = OverlapTracker(hold=0.03)

        class R:
            status = 200
            body = '["x",["how to meal prep","meal prep for beginners"]]'

        def fetch(url, **kwargs):
            tracker.enter()
            return R()

        result = collectors.youtube_rising(["cooking"], fetch=fetch)
        assert result.status == "ok"
        assert tracker.peak > 1, "autocomplete calls were still serial"

    def test_a_single_failing_request_does_not_lose_the_others(self):
        calls = {"n": 0}

        class Good:
            status = 200
            body = '["x",["meal prep for beginners"]]'

        def fetch(url, **kwargs):
            calls["n"] += 1
            if calls["n"] % 3 == 0:
                raise RuntimeError("flaky host")
            return Good()

        result = collectors.youtube_rising(["cooking"], fetch=fetch)
        assert result.status == "ok"
        assert result.items
