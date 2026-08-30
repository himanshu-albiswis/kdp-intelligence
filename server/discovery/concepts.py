"""Stage 2 — turn raw signals into normalised book concepts, with receipts.

A harvest returns sentences people actually wrote ("my 8yo was just diagnosed
and I have no idea what to do"). Those are not book titles. This module
normalises them into concepts ("ADHD parenting guide for newly diagnosed
kids"), clusters duplicates across sources, and tags audience and category.

Two rules hold regardless of which path produced a concept:

  * Every concept keeps links to the exact signals behind it. A concept with
    no surviving evidence is dropped, never shown. If the model invents a
    signal index, it is discarded rather than fabricating a citation.
  * The LLM is an optimisation, not a dependency. Without a key — or with a
    model that returns something unparseable — a deterministic grouping runs
    instead and the concept records `method: "heuristic"` so you always know
    which produced it.

A cheap deterministic filter runs first so tokens are only spent on signals
that could plausibly become a book. It exists because the Google Trends feed
is news-shaped: on the day this was written it offered "rays" and "djokovic".
"""

import json
import re
from collections import defaultdict
from typing import Any, Callable, Optional

# Someone describing a need, a struggle, or a request for a book.
_INTENT = re.compile(
    r"\b(is there a book|any book|book recommend|recommend(ations)? for|how (do|to) i?\s*\w+|"
    r"where do i start|how to|learn(ing)? to|beginner|guide|wish there was|"
    r"just (been )?diagnosed|struggling with|help with|tips for|no idea|"
    r"resources for|looking for)\b", re.I)

_MIN_WORDS = 3

PROMPT = """You turn messy public signals into book concepts for a KDP publisher.

Rules:
- Output ONLY a JSON array. No prose, no code fences.
- Each element: {"concept": str, "audience": str, "category": str, "signal_indexes": [int]}
- "concept" is a specific book someone could actually write, phrased as a
  subject, not a title. Good: "ADHD parenting guide for newly diagnosed kids".
  Bad: "ADHD" (too broad), "The ADHD Bible" (a title, not a subject).
- Merge signals expressing the same underlying need into ONE concept and list
  every contributing index.
- Only cite indexes that exist in the input. Never invent one.
- Skip signals that are news, celebrities, sports, or products rather than
  something a book could teach.

Signals:
"""


def has_book_intent(signal: dict[str, Any]) -> bool:
    """Cheap pre-filter: could this plausibly become a book?"""
    text = (signal.get("text") or "").strip()
    if len(text.split()) < _MIN_WORDS:
        return False
    body = f"{text} {signal.get('body') or ''}"
    if signal.get("asking"):
        return True
    # New-release titles are supply, not a request, but they still describe a
    # subject someone published into — useful as corroboration.
    if signal.get("source") == "amazon_new_releases":
        return True
    return bool(_INTENT.search(body))


def _evidence_from(signals: list[dict[str, Any]], indexes: list[int]) -> list[dict[str, Any]]:
    evidence = []
    for index in indexes:
        if not isinstance(index, int) or not (0 <= index < len(signals)):
            continue  # the model invented an index; drop it rather than cite nothing
        s = signals[index]
        evidence.append({"text": s.get("text"), "url": s.get("url"),
                         "source": s.get("source"), "intensity": s.get("intensity"),
                         "subreddit": s.get("subreddit")})
    return evidence


