"""Tests for seedless Discovery harvesting.

Collector reachability, probed live on 2026-08-31 from a residential IP:

    Amazon Hot New Releases      200, 32 ASINs + 26 titles   -> usable
    Amazon Movers & Shakers      200 but 2 ASINs, 0 titles   -> lazy-loaded,
                                 unchanged through a stealth browser
    Google Trends daily RSS      200, 10 items, but they are news
                                 ("rays", "djokovic") not book intent
    Reddit subreddit json/rss    403 without credentials

Network is injected so these run offline.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.discovery import collectors


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self.body = body


def fetcher(**by_substring):
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        for needle, response in by_substring.items():
            if needle in url:
                return response
        return FakeResponse(404, "")

    fetch.calls = calls
    return fetch


class TestWindows:
    def test_maps_the_three_offered_windows_to_reddit_spans(self):
        assert collectors.reddit_span("24h") == "day"
        assert collectors.reddit_span("7d") == "week"
        assert collectors.reddit_span("30d") == "month"

    def test_rejects_a_window_we_do_not_offer(self):
        with pytest.raises(ValueError):
            collectors.reddit_span("all time")

    def test_window_days_are_numeric_for_filtering(self):
        assert collectors.window_days("24h") == 1
        assert collectors.window_days("7d") == 7
        assert collectors.window_days("30d") == 30


class TestCategoryPanel:
    def test_every_category_declares_subreddits_and_seeds(self):
        assert collectors.CATEGORIES, "the panel must not be empty"
        for name, spec in collectors.CATEGORIES.items():
            assert spec["subreddits"], f"{name} has no subreddits"
            assert spec["seeds"], f"{name} has no seed terms"

    def test_the_panel_is_broad_enough_to_be_worth_calling_a_panel(self):
        total = sum(len(s["subreddits"]) for s in collectors.CATEGORIES.values())
        assert total >= 25, f"only {total} subreddits configured"

    def test_subreddits_are_unique_across_categories(self):
        seen = [s for spec in collectors.CATEGORIES.values() for s in spec["subreddits"]]
        assert len(seen) == len(set(seen)), "a subreddit is listed twice"

    def test_selecting_categories_narrows_the_panel(self):
        everything = collectors.selected_categories(None)
        one = collectors.selected_categories(["health"])
        assert set(one) == {"health"}
        assert len(one) < len(everything)

    def test_unknown_category_names_are_ignored_not_fatal(self):
        assert collectors.selected_categories(["health", "astrology"]) == ["health"]


TRENDS_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>adhd in adults</title><ht:approx_traffic>2000+</ht:approx_traffic>
  <ht:news_item><ht:news_item_title>How to spot adult ADHD</ht:news_item_title></ht:news_item></item>
<item><title>djokovic</title><ht:approx_traffic>500+</ht:approx_traffic></item>
</channel></rss>"""


class TestGoogleTrends:
    def test_parses_items_with_their_traffic(self):
        fetch = fetcher(**{"trends.google.com": FakeResponse(200, TRENDS_RSS)})
        result = collectors.google_trends(fetch=fetch)
        assert result.status == "ok"
        assert result.items[0]["text"] == "adhd in adults"
        assert result.items[0]["intensity"] == 2000

    def test_traffic_suffix_is_parsed_as_a_number(self):
        fetch = fetcher(**{"trends.google.com": FakeResponse(200, TRENDS_RSS)})
        items = collectors.google_trends(fetch=fetch).items
        assert items[1]["intensity"] == 500

    def test_reports_unavailable_on_a_bad_status(self):
        fetch = fetcher(**{"trends.google.com": FakeResponse(500, "")})
        assert collectors.google_trends(fetch=fetch).status == "unavailable"

    def test_detail_warns_that_this_feed_is_news_shaped(self):
        fetch = fetcher(**{"trends.google.com": FakeResponse(200, TRENDS_RSS)})
        assert "news" in collectors.google_trends(fetch=fetch).detail.lower()


NEW_RELEASES_HTML = """
<a href="/dp/B0G42XDNL9"><img alt="Cross Check (D.C. Stars Book 6)"></a>
<a href="/dp/B0GKV84XWF"><img alt="The Shadow Friends: A Thriller"></a>
<a href="/dp/B0G42XDNL9"><img alt="Cross Check (D.C. Stars Book 6)"></a>
"""


class TestAmazonNewReleases:
    def test_extracts_asin_and_title_pairs(self):
        fetch = fetcher(**{"new-releases": FakeResponse(200, NEW_RELEASES_HTML)})
        result = collectors.amazon_new_releases(["health"], fetch=fetch)
        assert result.status == "ok"
        titles = [i["text"] for i in result.items]
        assert "Cross Check (D.C. Stars Book 6)" in titles

    def test_deduplicates_repeated_listings(self):
        fetch = fetcher(**{"new-releases": FakeResponse(200, NEW_RELEASES_HTML)})
        items = collectors.amazon_new_releases(["health"], fetch=fetch).items
        assert len({i["asin"] for i in items}) == len(items)

    def test_each_item_links_back_to_its_listing(self):
        fetch = fetcher(**{"new-releases": FakeResponse(200, NEW_RELEASES_HTML)})
        items = collectors.amazon_new_releases(["health"], fetch=fetch).items
        assert all(i["url"].startswith("https://www.amazon.com/dp/") for i in items)

    def test_unavailable_when_the_page_is_blocked(self):
        fetch = fetcher(**{"new-releases": FakeResponse(503, "")})
        assert collectors.amazon_new_releases(["health"], fetch=fetch).status == "unavailable"


