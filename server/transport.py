"""Amazon transport defaults, chosen from measurement rather than folklore.

Live A/B on 2026-08-31, three consecutive trials per combination, against
`https://www.amazon.com/s?k=adhd+for+beginners&i=digital-text`:

| fingerprint | stealth headers | result                          |
|-------------|-----------------|---------------------------------|
| chrome      | on              | **HTTP 503, 2 KB decoy, 0 cards** (3/3) |
| chrome      | off             | HTTP 200, ~850 KB, 16 cards     |
| edge        | on              | HTTP 200, ~920 KB, 16 cards     |
| edge        | off             | HTTP 200, ~863 KB, 22 cards     |
| safari      | off             | HTTP 200, ~940 KB, 16 cards     |

The shipped default was chrome + stealth headers — the one pairing Amazon
refuses — so every default-settings run returned a decoy page. The scan
guard correctly refused to score it, which is why users saw "blocked"
warnings and zeroed KPIs rather than invented numbers.

Note what the table rules out: stealth headers are *not* the problem on
their own (edge tolerates them and returns the largest pages). It is the
chrome TLS fingerprint combined with them. Fixing this by globally
disabling stealth headers would have worked by accident while pointing at
the wrong cause.
"""

from typing import Any, Optional

DEFAULT_IMPERSONATE = "edge"

# Fingerprints that Amazon 503s when Scrapling's stealth headers are also
# sent. Keep this as data: it is a measured blocklist, not a rule of thumb.
STEALTH_INCOMPATIBLE = {"chrome"}

# A real Amazon search page is ~800 KB–1.5 MB. Amazon answers bots with a
# small shell — sometimes 503, sometimes a 200 that merely looks fine.
SOFT_BLOCK_MAX_BYTES = 50_000


def resolve(params: dict[str, Any]) -> tuple[str, bool, list[str]]:
    """Pick (impersonate, stealthy_headers, notes) for an Amazon session.

    Explicit user choices are honoured, with one exception: the measured
    dead combination is downgraded rather than silently failing, and the
    downgrade is explained in `notes` so it never looks like magic.
    """
    impersonate: str = params.get("impersonate") or DEFAULT_IMPERSONATE
    stealthy: bool = not params.get("plain_headers", False)
    notes: list[str] = []

    if stealthy and impersonate in STEALTH_INCOMPATIBLE:
        stealthy = False
        notes.append(
            f"Stealth headers were disabled for the {impersonate} fingerprint: "
            f"that pairing returns an HTTP 503 decoy page from Amazon in every "
            f"measured trial. Pick 'edge' to keep stealth headers on."
        )

    return impersonate, stealthy, notes


def looks_like_soft_block(response_bytes: Optional[int]) -> bool:
    """True when a page is far too small to be a real search result page."""
    if response_bytes is None:
        return False
    return response_bytes < SOFT_BLOCK_MAX_BYTES


def block_advice(impersonate: str, stealthy: bool) -> str:
    """What to actually try, cheapest and most likely first.

    The previous message told users to configure a residential proxy, which
    is expensive advice for what was usually a fingerprint setting.
    """
    steps = []
    if impersonate in STEALTH_INCOMPATIBLE:
        # Only sensible when the fingerprint is actually the problem; telling
        # an edge user to "switch from edge to edge" happened in a live block.
        steps.append(
            f"switch the fingerprint from {impersonate} to 'edge' (the {impersonate} "
            f"fingerprint with stealth headers is refused by Amazon)"
        )
    if stealthy:
        steps.append("tick 'Plain headers' to drop the faked Google referer")
    steps.append("try the 'safari' fingerprint")
    steps.append("only then add a residential proxy — datacenter IPs are blocked outright")
    ordered = "; ".join(f"{i}) {step}" for i, step in enumerate(steps, 1))
    return f"Amazon returned a decoy page instead of results. Try, in order: {ordered}."


def resilient_get(url: str, timeout: int = 30, fetcher: Any = None, pause: float = 1.5) -> Any:
    """Fetch one Amazon page, rotating fingerprints on a decoy or tiny page.

    Measured on Railway: a plain /dp/ GET with edge+stealth can come back as
    a 503 decoy or a 2 KB shell on the first try while the same page reads
    fine a moment later. Try edge+stealth, then chrome with plain headers,
    then firefox+stealth; return the first real page, else the last response.
    """
    import time
    if fetcher is None:  # pragma: no cover - live import
        from scrapling.fetchers import Fetcher
        fetcher = Fetcher.get
    attempts = [("edge", True), ("chrome", False), ("firefox", True)]
    last = None
    for i, (impersonate, stealthy) in enumerate(attempts):
        if i:
            time.sleep(pause)
        try:
            response = fetcher(url, impersonate=impersonate, stealthy_headers=stealthy, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - try the next fingerprint
            last = exc
            continue
        body = getattr(response, "body", b"") or b""
        if getattr(response, "status", 0) == 200 and (len(body) >= 5_000 or b"productTitle" in (body if isinstance(body, bytes) else body.encode())):
            return response
        last = response
    if isinstance(last, Exception):
        raise last
    return last
