"""AI Niche Brief — the one-page GO/NO-GO memo, generated from live data.

Two layers:
  1. Deterministic gates (always available): verdict + evidence computed
     straight from the research bundle. Nothing invented.
  2. Optional LLM narrative: when GEMINI_API_KEY or ANTHROPIC_API_KEY is
     set, a model writes the analyst narrative — grounded ONLY in the
     digest we hand it, with ASINs as receipts. If no key is set (or the
     call fails), the brief ships without the narrative and says so.

Env:
  GEMINI_API_KEY   + optional GEMINI_MODEL   (default gemini-2.5-flash)
  ANTHROPIC_API_KEY+ optional ANTHROPIC_MODEL (default claude-sonnet-5)
  (If both are set, Gemini is used — cheaper for bulk.)
"""

import json
import os
import statistics
import urllib.request
from typing import Any, Optional

try:  # pragma: no cover - import-shape shim (see server/trends.py)
    from . import royalty
except ImportError:  # pragma: no cover
    import royalty

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"


def active_provider() -> Optional[dict[str, str]]:
    """Which LLM to use, read at call time rather than at import.

    These used to be module constants captured when `brief` was first
    imported. Since `app.py` imports it during startup, a key added to
    `.env` afterwards was invisible and the brief reported "narrative off"
    with no clue why. Reading the environment per call also lets an operator
    rotate a key without a restart.
    """
    gemini = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini:
        return {"name": "gemini", "key": gemini,
                "model": os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL}
    anthropic = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic:
        return {"name": "anthropic", "key": anthropic,
                "model": os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_ANTHROPIC_MODEL}
    return None


# ---------------------------------------------------------------------------
# Layer 1 — deterministic gates
# ---------------------------------------------------------------------------
def _gate(name: str, passed: Optional[bool], detail: str) -> dict[str, Any]:
    return {"gate": name, "status": "PASS" if passed else ("FAIL" if passed is False else "UNKNOWN"),
            "detail": detail}


def build_brief(bundle: dict[str, Any], goal_month: float = 1000.0) -> dict[str, Any]:
    s = bundle.get("summary", {})
    intel = bundle.get("book_intel", [])
    books = bundle.get("books", [])
    keywords = bundle.get("keywords", [])
    cur = bundle.get("currency", "$")
    store = "kindle" if bundle.get("store", "kindle") == "kindle" else "books"

    gates: list[dict[str, Any]] = []

    # Gate 1 — competition
    total = s.get("total_results")
    gates.append(_gate(
        "Competition",
        None if total is None else total <= 3000,
        f"{total:,} competing books for the focus keyword" if total is not None
        else "No clean result count captured",
    ))

    # Gate 2 — review moat
    med = s.get("median_reviews")
    gates.append(_gate(
        "Review moat",
        None if med is None else med <= 150,
        f"Median page-1 book has {med:.0f} reviews" if med is not None else "No review data",
    ))

    # Gate 3 — money on the shelf
    earners = [b for b in intel if (b.get("est_monthly_royalty") or 0) >= 500]
    best = max((b.get("est_monthly_royalty") or 0) for b in intel) if intel else 0
    gates.append(_gate(
        "Money proof",
        len(earners) >= 2 if intel else None,
        f"{len(earners)} deep-dived books estimated ≥ {cur}500/mo; best ≈ {cur}{best:,.0f}/mo "
        "(BSR-derived, order-of-magnitude)" if intel else "No product pages deep-dived",
    ))

    # Gate 4 — demand evidence
    real_kw = [k for k in keywords if "PRODUCT-INTENT" not in (k.get("verdict") or "")]
    gates.append(_gate(
        "Demand",
        len(real_kw) >= 2,
        f"{len(keywords)} buyer searches mined from autocomplete; {len(real_kw)} are genuine book-intent",
    ))

    fails = sum(1 for g in gates if g["status"] == "FAIL")
    unknowns = sum(1 for g in gates if g["status"] == "UNKNOWN")
    if fails == 0 and unknowns <= 1:
        verdict, why = "VALIDATE", "All measurable gates pass."
    elif fails <= 1:
        verdict, why = "BORDERLINE", "One gate fails or key data is missing — enter only with a real angle."
    else:
        verdict, why = "SKIP", "Multiple gates fail on live data."

    # Break-even reality check at the niche's own price point
    avg_price = s.get("avg_buy_price") or (statistics.median([b["price"] for b in books if b.get("price")])
                                           if any(b.get("price") for b in books) else None)
    breakeven = None
    if avg_price:
        fmt = "ebook" if store == "kindle" else "paperback"
        breakeven = royalty.break_even(
            goal_month, fmt, float(avg_price), pages=120, store=store,
            niche_bsrs=[b["bsr"] for b in intel if b.get("bsr")],
        )

    complaints = bundle.get("complaints", [])[:6]
    return {
        "seed": bundle.get("seed"),
        "focus_keyword": bundle.get("focus_keyword"),
        "marketplace": bundle.get("marketplace"),
        "generated_at": bundle.get("generated_at"),
        "verdict": verdict,
        "verdict_reason": why,
        "gates": gates,
        "goal_month": goal_month,
        "breakeven": breakeven,
        "ku_read": (f"{s.get('ku_share_pct')}% of scanned books are in Kindle Unlimited — "
                    + ("KDP Select enrollment is effectively required; length drives page-read income."
                       if (s.get("ku_share_pct") or 0) >= 50 else
                       "KU is optional here; buy-price revenue dominates.")) if s.get("ku_share_pct") is not None else None,
        "evidence": [
            {"asin": b.get("asin"), "title": (b.get("title") or "")[:70], "bsr": b.get("bsr"),
             "price": b.get("price"), "est_monthly_royalty": b.get("est_monthly_royalty"),
             "published": b.get("publication_date")}
            for b in sorted(intel, key=lambda b: -(b.get("est_monthly_royalty") or 0))[:8]
        ],
        "differentiation": {
            "title_gaps": bundle.get("title_gaps", [])[:5],
            "complaint_themes": [{"stars": c.get("rating"), "title": c.get("title"),
                                  "quote": (c.get("body") or "")[:200]} for c in complaints],
        },
        "warnings": bundle.get("warnings", []),
        "narrative": None,           # filled by the LLM layer when a key is configured
        "narrative_source": None,
    }


