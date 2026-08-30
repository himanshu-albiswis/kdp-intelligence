"""Tests for Amazon transport defaults.

Measured against live Amazon on 2026-08-31, three consecutive trials each,
for `https://www.amazon.com/s?k=adhd+for+beginners&i=digital-text`:

    chrome + stealth headers -> HTTP 503,  2 KB decoy,  0 result cards  (x3)
    chrome + plain headers   -> HTTP 200, ~850 KB,     16 result cards
    edge   + stealth headers -> HTTP 200, ~920 KB,     16 result cards
    edge   + plain headers   -> HTTP 200, ~863 KB,     22 result cards
    safari + plain headers   -> HTTP 200, ~940 KB,     16 result cards

So the failure is not "stealth headers are bad" — edge tolerates them. It is
the chrome TLS fingerprint *combined* with Scrapling's stealth headers, and
that pairing was the shipped default. These tests pin the combination out.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import transport


class TestDefaults:
    def test_default_is_not_the_measured_dead_combination(self):
        impersonate, stealthy, _ = transport.resolve({})
        assert not (impersonate == "chrome" and stealthy), \
            "chrome + stealth headers returns a 503 decoy page from Amazon"

    def test_default_fingerprint_is_edge(self):
        impersonate, _, _ = transport.resolve({})
        assert impersonate == "edge"

    def test_default_keeps_stealth_headers(self):
        # edge tolerates them, and they help on other hosts
        _, stealthy, _ = transport.resolve({})
        assert stealthy is True

    def test_defaults_need_no_explanation(self):
        assert transport.resolve({})[2] == []


class TestChromeIsProtectedFromItself:
    def test_chrome_with_stealth_headers_is_downgraded_to_plain(self):
        impersonate, stealthy, _ = transport.resolve({"impersonate": "chrome"})
        assert impersonate == "chrome"
        assert stealthy is False

    def test_the_downgrade_is_explained_to_the_user(self):
        _, _, notes = transport.resolve({"impersonate": "chrome"})
        assert notes, "a silent downgrade is a surprise; say what happened"
        assert "503" in notes[0]

    def test_chrome_with_plain_headers_is_untouched_and_unremarked(self):
        impersonate, stealthy, notes = transport.resolve(
            {"impersonate": "chrome", "plain_headers": True})
        assert (impersonate, stealthy, notes) == ("chrome", False, [])


class TestExplicitChoicesAreRespected:
    def test_edge_keeps_stealth_headers(self):
        assert transport.resolve({"impersonate": "edge"})[:2] == ("edge", True)

    def test_plain_headers_flag_disables_stealth(self):
        assert transport.resolve({"impersonate": "edge", "plain_headers": True})[:2] == \
            ("edge", False)

    def test_safari_is_passed_through(self):
        assert transport.resolve({"impersonate": "safari"})[:2] == ("safari", True)

    def test_falsy_plain_headers_means_stealth_stays_on(self):
        assert transport.resolve({"impersonate": "edge", "plain_headers": False})[1] is True


class TestBlockDiagnosis:
    """A tiny 200 is Amazon lying to us. The advice given must be actionable."""

    def test_a_two_kilobyte_page_is_reported_as_a_soft_block(self):
        assert transport.looks_like_soft_block(2393) is True

    def test_a_real_search_page_is_not_a_soft_block(self):
        assert transport.looks_like_soft_block(1_004_995) is False

    def test_advice_leads_with_the_fingerprint_not_the_proxy(self):
        advice = transport.block_advice("chrome", True)
        assert "fingerprint" in advice.lower() or "chrome" in advice.lower()
        # The old message sent people to buy proxies for what was a config bug.
        assert advice.lower().index("chrome") < advice.lower().index("proxy")
