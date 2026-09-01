"""Discovery Mode — "what book do people want that doesn't exist yet?"

You supply a time window and optionally a category. No seed keyword. The
pipeline harvests demand, normalises it into book concepts, checks each one
against Amazon's actual shelf, and returns ranked opportunity cards.

    Stage 1  harvest      seedless collectors, failures isolated
    Stage 2  concepts     signals -> book concepts, evidence links kept
    Stage 3  demand       breadth x intensity; single-source demoted
    Stage 4  supply gap   demand / what Amazon already sells
    Stage 5  cards        ranked, each with a hand-off to full validation

Every observation is written to the history store as it goes, so momentum
("this concept has grown every scan for nine days") becomes available once
scans accumulate. Momentum is deliberately not computed from a single run:
one data point is not a trend, and pretending otherwise is the kind of
confident wrongness this project exists to avoid.
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from typing import Any, Callable, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from . import collectors, concepts as concepts_mod, scoring
try:  # pragma: no cover - the app runs flat (--app-dir server), tests as a package
    from ..sources import base, registry
except ImportError:  # pragma: no cover
    from sources import base, registry

ProgressFn = Callable[[str, int, str], None]

# A concept must clear this to be worth an Amazon request.
MIN_DEMAND_TO_VALIDATE = 12.0

# Seconds any one collector may take before the scan moves on without it.
DEFAULT_COLLECTOR_TIMEOUT = 75.0


def _default_collectors(credentials: Optional[dict]) -> dict[str, Callable]:
    fetch = registry.default_fetch("edge")
    reddit_fetch = registry.default_fetch(registry.REDDIT_IMPERSONATE)
    return {
        "reddit_panel": lambda window, categories, **kw: collectors.reddit_panel(
            window, categories, fetch=reddit_fetch, credentials=credentials),
        "youtube": lambda window, categories, **kw: collectors.youtube_rising(
            categories, fetch=fetch),
        "amazon_new_releases": lambda window, categories, **kw: collectors.amazon_new_releases(
            categories, fetch=fetch),
        "google_trends": lambda window, categories, **kw: collectors.google_trends(fetch=fetch),
    }


def _harvest(collectors_map: dict[str, Callable], window: str,
             categories: Optional[list[str]],
             timeout: float = DEFAULT_COLLECTOR_TIMEOUT) -> dict[str, base.SourceResult]:
    """Run every collector at once, bounding each one.

    Sequentially this stage sat at 8% for minutes, and a single stalled host
    held the entire scan. Collectors are independent, so they fan out; one
    that overruns its budget is reported `unavailable` and the scan proceeds
    with what did answer.
    """
    if not collectors_map:
        return {}

    results: dict[str, base.SourceResult] = {}
    # Deliberately not a `with` block: the context manager joins every worker
    # on exit, so a stalled collector would still hold the scan for its full
    # runtime even after we gave up waiting on it.
    pool = ThreadPoolExecutor(max_workers=min(8, len(collectors_map)))
    try:
        futures = {
            name: pool.submit(collector, window=window, categories=categories)
            for name, collector in collectors_map.items()
        }
        deadline = time.monotonic() + timeout
        for name, future in futures.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results[name] = future.result(timeout=remaining)
            except FutureTimeout:
                results[name] = base.unavailable(
                    name, f"timed out after {timeout:g}s and was skipped so the "
                          f"scan could finish; the other sources still ran")
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                results[name] = base.unavailable(
                    name, f"collector raised {type(exc).__name__}: {exc}")
    finally:
        # Abandon anything still running rather than blocking on it. The
        # orphaned request finishes into a discarded result.
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _gemini_caller() -> Optional[Callable[[str], str]]:
    """Reuse the brief's provider resolution so one key powers both features."""
    try:
        from .. import brief as brief_mod
    except (ImportError, ValueError):  # pragma: no cover - flat import shape
        import brief as brief_mod  # type: ignore
    if brief_mod.active_provider() is None:
        return None
    # Structured extraction needs headroom: the thinking budget is spent
    # from the same allowance, and a truncated array costs a whole batch.
    return lambda prompt: brief_mod.call_llm(prompt, max_output_tokens=8192)