# ---------------------------------------------------------------------------
# Layer 2 — grounded LLM narrative (optional)
# ---------------------------------------------------------------------------
_PROMPT = """You are a senior KDP niche analyst writing the narrative section of a validation brief.
Use ONLY the facts in the JSON below. Never invent numbers, books, or trends.
When you cite a book, include its ASIN in parentheses as the receipt.
Write 3 short sections in markdown: **What the data says** (2-4 sentences),
**The angle** (how a new book wins here, from the gaps and complaints),
**Risks** (honest, incl. any warnings). Under 250 words total.

DATA:
"""


def _post_prompt(prompt: str, provider: dict[str, str],
                 max_output_tokens: int = 3000) -> str:
    """Send exactly `prompt` to the configured provider. No prompt of its own.

    Kept separate from the Niche Brief prompt so other features (Discovery
    concept extraction) can use the same key without inheriting the brief's
    "write an analyst narrative" instruction — which is what happened, and
    produced prose where JSON was required.
    """
    if provider["name"] == "gemini":
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{provider['model']}:generateContent",
            data=json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                # Gemini 2.5+/3.x spend output budget on internal reasoning
                # first, so the cap must leave room for thinking and answer.
                "generationConfig": {"temperature": 0.2,
                                     "maxOutputTokens": max_output_tokens},
            }).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": provider["key"]},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        return data["candidates"][0]["content"]["parts"][0]["text"]

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({
            "model": provider["model"],
            "max_tokens": max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode(),
        headers={"Content-Type": "application/json", "x-api-key": provider["key"],
                 "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return "".join(block.get("text", "") for block in data.get("content", []))


def call_llm(prompt: str, max_output_tokens: int = 3000) -> Optional[str]:
    """Generic access to whichever model is configured. None when none is.

    `max_output_tokens` matters more than it looks: Gemini 2.5+ spends this
    budget on internal reasoning before emitting the answer, so a structured
    task with a long prompt needs a larger cap or the reply arrives truncated
    mid-JSON.
    """
    provider = active_provider()
    if provider is None:
        return None
    return _post_prompt(prompt, provider, max_output_tokens=max_output_tokens)


def _call_gemini(digest: str) -> str:
    return _post_prompt(_PROMPT + digest, active_provider())


def _call_anthropic(digest: str) -> str:
    return _post_prompt(_PROMPT + digest, active_provider())


def enhance_with_llm(brief: dict[str, Any]) -> dict[str, Any]:
    """Add the analyst narrative when an LLM key is configured; no-op otherwise."""
    digest = json.dumps({k: brief[k] for k in
                         ("seed", "focus_keyword", "verdict", "gates", "breakeven",
                          "ku_read", "evidence", "differentiation", "warnings")},
                        default=str)
    provider = active_provider()
    try:
        if provider is None:
            brief["narrative_source"] = "none — set GEMINI_API_KEY or ANTHROPIC_API_KEY to enable"
        elif provider["name"] == "gemini":
            brief["narrative"] = _call_gemini(digest)
            brief["narrative_source"] = f"gemini:{provider['model']}"
        else:
            brief["narrative"] = _call_anthropic(digest)
            brief["narrative_source"] = f"anthropic:{provider['model']}"
    except Exception as exc:
        brief["narrative_source"] = f"llm call failed: {type(exc).__name__}: {exc}"
    return brief
