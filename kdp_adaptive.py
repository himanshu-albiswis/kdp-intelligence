"""Selector resilience — keeping the scrapers working when Amazon redesigns.

The project's honest limitation has always been this line in
`kdp_niche_validator.py`:

    response.css('div[data-component-type="s-search-result"]')

The day Amazon renames that attribute, every scan reports "No usable results
page (soft block?)" — which reads exactly like an IP block. An operator would
go buy residential proxies to fix what is actually a one-word selector change.
We already watched a version of that confusion play out with the chrome/stealth
503, and the fix cost hours of probing.

Scrapling can save an element's fingerprint (tag, text, attributes, siblings,
path, and the parent's) on a successful parse, then relocate it later by
similarity when the selector stops matching. This module wraps that in the
shape the rest of the codebase wants:

  * `select()` returns `(elements, how)` where `how` is "direct", "adaptive",
    or "missing". A relocation is never silent — a scraper running on
    similarity-matched elements is a scraper that needs maintenance soon, and
    hiding that would trade one silent failure for another.
  * "missing" and "adaptive" are distinguishable, so a blocked page and a
    redesigned page can never be confused again.
  * Any failure in the adaptive machinery degrades to a plain selection.
    Element relocation is a safety net, not a dependency.

The save phase runs on every successful parse, so fingerprints accumulate
while things work and are waiting the day they break.
"""

import os
from typing import Any, Optional

from scrapling.fetchers import Fetcher, StealthyFetcher

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "adaptive.db")

_storage_path: Optional[str] = None


def storage_path() -> Optional[str]:
    return _storage_path


def configure(db_path: str = DEFAULT_DB) -> None:
    """Point Scrapling's adaptive storage at our data directory.

    Left to itself the library writes inside the installed package, which does
    not survive a reinstall and is invisible to anyone reading the repo. The
    storage file has to be passed at construction, so `selector_kwargs()`
    below is what callers spread into `Selector(...)` / fetcher config.
    """
    global _storage_path
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    _storage_path = db_path
    for fetcher in (Fetcher, StealthyFetcher):
        try:
            fetcher.configure(adaptive=True, storage_args={"storage_file": db_path})
        except Exception:  # noqa: BLE001 - configuration must never be fatal
            try:
                fetcher.adaptive = True
            except Exception:  # noqa: BLE001
                pass


def selector_kwargs() -> dict[str, Any]:
    """Spread into `Selector(...)` so fingerprints land in our database."""
    kwargs: dict[str, Any] = {"adaptive": True}
    if _storage_path:
        kwargs["storage_args"] = {"storage_file": _storage_path}
    return kwargs


def _adaptive_retry(page: Any, selector: str, identifier: Optional[str]) -> list:
    """Ask Scrapling to relocate the element by similarity."""
    kwargs: dict[str, Any] = {"adaptive": True}
    if identifier:
        kwargs["identifier"] = identifier
    return list(page.css(selector, **kwargs) or [])


def select(page: Any, selector: str, *,
           identifier: Optional[str] = None) -> tuple[list, str]:
    """Select elements, relocating them if the markup moved.

    Returns `(elements, how)`. `how` is one of:
      direct   — the selector matched; the element was re-learned
      adaptive — the selector failed and the element was found by similarity
      missing  — nothing matched, adaptively or otherwise
    """
    save_kwargs: dict[str, Any] = {"auto_save": True}
    if identifier:
        save_kwargs["identifier"] = identifier

    try:
        found = list(page.css(selector, **save_kwargs) or [])
    except Exception:  # noqa: BLE001 - auto_save needs adaptive enabled upstream
        found = list(page.css(selector) or [])

    if found:
        return found, "direct"

    try:
        relocated = _adaptive_retry(page, selector, identifier)
    except Exception:  # noqa: BLE001 - the net is optional, the scrape is not
        relocated = []

    if relocated:
        return relocated, "adaptive"
    return [], "missing"


def explain(how: str, selector: str) -> Optional[str]:
    """A warning worth surfacing, or None when nothing needs saying."""
    if how == "adaptive":
        return (f"Amazon's markup changed: `{selector}` no longer matches, and the "
                f"element was recovered by similarity instead. The data is usable, "
                f"but update the selector before the similarity match drifts too.")
    return None