def run_discovery(params: dict[str, Any], progress: ProgressFn,
                  collectors_map: Optional[dict[str, Callable]] = None,
                  validator: Optional[Callable[[list[str]], dict]] = None,
                  llm: Optional[Callable[[str], str]] = "auto",
                  store: Optional[Any] = None) -> dict[str, Any]:
    """Blocking; call from a worker thread. Returns the discovery bundle."""
    window: str = params.get("window", "7d")
    collectors.reddit_span(window)          # validates the window, raises if unknown
    categories = collectors.selected_categories(params.get("categories"))
    validate_top = int(params.get("validate_top", 8))
    collector_timeout = float(params.get("collector_timeout", DEFAULT_COLLECTOR_TIMEOUT))
    warnings: list[str] = []

    # --- Stage 1 ---------------------------------------------------------
    progress("harvest", 8, f"Harvesting demand signals from the last {window}")
    if collectors_map is None:
        collectors_map = _default_collectors(registry.reddit_credentials())
    sources = _harvest(collectors_map, window, categories, collector_timeout)
    health = registry.health(sources)
    for name in health["degraded"]:
        warnings.append(f"{name}: {sources[name].detail}")

    signals: list[dict[str, Any]] = []
    for name, result in sources.items():
        if result.usable:
            signals.extend(result.items)
    progress("harvest", 28, f"{len(signals)} raw signals from {health['ok']} source(s)")

    # --- Stage 2 ---------------------------------------------------------
    if llm == "auto":
        llm = _gemini_caller()
    progress("concepts", 40, "Normalising signals into book concepts")
    found, extraction = concepts_mod.extract_with_report(signals, llm=llm)
    if extraction.get('method') == 'heuristic' and extraction.get('reason'):
        warnings.append(
            f"Concept extraction fell back to keyword grouping: {extraction['reason']}. "
            f"Concepts will be rougher than the model would produce.")
    if not found:
        progress("done", 100, "No book-shaped demand found in this window")
        return _bundle(window, categories, sources, health, [], warnings +
                       ["No signal in this window looked like demand for a book. "
                        "Try a longer window or a different category."])

    # --- Stage 3 ---------------------------------------------------------
    progress("score", 55, f"Scoring demand for {len(found)} concepts")
    for concept in found:
        concept["demand"] = scoring.demand(concept)
        concept["corroborated"] = scoring.is_corroborated(concept)
    found.sort(key=lambda c: -c["demand"])

    shortlist = [c for c in found if c["demand"] >= MIN_DEMAND_TO_VALIDATE][:validate_top]
    if not shortlist:
        progress("done", 100, "Nothing cleared the demand floor")
        return _bundle(window, categories, sources, health, [], warnings +
                       [f"{len(found)} concepts were found but none reached the demand "
                        f"floor of {MIN_DEMAND_TO_VALIDATE}. They are usually one-off posts."])

    # --- Stage 4 ---------------------------------------------------------
    progress("amazon", 68,
             f"Checking {len(shortlist)} concepts against the live Amazon shelf")
    # Search Amazon for what a buyer would type, not the concept sentence —
    # a 150-character description returns no usable results page.
    for concept in shortlist:
        concept.setdefault("search_phrase",
                           concepts_mod.derive_search_phrase(concept["concept"]))
    phrases = [c["search_phrase"] for c in shortlist]
    try:
        supply = (validator or _amazon_validator)(phrases)
    except Exception as exc:  # noqa: BLE001
        supply = {}
        warnings.append(f"Amazon supply check failed: {type(exc).__name__}: {exc}")

    # --- Stage 5 ---------------------------------------------------------
    progress("cards", 88, "Ranking opportunities")
    cards: list[dict[str, Any]] = []
    for concept in shortlist:
        metrics = supply.get(concept["search_phrase"], {}) or {}
        card_gap = scoring.gap(concept["demand"], metrics)
        cards.append({
            "concept": concept["concept"],
            "search_phrase": concept["search_phrase"],
            "audience": concept.get("audience"),
            "category": concept.get("category"),
            "method": concept.get("method"),
            "demand": concept["demand"],
            "corroborated": concept["corroborated"],
            "sources": sorted(scoring.distinct_sources(concept)),
            "evidence": concept["evidence"][:6],
            "gap": card_gap,
            "amazon_url": metrics.get("url"),
            # what the "Run full validation" button hands to the research
            # pipeline — the typeable phrase, never the concept sentence,
            # which exceeded the research API's seed length in a live run
            "validate_phrase": concept["search_phrase"],
        })
    cards.sort(key=lambda c: (c["gap"]["score"] is None, -(c["gap"]["score"] or 0)))

    if store is not None:
        try:
            store.record(window, cards)
        except Exception as exc:  # noqa: BLE001 - history is a nice-to-have
            warnings.append(f"history not recorded: {exc}")

    progress("done", 100, f"{len(cards)} opportunities ranked")
    return _bundle(window, categories, sources, health, cards, warnings,
                   concepts_found=len(found), extraction=extraction)


def _bundle(window, categories, sources, health, cards, warnings,
            concepts_found: int = 0,
            extraction: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "window": window,
        "categories": categories,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {n: r.as_dict() for n, r in sources.items()},
        "source_health": health,
        "concepts_found": concepts_found,
        "extraction": extraction or {},
        "cards": cards,
        "warnings": warnings,
        "momentum_note": ("Momentum needs history. This run was stored; once a few "
                          "scans accumulate, concepts will show whether they are "
                          "growing rather than just present."),
    }


def _amazon_validator(phrases: list[str]) -> dict[str, dict[str, Any]]:
    """Real supply check through the existing long-tail validator."""
    from kdp_longtail_finder import STORES, LongTailSpider

    spider = LongTailSpider(
        candidates={p: i for i, p in enumerate(phrases)},
        store=STORES["kindle"], marketplace="us",
        impersonate="edge", stealthy_headers=True,
    )
    spider.start()
    return {m.keyword: m.model_dump() for m in spider.results}
