"""Runs the configured sources, isolates their failures, reports their health.

Two jobs. First, no single source may take down a scan — a blocked host
returns `unavailable` and the rest carry on. Second, sources we deliberately
do not attempt are reported as `excluded` with the evidence, so the absence
of BookTok in a report reads as a measured decision rather than an oversight.
"""

import os
from typing import Any, Callable, Optional

from scrapling.fetchers import Fetcher

from . import autocomplete, base, openlibrary, reddit

# Probed 2026-08-31 from a residential IP, including through Scrapling's
# Camoufox stealth browser. Recorded here so the reasoning travels with the
# code and nobody re-litigates it from memory.
EXCLUDED: dict[str, str] = {
    "x_twitter": (
        "Logged-out X serves only a JavaScript wall — confirmed with a stealth "
        "browser, which received the same 'JavaScript is not available' page. "
        "Access needs a logged-in account (ToS/ban risk) or X Basic at $200/mo. "
        "Excluded rather than faked."
    ),
    "tiktok": (
        "TikTok's hashtag page returns HTTP 200 to a stealth browser but the "
        "document is an empty shell: zero videos, zero view counts. Real data "
        "comes from signed API calls (X-Bogus/msToken), and the Creative Center "
        "API answers {\"code\":40101,\"msg\":\"no permission\"} without a session. "
        "BookTok needs a paid provider or ToS-violating signature scraping; "
        "neither is worth fabricating a signal over."
    ),
}


def excluded_sources() -> dict[str, base.SourceResult]:
    return {name: base.excluded(name, reason) for name, reason in EXCLUDED.items()}


# Measured: Reddit returns a deterministic 403 to the "edge" TLS fingerprint
# but serves the same RSS URL to "chrome". Autocomplete is indifferent.
REDDIT_IMPERSONATE = "chrome"


def default_fetch(impersonate: str = "edge", timeout: int = 20) -> Callable:
    """Real HTTP transport, shaped like the callable sources expect."""

    def fetch(url: str, method: str = "GET", **kwargs: Any) -> Any:
        kwargs.setdefault("impersonate", impersonate)
        kwargs.setdefault("stealthy_headers", False)
        kwargs.setdefault("timeout", timeout)
        if method.upper() == "POST":
            return Fetcher.post(url, **kwargs)
        kwargs.pop("auth", None)
        kwargs.pop("data", None)
        return Fetcher.get(url, **kwargs)

    return fetch


def reddit_credentials() -> Optional[dict[str, str]]:
    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret}
    return None


def run_sources(sources: dict[str, Callable], topic: str,
                **kwargs: Any) -> dict[str, base.SourceResult]:
    """Call each source, converting any escape into an `unavailable` result."""
    results: dict[str, base.SourceResult] = {}
    for name, source in sources.items():
        try:
            results[name] = source(topic, **kwargs)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            results[name] = base.unavailable(name, f"source raised {type(exc).__name__}: {exc}")
    return results


def collect_all(topic: str, *, fetch: Optional[Callable] = None,
                impersonate: str = "edge") -> dict[str, base.SourceResult]:
    """Every live source, plus the documented exclusions."""
    fetch = fetch or default_fetch(impersonate)
    credentials = reddit_credentials()

    results = run_sources(
        {
            "google": lambda t, **kw: autocomplete.google(t, fetch=fetch),
            "youtube": lambda t, **kw: autocomplete.youtube(t, fetch=fetch),
            "reddit": lambda t, **kw: reddit.collect(
                t, fetch=default_fetch(REDDIT_IMPERSONATE), credentials=credentials),
            "openlibrary": lambda t, **kw: openlibrary.collect(t, fetch=fetch),
        },
        topic,
    )
    if not credentials and results["reddit"].status == "ok":
        results["reddit"] = base.SourceResult(
            name="reddit", status="ok", items=results["reddit"].items,
            transport=results["reddit"].transport,
            detail=results["reddit"].detail + " — set REDDIT_CLIENT_ID/SECRET for scores and comment counts",
            evidence_url=results["reddit"].evidence_url, meta=results["reddit"].meta,
        )
    results.update(excluded_sources())
    return results


def health(results: dict[str, base.SourceResult]) -> dict[str, Any]:
    """A panel the dashboard can render: what answered, what did not, and why."""
    counts: dict[str, int] = {}
    for result in results.values():
        counts[result.status] = counts.get(result.status, 0) + 1
    for status in base.STATUSES:
        counts.setdefault(status, 0)
    return {
        **counts,
        "usable_sources": sorted(n for n, r in results.items() if r.usable),
        "degraded": sorted(n for n, r in results.items()
                           if r.status in ("unavailable", "not_configured")),
        "notes": {n: r.detail for n, r in results.items() if r.status != "ok"},
    }
