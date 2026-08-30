"""Tests for the pluggable signal-source layer.

Network is injected as a `fetch` callable so these run offline and
deterministically. The behaviour under test is the adapter logic:
which transport gets chosen, how responses are normalised, and — most
importantly — that a dead source reports itself dead instead of quietly
contributing zero signal.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.sources import base, openlibrary, reddit, registry


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self.body = body


def fetcher(**by_substring):
    """Build a fake fetch that dispatches on a substring of the URL."""
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        for needle, response in by_substring.items():
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return FakeResponse(404, "")

    fetch.calls = calls
    return fetch


REDDIT_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Best air fryer cookbook for beginners?</title>
    <link href="https://www.reddit.com/r/cooking/comments/abc/x/"/>
    <category term="cooking"/>
    <updated>2026-08-01T10:00:00+00:00</updated>
  </entry>
  <entry>
    <title>My air fryer recipes flopped, what am I doing wrong</title>
    <link href="https://www.reddit.com/r/AirFryer/comments/def/y/"/>
    <category term="AirFryer"/>
    <updated>2026-08-02T10:00:00+00:00</updated>
  </entry>
</feed>"""

REDDIT_OAUTH_JSON = json.dumps({
    "data": {"children": [
        {"data": {"title": "Air fryer cookbook recommendations", "subreddit": "cooking",
                  "score": 412, "num_comments": 88, "permalink": "/r/cooking/comments/abc/x/"}},
        {"data": {"title": "Air fryer for one person", "subreddit": "AirFryer",
                  "score": 55, "num_comments": 12, "permalink": "/r/AirFryer/comments/def/y/"}},
    ]}
})


class TestSourceResult:
    def test_a_source_with_items_is_usable(self):
        r = base.SourceResult(name="x", status="ok", items=[{"a": 1}], detail="")
        assert r.usable is True

    def test_an_ok_source_with_no_items_is_not_usable(self):
        r = base.SourceResult(name="x", status="ok", items=[], detail="")
        assert r.usable is False

    def test_an_unavailable_source_is_not_usable(self):
        r = base.SourceResult(name="x", status="unavailable", items=[{"a": 1}], detail="")
        assert r.usable is False

    def test_serialises_for_the_api(self):
        d = base.SourceResult(name="x", status="ok", items=[], detail="hi").as_dict()
        assert d["name"] == "x" and d["status"] == "ok" and d["detail"] == "hi"


class TestRedditWithoutCredentials:
    def test_falls_back_to_the_rss_transport(self):
        fetch = fetcher(**{"search.rss": FakeResponse(200, REDDIT_RSS)})
        result = reddit.collect("air fryer", fetch=fetch)
        assert result.status == "ok"
        assert any("search.rss" in u for u in fetch.calls)

    def test_parses_titles_and_subreddits_out_of_rss(self):
        fetch = fetcher(**{"search.rss": FakeResponse(200, REDDIT_RSS)})
        items = reddit.collect("air fryer", fetch=fetch).items
        assert len(items) == 2
        assert items[0]["title"] == "Best air fryer cookbook for beginners?"
        assert items[0]["subreddit"] == "cooking"
        assert items[0]["url"].startswith("https://www.reddit.com/")

    def test_rss_items_admit_they_have_no_engagement_numbers(self):
        fetch = fetcher(**{"search.rss": FakeResponse(200, REDDIT_RSS)})
        items = reddit.collect("air fryer", fetch=fetch).items
        assert items[0]["score"] is None
        assert items[0]["comments"] is None

    def test_reports_unavailable_rather_than_empty_when_blocked(self):
        fetch = fetcher(**{"search.rss": FakeResponse(403, "blocked")})
        result = reddit.collect("air fryer", fetch=fetch)
        assert result.status == "unavailable"
        assert result.items == []
        assert "403" in result.detail

    def test_reports_unavailable_when_rate_limited(self):
        fetch = fetcher(**{"search.rss": FakeResponse(429, "slow down")})
        result = reddit.collect("air fryer", fetch=fetch, sleep=lambda _: None)
        assert result.status == "unavailable"
        assert "429" in result.detail

    def test_a_transport_exception_does_not_escape(self):
        fetch = fetcher(**{"search.rss": RuntimeError("connection reset")})
        result = reddit.collect("air fryer", fetch=fetch)
        assert result.status == "unavailable"


