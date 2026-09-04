"""Listing translation for international marketplaces.

The model receives only the listing and returns strict JSON per target
language. Every returned pack is re-checked against KDP's rules — a German
title overruns 200 characters as easily as an English one — and English
marketplaces are passed through untouched rather than "translated". No
model configured means no translation, said out loud, not a silent copy.
"""

import json
import re
from typing import Any, Callable, Optional

try:  # pragma: no cover - import-shape shim
    from . import guidelines as guidelines_mod
except ImportError:  # pragma: no cover
    import guidelines as guidelines_mod

MARKET_LANGUAGE: dict[str, str] = {
    "us": "English", "uk": "English", "ca": "English", "au": "English", "in": "English",
    "de": "German", "fr": "French", "it": "Italian", "es": "Spanish", "nl": "Dutch",
    "se": "Swedish", "jp": "Japanese", "br": "Portuguese (Brazil)", "mx": "Spanish (Mexico)",
}

PROMPT = """Translate this Amazon KDP book listing for the marketplaces listed.

Rules:
- Output ONLY a JSON array, no prose, no code fences.
- One element per marketplace: {{"marketplace": str, "language": str, "title": str,
  "subtitle": str, "description": str, "keywords": [str]}}
- Translate for buyers, not word-for-word: phrases a native reader would type
  into Amazon. Keep the description's HTML tags exactly as given.
- Title at most 200 characters; each keyword at most 50 characters and
  lowercase; keep the same number of keywords.
- Do not add claims, awards or words that are not in the source.

Marketplaces and languages:
{targets}

Listing:
{listing}
"""


def language_for(marketplace: str) -> str:
    try:
        return MARKET_LANGUAGE[marketplace.lower()]
    except KeyError:
        raise ValueError(f"unknown marketplace {marketplace!r}; known: {sorted(MARKET_LANGUAGE)}")


def needs_translation(marketplace: str, source_language: str = "English") -> bool:
    return language_for(marketplace).split(" ")[0] != source_language.split(" ")[0]


def _parse(reply: str) -> Optional[list[dict[str, Any]]]:
    text = (reply or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    if start == -1:
        return None
    try:
        parsed = json.loads(text[start:])
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _passthrough(listing: dict[str, Any], marketplace: str) -> dict[str, Any]:
    pack = {"marketplace": marketplace, "language": language_for(marketplace),
            "translated": False, **{k: listing.get(k) for k in
                                   ("title", "subtitle", "description", "keywords", "categories")}}
    pack["guidelines"] = guidelines_mod.check_listing(pack)
    return pack


def translate_listing(listing: dict[str, Any], marketplaces: list[str],
                      llm: Optional[Callable[[str], str]], source_language: str = "English") -> dict[str, Any]:
    warnings: list[str] = []
    packs: list[dict[str, Any]] = []
    targets = []
    for market in marketplaces:
        if needs_translation(market, source_language):
            targets.append(market)
        else:
            packs.append(_passthrough(listing, market))

    if targets and llm is None:
        warnings.append("No model configured, so nothing was translated. Set GEMINI_API_KEY "
                        f"to translate for: {', '.join(targets)}.")
        targets = []

    if targets:
        prompt = PROMPT.format(
            targets="\n".join(f"- {m}: {language_for(m)}" for m in targets),
            listing=json.dumps({k: listing.get(k) for k in
                                ("title", "subtitle", "description", "keywords")}, ensure_ascii=False))
        try:
            parsed = _parse(llm(prompt))
        except Exception as exc:  # noqa: BLE001
            parsed = None
            warnings.append(f"Model call failed ({type(exc).__name__}: {exc}).")
        if parsed is None:
            warnings.append("The model's reply was not usable JSON; nothing was translated.")
        else:
            got = {str(p.get("marketplace", "")).lower(): p for p in parsed if isinstance(p, dict)}
            for market in targets:
                pack = got.get(market)
                if not pack or not pack.get("title"):
                    warnings.append(f"The model returned no pack for {market}.")
                    continue
                out = {
                    "marketplace": market, "language": language_for(market), "translated": True,
                    "title": str(pack.get("title") or "")[:300],
                    "subtitle": str(pack.get("subtitle") or "")[:300],
                    "description": str(pack.get("description") or ""),
                    "keywords": [str(k).strip()[:80] for k in (pack.get("keywords") or [])][:7],
                    "categories": listing.get("categories") or [],
                }
                out["guidelines"] = guidelines_mod.check_listing(out)
                packs.append(out)

    return {"packs": packs, "warnings": warnings}
