"""Tests for selector resilience against Amazon markup changes.

The project's stated top maintenance risk is that "Amazon markup changes will
eventually need selector touch-ups in the three CLI tools". One selector
carries most of that risk:

    kdp_niche_validator.py:290
    response.css('div[data-component-type="s-search-result"]')

When Amazon renames that attribute, every scan returns "No usable results
page (soft block?)" — indistinguishable from an IP block, so the operator
chases proxies instead of a selector. Scrapling can relocate the element by
similarity; this wires that in and reports which path was used, so a silent
relocation never masquerades as a normal parse.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kdp_adaptive
from scrapling.parser import Selector

URL = "https://www.amazon.com/s?k=air+fryer"

TODAY = '''<div data-component-type="s-search-result" data-asin="B01N3C85XY">
  <h2><span>The Air Fryer Cookbook</span></h2>
  <span class="a-price"><span class="a-offscreen">$4.99</span></span>
</div>'''

# Same listing after a plausible redesign: attribute renamed, classes
# changed, an extra wrapper introduced.
REDESIGNED = '''<div data-cy="product-card" data-item-id="B01N3C85XY" class="s-card-container">
  <div class="inner-wrap"><h2 class="a-size-base"><span>The Air Fryer Cookbook</span></h2>
  <span class="pricing"><span class="price-text">$4.99</span></span></div>
</div>'''

CARD = 'div[data-component-type="s-search-result"]'


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path):
    kdp_adaptive.configure(str(tmp_path / "adaptive.db"))
    yield


class TestConfiguration:
    def test_storage_lives_where_we_put_it_not_inside_the_package(self, tmp_path):
        path = str(tmp_path / "nested" / "adaptive.db")
        kdp_adaptive.configure(path)
        assert kdp_adaptive.storage_path() == path
        assert os.path.isdir(os.path.dirname(path)), "parent dir should be created"

    def test_configure_is_idempotent(self, tmp_path):
        path = str(tmp_path / "adaptive.db")
        kdp_adaptive.configure(path)
        kdp_adaptive.configure(path)
        assert kdp_adaptive.storage_path() == path


class TestSelectSurvivesRedesign:
    def test_direct_match_is_reported_as_direct(self):
        page = Selector(TODAY, adaptive=True, url=URL)
        elements, how = kdp_adaptive.select(page, CARD, identifier="search_card")
        assert len(elements) == 1
        assert how == "direct"

    def test_learns_the_element_on_a_successful_parse(self):
        page = Selector(TODAY, adaptive=True, url=URL)
        kdp_adaptive.select(page, CARD, identifier="search_card")
        # the save phase must have happened, or relocation later is impossible
        changed = Selector(REDESIGNED, adaptive=True, url=URL)
        elements, how = kdp_adaptive.select(changed, CARD, identifier="search_card")
        assert elements, "nothing was learned, so nothing could be relocated"

    def test_relocates_after_the_markup_changes(self):
        kdp_adaptive.select(Selector(TODAY, adaptive=True, url=URL), CARD,
                            identifier="search_card")
        changed = Selector(REDESIGNED, adaptive=True, url=URL)
        elements, how = kdp_adaptive.select(changed, CARD, identifier="search_card")
        assert how == "adaptive", "a relocation must announce itself"
        assert elements[0].attrib.get("data-item-id") == "B01N3C85XY"

    def test_relocated_element_is_still_usable(self):
        kdp_adaptive.select(Selector(TODAY, adaptive=True, url=URL), CARD,
                            identifier="search_card")
        changed = Selector(REDESIGNED, adaptive=True, url=URL)
        elements, _ = kdp_adaptive.select(changed, CARD, identifier="search_card")
        title = elements[0].css("h2 span::text")
        assert title and "Air Fryer Cookbook" in str(title[0])

    def test_a_genuinely_empty_page_is_reported_as_missing(self):
        empty = Selector("<html><body><p>nothing here</p></body></html>",
                         adaptive=True, url=URL)
        elements, how = kdp_adaptive.select(empty, CARD, identifier="never_seen")
        assert elements == []
        assert how == "missing"

    def test_missing_is_distinguishable_from_relocated(self):
        """A blocked page and a redesigned page must not look the same."""
        kdp_adaptive.select(Selector(TODAY, adaptive=True, url=URL), CARD,
                            identifier="search_card")
        _, redesigned = kdp_adaptive.select(
            Selector(REDESIGNED, adaptive=True, url=URL), CARD, identifier="search_card")
        _, blocked = kdp_adaptive.select(
            Selector("<html></html>", adaptive=True, url=URL), CARD, identifier="search_card")
        assert redesigned != blocked


class TestFailureIsNeverFatal:
    def test_a_storage_error_degrades_to_a_plain_selection(self, monkeypatch):
        page = Selector(TODAY, adaptive=True, url=URL)

        def boom(*a, **kw):
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(kdp_adaptive, "_adaptive_retry", boom)
        elements, how = kdp_adaptive.select(page, CARD, identifier="search_card")
        assert len(elements) == 1 and how == "direct"

    def test_adaptive_failure_on_an_empty_page_still_returns_cleanly(self, monkeypatch):
        page = Selector("<html></html>", adaptive=True, url=URL)

        def boom(*a, **kw):
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(kdp_adaptive, "_adaptive_retry", boom)
        elements, how = kdp_adaptive.select(page, CARD, identifier="search_card")
        assert elements == [] and how == "missing"


class TestStorageActuallyLandsWhereWeSaid:
    """configure() must steer Scrapling, not just record our intention.

    The first version of this module set an internal variable and asserted on
    it. That test passed while the fingerprint database was still being
    written inside the installed package — invisible in the repo and lost on
    every reinstall.
    """

    def test_the_database_file_is_created_at_our_path(self, tmp_path):
        path = str(tmp_path / "adaptive.db")
        kdp_adaptive.configure(path)
        page = Selector(TODAY, url=URL, **kdp_adaptive.selector_kwargs())
        kdp_adaptive.select(page, CARD, identifier="storage_probe")
        assert os.path.exists(path), "no fingerprint database written to our path"
        assert os.path.getsize(path) > 0

    def test_selector_kwargs_carry_the_storage_file(self, tmp_path):
        path = str(tmp_path / "adaptive.db")
        kdp_adaptive.configure(path)
        kwargs = kdp_adaptive.selector_kwargs()
        assert kwargs["adaptive"] is True
        assert kwargs["storage_args"]["storage_file"] == path

    def test_fingerprints_persist_across_separate_selector_objects(self, tmp_path):
        path = str(tmp_path / "adaptive.db")
        kdp_adaptive.configure(path)
        kdp_adaptive.select(Selector(TODAY, url=URL, **kdp_adaptive.selector_kwargs()),
                            CARD, identifier="persisted")
        # a brand new Selector, as a later scan would be
        later = Selector(REDESIGNED, url=URL, **kdp_adaptive.selector_kwargs())
        elements, how = kdp_adaptive.select(later, CARD, identifier="persisted")
        assert how == "adaptive" and elements


class TestSpiderActuallyEnablesAdaptive:
    """The save phase has to happen inside the spider, or none of this works.

    A live scan scraped 12 books cleanly and wrote no fingerprint database at
    all: `Fetcher.configure()` does not reach the Response objects a Spider
    builds. Those take their parsing arguments from `selector_config` on the
    session, so without it `auto_save=True` is silently inert and relocation
    could never happen in production.
    """

    def _sessions(self):
        from kdp_longtail_finder import STORES
        from kdp_niche_validator import KDPNicheSpider

        kdp_adaptive.configure()
        spider = KDPNicheSpider(keyword="probe", store=STORES["kindle"],
                                marketplace="us", max_books=1)
        captured = {}

        class Manager:
            def add(self, name, session, **kw):
                captured[name] = session

        spider.configure_sessions(Manager())
        return captured

    @staticmethod
    def _selector_config(session):
        """FetcherSession exposes it directly; browser sessions nest it in _config."""
        direct = getattr(session, "selector_config", None)
        if direct:
            return direct
        return getattr(getattr(session, "_config", None), "selector_config", None) or {}

    def test_the_http_session_carries_adaptive_selector_config(self):
        config = self._selector_config(self._sessions()["http"])
        assert config.get("adaptive") is True, \
            "auto_save is inert unless the Response is built with adaptive=True"

    def test_the_http_session_points_at_our_storage_file(self):
        config = self._selector_config(self._sessions()["http"])
        assert config.get("storage_args", {}).get("storage_file") == kdp_adaptive.storage_path()

    def test_the_escalation_session_is_configured_too(self):
        config = self._selector_config(self._sessions()["stealth"])
        assert config.get("adaptive") is True, \
            "a scan escalated to the stealth browser must still learn selectors"