REDDIT_OAUTH = json.dumps({"data": {"children": [
    {"data": {"title": "Is there a book about ADHD for newly diagnosed kids?",
              "subreddit": "ADHD", "score": 340, "num_comments": 52,
              "permalink": "/r/ADHD/comments/a/x/", "selftext": "my 8yo was just diagnosed"}},
    {"data": {"title": "Any recommendations for menopause nutrition?",
              "subreddit": "Menopause", "score": 88, "num_comments": 14,
              "permalink": "/r/Menopause/comments/b/y/", "selftext": ""}},
]}})


class TestRedditPanel:
    creds = {"client_id": "id", "client_secret": "secret"}

    def test_not_configured_without_credentials(self):
        result = collectors.reddit_panel("7d", None, fetch=fetcher(), credentials=None)
        assert result.status == "not_configured"
        assert "REDDIT_CLIENT_ID" in result.detail

    def test_harvests_posts_with_real_engagement(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(200, json.dumps({"access_token": "t"})),
            "oauth.reddit.com": FakeResponse(200, REDDIT_OAUTH),
        })
        result = collectors.reddit_panel("7d", ["health"], fetch=fetch, credentials=self.creds)
        assert result.status == "ok"
        assert result.items[0]["intensity"] == 340
        assert result.items[0]["comments"] == 52

    def test_carries_the_evidence_link_for_every_signal(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(200, json.dumps({"access_token": "t"})),
            "oauth.reddit.com": FakeResponse(200, REDDIT_OAUTH),
        })
        items = collectors.reddit_panel("7d", ["health"], fetch=fetch, credentials=self.creds).items
        assert all(i["url"].startswith("https://www.reddit.com/r/") for i in items)

    def test_requests_the_span_matching_the_window(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(200, json.dumps({"access_token": "t"})),
            "oauth.reddit.com": FakeResponse(200, REDDIT_OAUTH),
        })
        collectors.reddit_panel("24h", ["health"], fetch=fetch, credentials=self.creds)
        assert any("t=day" in u for u in fetch.calls)

    def test_unavailable_when_the_token_handshake_fails(self):
        fetch = fetcher(**{"access_token": FakeResponse(401, "nope")})
        result = collectors.reddit_panel("7d", ["health"], fetch=fetch, credentials=self.creds)
        assert result.status == "unavailable"


# Real markup captured from amazon.com/gp/new-releases/digital-text/154606011
# on 2026-08-31. The ASIN is followed by a /ref= path and the <img> sits
# inside a nested <div>, which the first parser did not allow for.
REAL_NEW_RELEASES = '''
<div id="B0G42XDNL9" class="p13n-sc-uncoverable-faceout"><a aria-hidden="true"
 class="a-link-normal aok-block" tabindex="-1"
 href="/Cross-Check-D-C-Stars-Book-ebook/dp/B0G42XDNL9/ref=zg_bsnr_g_154606011_d_sccl_1/131-057?psc=1">
 <div class="a-section a-spacing-mini _cDEzb_noop_3Xbw5">
 <img alt="Cross Check (D.C. Stars Book 6)" src="https://m.media-amazon.com/x.jpg"></div></a></div>
<div id="B0GKV84XWF" class="p13n-sc-uncoverable-faceout"><a
 href="/Shadow-Friends-Thriller-ebook/dp/B0GKV84XWF/ref=zg_bsnr_g_2">
 <div><img alt="The Shadow Friends: A Thriller" src="https://m.media-amazon.com/y.jpg"></div></a></div>
'''


class TestNewReleasesAgainstRealMarkup:
    def test_parses_the_markup_amazon_actually_serves(self):
        fetch = fetcher(**{"new-releases": FakeResponse(200, REAL_NEW_RELEASES)})
        result = collectors.amazon_new_releases(["health"], fetch=fetch)
        assert result.status == "ok", result.detail
        titles = [i["text"] for i in result.items]
        assert "Cross Check (D.C. Stars Book 6)" in titles
        assert "The Shadow Friends: A Thriller" in titles

    def test_asin_is_taken_from_the_dp_segment_not_the_ref_path(self):
        fetch = fetcher(**{"new-releases": FakeResponse(200, REAL_NEW_RELEASES)})
        asins = {i["asin"] for i in collectors.amazon_new_releases(["health"], fetch=fetch).items}
        assert asins == {"B0G42XDNL9", "B0GKV84XWF"}

    def test_does_not_run_titles_together_across_listings(self):
        fetch = fetcher(**{"new-releases": FakeResponse(200, REAL_NEW_RELEASES)})
        for item in collectors.amazon_new_releases(["health"], fetch=fetch).items:
            assert len(item["text"]) < 120, "greedy match swallowed the next listing"
