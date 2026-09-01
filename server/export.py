"""CSV export for every table the app produces.

Every cell here is scraped or model-written text, which makes the classic
spreadsheet formula-injection attack real: a competitor could title a book
"=HYPERLINK(...)" and it would execute the moment a client opens the export
in Excel. Following OWASP, any cell beginning with = + - @ is prefixed with
an apostrophe — Excel then shows it as text. Genuine numbers are exempt; a
negative price is data, not a payload.
"""

import csv
import io
import re
from typing import Any, Iterable, Optional

_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(str(_cell(v)) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={_cell(v)}" for k, v in value.items())
    text = str(value)
    if text.startswith(_FORMULA_LEADS):
        return "'" + text
    return text


def _lookup(row: dict[str, Any], path: str) -> Any:
    """Dotted paths reach into nested dicts: "gap.score"."""
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def to_csv(rows: Iterable[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    """Rows + (path, header) columns -> CSV text."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([header for _, header in columns])
    for row in rows:
        writer.writerow([_cell(_lookup(row, path)) for path, _ in columns])
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Which tables a result bundle can offer, and their columns
# ---------------------------------------------------------------------------
_TABLES: dict[str, dict[str, Any]] = {
    "keywords": {
        "rows": lambda b: b.get("keywords") or [],
        "columns": [
            ("keyword", "Keyword"), ("demand_index", "Demand (0-100)"),
            ("demand_band", "Demand band"), ("total_results", "Amazon results"),
            ("median_reviews", "Median reviews"), ("pct_under_100", "% under 100 reviews"),
            ("ku_share", "KU share %"), ("avg_price", "Avg price"),
            ("phrase_in_titles", "Phrase in titles"), ("opportunity", "Opportunity"),
            ("verdict", "Verdict"), ("demand_basis", "Demand basis"), ("url", "Amazon URL"),
        ],
    },
    "books": {
        "rows": lambda b: b.get("books") or [],
        "columns": [
            ("asin", "ASIN"), ("title", "Title"), ("price", "Price"),
            ("kindle_unlimited", "Kindle Unlimited"), ("rating", "Rating"),
            ("reviews", "Reviews"), ("url", "URL"),
        ],
    },
    "book_intel": {
        "rows": lambda b: b.get("book_intel") or [],
        "columns": [
            ("asin", "ASIN"), ("title", "Title"), ("bsr", "BSR"),
            ("category_rank", "Best category rank"), ("price", "Price"),
            ("est_sales_per_day", "Est sales/day"),
            ("est_monthly_royalty", "Est royalty/mo"),
            ("publication_date", "Published"), ("url", "URL"),
        ],
    },
    "categories": {
        "rows": lambda b: (b.get("category_intel") or {}).get("categories") or [],
        "columns": [
            ("category", "Category"), ("books_observed", "Niche books observed"),
            ("best_observed_rank", "Best observed rank"),
            ("entry_sales_day", "Entry bar (sales/day)"), ("exact", "Exact read"),
            ("read", "Reading"), ("witness.title", "Witness book"),
            ("witness.asin", "Witness ASIN"),
        ],
    },
    "teardown": {
        "rows": lambda b: b.get("rows") or [],
        "columns": [
            ("asin", "Kindle ASIN"), ("title", "Title"), ("subtitle", "Subtitle"),
            ("author", "Author"), ("credential", "Credential"),
            ("sub_niche", "Sub-niche"), ("formats", "Formats"),
            ("paperback_isbn", "Paperback ISBN"), ("year", "Year"), ("pages", "Pages"),
            ("bsr", "BSR (Kindle)"), ("bsr_status", "BSR status"),
            ("reviews", "Reviews"), ("rating", "Rating"),
            ("positioning", "Positioning"), ("crowdedness.verdict", "Crowdedness"),
            ("crowdedness.competing_books", "Competing books"),
            ("crowdedness.median_reviews", "Shelf median reviews"),
            ("source_url", "Source URL"),
        ],
    },
    "cards": {
        "rows": lambda b: b.get("cards") or [],
        "columns": [
            ("concept", "Concept"), ("audience", "Audience"), ("category", "Category"),
            ("demand", "Demand"), ("corroborated", "Corroborated"),
            ("sources", "Sources"), ("gap.score", "Gap score"),
            ("gap.verdict", "Gap verdict"), ("validate_phrase", "Validate phrase"),
        ],
    },
    "validated": {
        "rows": lambda b: b.get("validated") or [],
        "columns": [
            ("phrase", "Phrase"), ("quadrant", "Quadrant"), ("sources", "Sources"),
            ("breadth", "Breadth"), ("total_results", "Amazon results"),
            ("median_reviews", "Median reviews"), ("phrase_in_titles", "In titles"),
            ("opportunity", "Opportunity"), ("url", "Amazon URL"),
        ],
    },
}


def available_tables(bundle: dict[str, Any]) -> list[str]:
    """Only the tables this bundle actually has rows for."""
    return [name for name, spec in _TABLES.items() if spec["rows"](bundle)]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "export").lower()).strip("-")[:40] or "export"


def table_csv(bundle: dict[str, Any], table: str) -> tuple[str, str]:
    """(filename, csv text) for one table of a result bundle."""
    spec = _TABLES.get(table)
    if spec is None or not spec["rows"](bundle):
        raise KeyError(f"bundle has no table {table!r}; "
                       f"available: {available_tables(bundle)}")
    label = bundle.get("seed") or bundle.get("topic") or bundle.get("window") or "export"
    filename = f"{_slug(str(label))}-{table}.csv"
    return filename, to_csv(spec["rows"](bundle), spec["columns"])
