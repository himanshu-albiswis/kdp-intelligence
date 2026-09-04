"""Category database — a browsable catalogue that grows with every scan.

BookBeam sells 45,000 browsable categories. Crawling all of that from one
rate-limited residential IP is not an honest promise, so this does two
things instead: it records every category any scan observes (with the best
entry bar seen), and it can walk Amazon's bestseller tree breadth-first
within a request budget whenever the network allows. Both feed one store,
searchable and browsable from the UI.

Markup, from amazon.com/gp/bestsellers/digital-text/ on 2026-09-05:
subcategory links are /zgbs/digital-text/<node>, and the ranked grid ships
its items in a data-client-recs-list JSON attribute with render.zg.rank.
"""

import html as html_mod
import json
import os
import re
import sqlite3
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Optional

_SUBCAT = re.compile(r'href="([^"]*?/zgbs/digital-text/(\d+)[^"]*)"[^>]*>\s*([^<]{2,80}?)\s*<')
_RECS = re.compile(r'data-client-recs-list="([^"]+)"')

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    name        TEXT PRIMARY KEY,
    node        TEXT,
    parent      TEXT,
    depth       INTEGER,
    best_observed_rank INTEGER,
    entry_sales_day    REAL,
    top_asin    TEXT,
    times_seen  INTEGER NOT NULL DEFAULT 0,
    niches      TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent);
"""


def parse_bestseller_page(page: str, own_node: str) -> dict[str, Any]:
    subs: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, node, name in _SUBCAT.findall(page or ""):
        name = html_mod.unescape(name).strip()
        if node == own_node or node in seen or not name or name == "Amazon Best Sellers":
            continue
        seen.add(node)
        subs.append({"node": node, "name": name})

    top: list[dict[str, Any]] = []
    for raw in _RECS.findall(page or ""):
        try:
            items = json.loads(html_mod.unescape(raw))
        except ValueError:
            continue
        for item in items:
            asin = item.get("id")
            rank = (item.get("metadataMap") or {}).get("render.zg.rank")
            if asin:
                top.append({"asin": asin, "rank": int(rank) if str(rank).isdigit() else None})
    top.sort(key=lambda t: (t["rank"] is None, t["rank"] or 0))
    return {"subcategories": subs, "top": top}


class CategoryStore:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def record_observed(self, categories: list[dict[str, Any]], niche: str) -> int:
        """Categories a scan saw, keeping the best entry bar across scans."""
        written = 0
        with self._db() as db:
            for cat in categories:
                name = (cat.get("category") or "").strip()
                if not name:
                    continue
                row = db.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()
                rank, bar = cat.get("best_observed_rank"), cat.get("entry_sales_day")
                if row:
                    better = rank is not None and (row["best_observed_rank"] is None
                                                   or rank < row["best_observed_rank"])
                    niches = set(filter(None, row["niches"].split("|"))) | {niche}
                    db.execute(
                        "UPDATE categories SET best_observed_rank=?, entry_sales_day=?, "
                        "times_seen=times_seen+1, niches=?, updated_at=? WHERE name=?",
                        (rank if better else row["best_observed_rank"],
                         bar if better else row["entry_sales_day"],
                         "|".join(sorted(niches)), self._now(), name))
                else:
                    db.execute(
                        "INSERT INTO categories (name, best_observed_rank, entry_sales_day, "
                        "times_seen, niches, updated_at) VALUES (?,?,?,1,?,?)",
                        (name, rank, bar, niche, self._now()))
                written += 1
        return written

    def record_node(self, node: str, name: str, parent: Optional[str], depth: int,
                    top_asin: Optional[str] = None) -> None:
        with self._db() as db:
            row = db.execute("SELECT name FROM categories WHERE name = ?", (name,)).fetchone()
            if row:
                db.execute("UPDATE categories SET node=?, parent=?, depth=?, "
                           "top_asin=COALESCE(?, top_asin), updated_at=? WHERE name=?",
                           (node, parent, depth, top_asin, self._now(), name))
            else:
                db.execute("INSERT INTO categories (name, node, parent, depth, top_asin, "
                           "times_seen, updated_at) VALUES (?,?,?,?,?,0,?)",
                           (name, node, parent, depth, top_asin, self._now()))

    def count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]

    def search(self, text: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM categories WHERE lower(name) LIKE ? "
                "ORDER BY times_seen DESC, entry_sales_day IS NULL, entry_sales_day ASC, name "
                "LIMIT ?", (f"%{text.lower()}%", limit)).fetchall()
        return [dict(r) for r in rows]

    def children(self, node: str) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM categories WHERE parent = ? ORDER BY name",
                              (node,)).fetchall()
        return [dict(r) for r in rows]

    def all(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM categories ORDER BY times_seen DESC, name LIMIT ?",
                              (limit,)).fetchall()
        return [dict(r) for r in rows]


def crawl(store: CategoryStore, fetch: Callable[[str], Any], root: str = "154606011",
          max_pages: int = 40, max_depth: int = 3) -> int:
    """Walk the bestseller tree breadth-first, within a page budget.

    Returns pages successfully parsed. A blocked page is skipped, never
    retried in the same run — retrying against a soft-block deepens it.
    """
    queue: deque[tuple[str, Optional[str], int, str]] = deque([(root, None, 0, "Kindle eBooks")])
    seen = {root}
    parsed = attempts = 0
    # The budget bounds requests, not successes: it exists to cap the load
    # placed on Amazon, and an empty or blocked page still cost a request.
    while queue and attempts < max_pages:
        node, parent, depth, name = queue.popleft()
        attempts += 1
        # /zgbs/digital-text/<node> 404s without its SEO slug; the /gp/ form
        # serves the same tree (measured: 31 subcategories, 50 ranked books).
        url = f"https://www.amazon.com/gp/bestsellers/digital-text/{node}"
        try:
            response = fetch(url)
        except Exception:  # noqa: BLE001
            continue
        body = getattr(response, "body", "") or ""
        if not isinstance(body, str):
            body = body.decode("utf-8", "ignore")
        if getattr(response, "status", 0) != 200 or not body:
            continue
        page = parse_bestseller_page(body, own_node=node)
        top_asin = page["top"][0]["asin"] if page["top"] else None
        store.record_node(node, name, parent, depth, top_asin=top_asin)
        parsed += 1
        if depth < max_depth:
            for sub in page["subcategories"]:
                if sub["node"] not in seen:
                    seen.add(sub["node"])
                    queue.append((sub["node"], node, depth + 1, sub["name"]))
    return parsed