class TestRedditWithCredentials:
    creds = {"client_id": "id", "client_secret": "secret"}

    def test_prefers_the_official_api_when_credentials_exist(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(200, json.dumps({"access_token": "tok"})),
            "oauth.reddit.com": FakeResponse(200, REDDIT_OAUTH_JSON),
        })
        result = reddit.collect("air fryer", fetch=fetch, credentials=self.creds)
        assert result.status == "ok"
        assert any("oauth.reddit.com" in u for u in fetch.calls)
        assert not any("search.rss" in u for u in fetch.calls)

    def test_official_api_carries_real_engagement_numbers(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(200, json.dumps({"access_token": "tok"})),
            "oauth.reddit.com": FakeResponse(200, REDDIT_OAUTH_JSON),
        })
        items = reddit.collect("air fryer", fetch=fetch, credentials=self.creds).items
        assert items[0]["score"] == 412
        assert items[0]["comments"] == 88

    def test_results_are_ranked_by_engagement(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(200, json.dumps({"access_token": "tok"})),
            "oauth.reddit.com": FakeResponse(200, REDDIT_OAUTH_JSON),
        })
        items = reddit.collect("air fryer", fetch=fetch, credentials=self.creds).items
        assert [i["score"] for i in items] == sorted([i["score"] for i in items], reverse=True)

    def test_falls_back_to_rss_when_the_token_call_fails(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(401, "bad creds"),
            "search.rss": FakeResponse(200, REDDIT_RSS),
        })
        result = reddit.collect("air fryer", fetch=fetch, credentials=self.creds)
        assert result.status == "ok"
        assert result.transport == "rss"

    def test_records_which_transport_produced_the_data(self):
        fetch = fetcher(**{
            "access_token": FakeResponse(200, json.dumps({"access_token": "tok"})),
            "oauth.reddit.com": FakeResponse(200, REDDIT_OAUTH_JSON),
        })
        assert reddit.collect("air fryer", fetch=fetch, credentials=self.creds).transport == "oauth"


class TestOpenLibrary:
    payload = json.dumps({
        "numFound": 2979,
        "docs": [
            {"title": "The Complete Air Fryer Cookbook", "first_publish_year": 2016,
             "edition_count": 3, "want_to_read_count": 5},
            {"title": "Air Fryer Cookbook", "first_publish_year": 2021,
             "edition_count": 1, "want_to_read_count": 2},
        ],
    })

    def test_reports_catalogue_supply(self):
        fetch = fetcher(**{"openlibrary.org": FakeResponse(200, self.payload)})
        result = openlibrary.collect("air fryer cookbook", fetch=fetch)
        assert result.status == "ok"
        assert result.meta["total_works"] == 2979

    def test_extracts_publication_years_for_saturation(self):
        fetch = fetcher(**{"openlibrary.org": FakeResponse(200, self.payload)})
        result = openlibrary.collect("air fryer cookbook", fetch=fetch)
        assert result.meta["oldest_year"] == 2016
        assert result.meta["newest_year"] == 2021

    def test_unavailable_on_error_status(self):
        fetch = fetcher(**{"openlibrary.org": FakeResponse(500, "boom")})
        assert openlibrary.collect("x", fetch=fetch).status == "unavailable"


class TestExcludedSources:
    def test_x_twitter_is_excluded_with_a_reason(self):
        r = registry.excluded_sources()["x_twitter"]
        assert r.status == "excluded"
        assert r.items == []
        assert "javascript" in r.detail.lower() or "log" in r.detail.lower()

    def test_tiktok_is_excluded_with_a_reason(self):
        r = registry.excluded_sources()["tiktok"]
        assert r.status == "excluded"
        assert r.items == []
        assert r.detail

    def test_excluded_sources_are_never_usable(self):
        for r in registry.excluded_sources().values():
            assert r.usable is False


