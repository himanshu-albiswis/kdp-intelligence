"""KDP Niche Intelligence — web backend.

Run from the repo root:
    uvicorn app:app --app-dir server --host 0.0.0.0 --port 8000

Environment:
    KDP_API_KEY   optional — when set, POST/DELETE require header  X-API-Key: <value>
    KDP_DB        optional — sqlite path (default: data/jobs.db under repo root)
"""

import json
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import brief as brief_mod
import pipeline
import royalty as royalty_mod
import trends as trends_mod

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SERVER_DIR)
DB_PATH = os.environ.get("KDP_DB", os.path.join(REPO_ROOT, "data", "jobs.db"))
API_KEY = os.environ.get("KDP_API_KEY", "")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
_db_lock = threading.Lock()
# One research at a time: polite to Amazon, and spiders are process-heavy anyway
_executor = ThreadPoolExecutor(max_workers=1)

app = FastAPI(title="KDP Niche Intelligence", version="1.0")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


with _db() as conn:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            params TEXT NOT NULL,
            status TEXT NOT NULL,           -- queued | running | done | partial | failed
            stage TEXT DEFAULT '',
            pct INTEGER DEFAULT 0,
            message TEXT DEFAULT '',
            result TEXT
        )"""
    )
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN kind TEXT DEFAULT 'research'")
    except sqlite3.OperationalError:
        pass  # column already exists


def _update(job_id: str, **fields: Any) -> None:
    keys = ", ".join(f"{k}=?" for k in fields)
    with _db_lock, _db() as conn:
        conn.execute(f"UPDATE jobs SET {keys} WHERE id=?", [*fields.values(), job_id])


def _row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    params = json.loads(row["params"])
    return {
        "id": row["id"],
        "kind": row["kind"] if "kind" in row.keys() else "research",
        "created_at": row["created_at"],
        "seed": params.get("seed"),
        "marketplace": params.get("marketplace", "us"),
        "store": params.get("store", "kindle"),
        "demo": bool(params.get("demo")),
        "status": row["status"],
        "stage": row["stage"],
        "pct": row["pct"],
        "message": row["message"],
    }


def _worker(job_id: str, params: dict[str, Any], kind: str = "research") -> None:
    _update(job_id, status="running", stage="starting", message="Worker picked up the job")

    def progress(stage: str, pct: int, message: str) -> None:
        _update(job_id, stage=stage, pct=pct, message=message)

    try:
        if kind == "trends":
            bundle = trends_mod.run_trend_radar(params, progress)
        else:
            bundle = pipeline.run_research(params, progress)
        status = "partial" if bundle.get("warnings") and not bundle.get("books") else "done"
        if bundle.get("warnings") and bundle.get("books"):
            status = "done"  # warnings but usable data
        _update(job_id, status=status, pct=100, stage="done",
                message="; ".join(bundle.get("warnings", [])) or "Complete",
                result=json.dumps(bundle, default=str))
    except Exception as exc:  # surface real failures to the UI, never hang the queue
        _update(job_id, status="failed", stage="error", message=f"{type(exc).__name__}: {exc}")


class ResearchRequest(BaseModel):
    seed: str = Field(min_length=2, max_length=120)
    marketplace: str = "us"
    store: str = "kindle"
    max_keywords: int = Field(default=10, ge=1, le=40)
    books: int = Field(default=30, ge=5, le=200)
    deep_dive: int = Field(default=8, ge=0, le=30)
    complaint_books: int = Field(default=3, ge=0, le=10)
    focus: Optional[str] = None
    impersonate: str = "chrome"
    plain_headers: bool = False
    proxy: Optional[str] = None
    proxies: Optional[list[str]] = None
    demo: bool = False


def _check_key(x_api_key: Optional[str]) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Missing or wrong X-API-Key header")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(), "auth_required": bool(API_KEY)}


@app.post("/api/research")
def create_research(req: ResearchRequest, x_api_key: Optional[str] = Header(default=None)) -> dict[str, str]:
    _check_key(x_api_key)
    if req.marketplace not in pipeline.MARKETPLACES:
        raise HTTPException(422, f"Unknown marketplace {req.marketplace!r}")
    if req.store not in pipeline.STORES:
        raise HTTPException(422, f"Unknown store {req.store!r}")
    return _enqueue(req.model_dump(), "research")


def _enqueue(params: dict[str, Any], kind: str) -> dict[str, str]:
    job_id = uuid.uuid4().hex[:12]
    with _db_lock, _db() as conn:
        conn.execute(
            "INSERT INTO jobs (id, created_at, params, status, kind) VALUES (?,?,?,?,?)",
            [job_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(params), "queued", kind],
        )
    _executor.submit(_worker, job_id, params, kind)
    return {"job_id": job_id}


class TrendRequest(BaseModel):
    seed: str = Field(min_length=2, max_length=120)
    marketplace: str = "us"
    store: str = "kindle"
    validate_top: int = Field(default=8, ge=1, le=20)
    impersonate: str = "edge"
    plain_headers: bool = False
    proxy: Optional[str] = None
    proxies: Optional[list[str]] = None


@app.post("/api/trends")
def create_trends(req: TrendRequest, x_api_key: Optional[str] = Header(default=None)) -> dict[str, str]:
    _check_key(x_api_key)
    return _enqueue(req.model_dump(), "trends")


class RoyaltyRequest(BaseModel):
    goal_month: float = Field(default=1000, gt=0, le=1_000_000)
    format: str = "ebook"  # ebook | paperback | hardcover | audiobook_acx | audiobook_wide
    price: float = Field(gt=0, le=500)
    pages: int = Field(default=120, ge=24, le=900)
    color: bool = False
    file_mb: float = Field(default=2.0, gt=0, le=100)
    research_job_id: Optional[str] = None  # feasibility against that job's deep-dived BSRs


@app.post("/api/royalty")
def royalty_calc(req: RoyaltyRequest) -> dict[str, Any]:
    niche_bsrs = None
    if req.research_job_id:
        with _db() as conn:
            row = conn.execute("SELECT result FROM jobs WHERE id=?", [req.research_job_id]).fetchone()
        if row and row["result"]:
            intel = json.loads(row["result"]).get("book_intel", [])
            niche_bsrs = [b["bsr"] for b in intel if b.get("bsr")]
    try:
        return royalty_mod.break_even(
            req.goal_month, req.format, req.price, pages=req.pages,
            color=req.color, file_mb=req.file_mb, niche_bsrs=niche_bsrs,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/research/{job_id}/brief")
def make_brief(job_id: str, goal_month: float = 1000) -> dict[str, Any]:
    with _db() as conn:
        row = conn.execute("SELECT result FROM jobs WHERE id=?", [job_id]).fetchone()
    if not row or not row["result"]:
        raise HTTPException(409, "Job has no result yet")
    bundle = json.loads(row["result"])
    b = brief_mod.build_brief(bundle, goal_month=goal_month)
    b = brief_mod.enhance_with_llm(b)
    bundle["brief"] = b
    _update(job_id, result=json.dumps(bundle, default=str))
    return b


@app.get("/api/research")
def list_research() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
    return [_row_to_summary(r) for r in rows]


@app.get("/api/research/{job_id}")
def get_research(job_id: str) -> dict[str, Any]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", [job_id]).fetchone()
    if not row:
        raise HTTPException(404, "No such job")
    out = _row_to_summary(row)
    if row["result"]:
        out["result"] = json.loads(row["result"])
    return out


@app.delete("/api/research/{job_id}")
def delete_research(job_id: str, x_api_key: Optional[str] = Header(default=None)) -> dict[str, bool]:
    _check_key(x_api_key)
    with _db_lock, _db() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id=? AND status IN ('done','partial','failed')", [job_id])
    if cur.rowcount == 0:
        raise HTTPException(409, "Job not found or still running")
    return {"deleted": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(SERVER_DIR, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(SERVER_DIR, "static")), name="static")