def _heuristic(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by shared salient words. Crude, but honest and always available."""
    stop = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "my",
            "i", "is", "it", "with", "how", "do", "any", "book", "books", "that",
            "this", "was", "just", "have", "no", "idea", "what", "where", "start",
            "about", "from", "you", "your", "me", "we", "are", "be", "at", "as"}
    buckets: dict[str, list[int]] = defaultdict(list)
    for index, signal in enumerate(signals):
        words = [w for w in re.findall(r"[a-z']{3,}", (signal.get("text") or "").lower())
                 if w not in stop]
        key = " ".join(sorted(words)[:2]) if words else ""
        if key:
            buckets[key].append(index)

    out: list[dict[str, Any]] = []
    for key, indexes in buckets.items():
        evidence = _evidence_from(signals, indexes)
        if not evidence:
            continue
        longest = max((signals[i].get("text") or "" for i in indexes), key=len)
        out.append({
            "concept": longest[:120],
            "audience": "general",
            "category": signals[indexes[0]].get("category") or "general",
            "evidence": evidence,
            "method": "heuristic",
        })
    out.sort(key=lambda c: -len(c["evidence"]))
    return out


def _parse_llm(reply: str) -> Optional[list[dict[str, Any]]]:
    """Models like to wrap JSON in prose or fences. Recover what we can."""
    if not reply:
        return None
    text = reply.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("["):
        bracket = text.find("[")
        if bracket == -1:
            return None
        text = text[bracket:]
    try:
        parsed = json.loads(text)
    except ValueError:
        return _salvage_array(text)
    return parsed if isinstance(parsed, list) else None


def _salvage_array(text: str) -> Optional[list[dict[str, Any]]]:
    """Recover the complete objects from an array cut off mid-flight.

    A truncated reply still contains good concepts before the cut. Discarding
    the whole thing wasted the call and dropped the run to keyword grouping.
    """
    objects: list[dict[str, Any]] = []
    depth = 0
    start = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    candidate = json.loads(text[start:index + 1])
                except ValueError:
                    pass
                else:
                    if isinstance(candidate, dict):
                        objects.append(candidate)
                start = None
    return objects or None


def extract_with_report(signals: list[dict[str, Any]],
                        llm: Optional[Callable[[str], str]] = None,
                        batch_size: int = 40) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Signals -> (concepts, report).

    The report says which path produced the concepts and, when the LLM path
    was abandoned, why. Falling back silently meant a working model could sit
    behind poor heuristic concepts with no way to tell from the output.
    """
    usable = [s for s in signals if has_book_intent(s)]
    if not usable:
        return [], {"method": "none", "reason": "no signal looked book-shaped",
                    "signals": len(signals), "usable": 0, "batches": 0}

    base_report = {"signals": len(signals), "usable": len(usable)}
    if llm is None:
        return _heuristic(usable), {**base_report, "method": "heuristic",
                                    "reason": "no_llm_configured", "batches": 0}

    concepts: list[dict[str, Any]] = []
    batches = 0
    failures: list[str] = []

    for start in range(0, len(usable), batch_size):
        batch = usable[start:start + batch_size]
        listing = "\n".join(
            f"{i}. [{s.get('source')}] {s.get('text')}" +
            (f" — {(s.get('body') or '')[:160]}" if s.get("body") else "")
            for i, s in enumerate(batch))
        try:
            reply = llm(PROMPT + listing)
        except Exception as exc:  # noqa: BLE001 - an outage must not lose the harvest
            failures.append(f"{type(exc).__name__}: {exc}")
            continue
        parsed = _parse_llm(reply)
        if parsed is None:
            failures.append(f"unparseable reply ({len((reply or '').strip())} chars)")
            continue
        batches += 1
        for entry in parsed:
            if not isinstance(entry, dict) or not entry.get("concept"):
                continue
            evidence = _evidence_from(batch, entry.get("signal_indexes") or [])
            if not evidence:
                continue  # a concept without receipts is not a concept
            concepts.append({
                "concept": str(entry["concept"])[:160],
                "audience": str(entry.get("audience") or "general")[:60],
                "category": str(entry.get("category") or "general")[:40],
                "evidence": evidence,
                "method": "llm",
            })

    if not concepts:
        reason = "; ".join(failures[:3]) or "the model returned no usable concepts"
        return _heuristic(usable), {**base_report, "method": "heuristic",
                                    "reason": reason, "batches": batches}

    report = {**base_report, "method": "llm", "batches": batches, "reason": None}
    if failures:
        report["reason"] = f"{len(failures)} batch(es) degraded: " + "; ".join(failures[:2])
    return concepts, report


def extract(signals: list[dict[str, Any]],
            llm: Optional[Callable[[str], str]] = None,
            batch_size: int = 40) -> list[dict[str, Any]]:
    """Concepts only, for callers that do not need the report."""
    return extract_with_report(signals, llm=llm, batch_size=batch_size)[0]