class TestRegistryIsolation:
    def test_one_exploding_source_does_not_kill_the_others(self):
        def good(topic, **kw):
            return base.SourceResult(name="good", status="ok", items=[{"x": 1}], detail="")

        def bad(topic, **kw):
            raise RuntimeError("kaboom")

        results = registry.run_sources({"good": good, "bad": bad}, "air fryer")
        assert results["good"].status == "ok"
        assert results["bad"].status == "unavailable"
        assert "kaboom" in results["bad"].detail

    def test_health_summary_counts_each_status(self):
        results = {
            "a": base.SourceResult(name="a", status="ok", items=[{"x": 1}], detail=""),
            "b": base.SourceResult(name="b", status="unavailable", items=[], detail=""),
            "c": base.SourceResult(name="c", status="excluded", items=[], detail=""),
        }
        health = registry.health(results)
        assert health["ok"] == 1
        assert health["unavailable"] == 1
        assert health["excluded"] == 1
        assert health["usable_sources"] == ["a"]


class TestRedditRateLimitHandling:
    """429 is temporary and worth retrying; 403 is a policy wall and is not."""

    def test_retries_after_a_rate_limit_and_succeeds(self):
        responses = [FakeResponse(429, ""), FakeResponse(200, REDDIT_RSS)]
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            return responses.pop(0) if responses else FakeResponse(200, REDDIT_RSS)

        slept = []
        result = reddit.collect("air fryer", fetch=fetch, sleep=slept.append)
        assert result.status == "ok"
        assert slept, "should have backed off before retrying"

    def test_gives_up_after_the_retry_budget(self):
        fetch = fetcher(**{"search.rss": FakeResponse(429, "")})
        slept = []
        result = reddit.collect("air fryer", fetch=fetch, retries=2, sleep=slept.append)
        assert result.status == "unavailable"
        assert len(slept) <= 4, "must not retry forever"

    def test_does_not_retry_a_403_policy_block(self):
        fetch = fetcher(**{"search.rss": FakeResponse(403, "")})
        slept = []
        reddit.collect("air fryer", fetch=fetch, retries=3, sleep=slept.append)
        assert slept == [], "403 is not transient; retrying only burns quota"

    def test_backoff_grows_between_attempts(self):
        fetch = fetcher(**{"search.rss": FakeResponse(429, "")})
        slept = []
        reddit.collect("air fryer", fetch=fetch, retries=3, sleep=slept.append)
        assert slept == sorted(slept), "delays should not shrink"


class TestQueryEncoding:
    """Raw quotes in a query string make Reddit answer 403. Encode properly."""

    def test_reddit_percent_encodes_quotes(self):
        fetch = fetcher(**{"search.rss": FakeResponse(200, REDDIT_RSS)})
        reddit.collect("air fryer", fetch=fetch)
        assert fetch.calls, "expected at least one request"
        for url in fetch.calls:
            assert '"' not in url, f"raw quote leaked into the URL: {url}"
        assert any("%22" in url for url in fetch.calls)

    def test_reddit_encodes_ampersands_in_the_topic(self):
        fetch = fetcher(**{"search.rss": FakeResponse(200, REDDIT_RSS)})
        reddit.collect("cooking & baking", fetch=fetch)
        query_parts = [url.split("q=")[1].split("&sort")[0] for url in fetch.calls]
        assert all("%26" in q for q in query_parts), \
            "an unencoded & would truncate the query server-side"

    def test_autocomplete_encodes_special_characters(self):
        from server.sources import autocomplete
        fetch = fetcher(**{"suggestqueries": FakeResponse(200, '["x",["a","b"]]')})
        autocomplete.google("c++ & rust", fetch=fetch)
        for url in fetch.calls:
            tail = url.split("&q=")[1]
            assert "+" not in tail.replace("%2B", "") or "%" in tail
            assert "&" not in tail, f"unencoded & truncates the query: {url}"
