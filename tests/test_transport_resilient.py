from types import SimpleNamespace

from server import transport


class TestResilientGet:
    """Teardown on Railway reported 'HTTP 503, 1,800 bytes' for a page the
    deep-dive spider read fine seconds later. One fingerprint is not a verdict."""

    def test_rotates_fingerprint_after_a_decoy(self):
        calls = []
        def fetcher(url, impersonate, stealthy_headers, timeout):
            calls.append((impersonate, stealthy_headers))
            if len(calls) == 1:
                return SimpleNamespace(status=503, body=b"<html>decoy</html>")
            return SimpleNamespace(status=200, body=b"x" * 6000)
        r = transport.resilient_get("https://www.amazon.com/dp/B000000000", fetcher=fetcher, pause=0)
        assert r.status == 200
        assert calls == [("edge", True), ("chrome", False)]

    def test_tiny_200_is_not_a_page(self):
        seen = []
        def fetcher(url, **kw):
            seen.append(kw["impersonate"])
            return SimpleNamespace(status=200, body=b"<html></html>")
        r = transport.resilient_get("u", fetcher=fetcher, pause=0)
        assert seen == ["edge", "chrome", "firefox"] and r.status == 200

    def test_exception_then_success(self):
        n = {"i": 0}
        def fetcher(url, **kw):
            n["i"] += 1
            if n["i"] == 1:
                raise ConnectionError("reset")
            return SimpleNamespace(status=200, body=b"productTitle")
        assert transport.resilient_get("u", fetcher=fetcher, pause=0).status == 200
