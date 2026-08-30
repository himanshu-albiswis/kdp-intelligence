"""History for Discovery observations — what turns a window into momentum.

Every scored concept from every run is appended here. Nothing overwrites, so
after a few scans a concept's demand trajectory is a real series rather than a
single reading. Until then `momentum()` says `insufficient_history` instead of
inventing a trend from one point — the same discipline the rest of this
project applies to BSR estimates and blocked sources.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    concept     TEXT    NOT NULL,
    category    TEXT,
    window      TEXT    NOT NULL,
    demand      REAL    NOT NULL,
    gap_score   REAL,
    verdict     TEXT,
    observed_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_concept ON observations(concept);
"""


class DiscoveryStore:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def record(self, window: str, cards: list[dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [(c.get("concept"), c.get("category"), window,
                 float(c.get("demand") or 0), (c.get("gap") or {}).get("score"),
                 (c.get("gap") or {}).get("verdict"), now)
                for c in cards if c.get("concept")]
        if not rows:
            return 0
        with self._connect() as db:
            db.executemany(
                "INSERT INTO observations (concept, category, window, demand, "
                "gap_score, verdict, observed_at) VALUES (?,?,?,?,?,?,?)", rows)
        return len(rows)

    def count(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) AS n FROM observations").fetchone()["n"]

    def history(self, concept: str) -> list[dict[str, Any]]:
        """Every observation of one concept, oldest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT demand, gap_score, verdict, window, observed_at "
                "FROM observations WHERE concept = ? ORDER BY id ASC", (concept,)).fetchall()
        return [dict(r) for r in rows]

    def momentum(self, concept: str) -> dict[str, Any]:
        """Direction of travel, or an honest refusal when there is one point."""
        rows = self.history(concept)
        if len(rows) < 2:
            return {"status": "insufficient_history", "change": None,
                    "observations": len(rows),
                    "detail": "seen once; momentum needs at least two scans"}
        change = round(rows[-1]["demand"] - rows[0]["demand"], 1)
        status = "rising" if change > 0 else "falling" if change < 0 else "flat"
        return {"status": status, "change": change, "observations": len(rows),
                "first_seen": rows[0]["observed_at"], "last_seen": rows[-1]["observed_at"],
                "detail": f"demand moved {change:+} across {len(rows)} scans"}

    def top_movers(self, limit: int = 5) -> list[dict[str, Any]]:
        """Concepts whose demand grew most between their first and latest scan."""
        with self._connect() as db:
            names = [r["concept"] for r in db.execute(
                "SELECT concept FROM observations GROUP BY concept HAVING COUNT(*) > 1")]
        movers = []
        for name in names:
            m = self.momentum(name)
            if m["change"] is not None and m["change"] > 0:
                movers.append({"concept": name, **m})
        movers.sort(key=lambda m: -m["change"])
        return movers[:limit]
