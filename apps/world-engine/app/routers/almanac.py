"""Reading Room API — cited facts, no unsourced claims (ticket WORLD-7).

Three endpoints, mounted under ``/api/almanac``:

* ``GET  /api/almanac/search``       — FTS5 search over ``docs_fts``
* ``GET  /api/almanac/doc/{ref}``    — full document by id or slug
* ``POST /api/almanac/verify``       — supported / stale / unsupported verdict

Every hit carries ``source_path`` + ``fetched_at`` (plan rule D10). Nothing in
here calls an LLM and nothing derives state: it is a read-only projection of
``world.db``.

Mount it in the engine app::

    from app.routers.almanac import router as almanac_router
    app.include_router(almanac_router)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..almanac_store import DEFAULT_DB_PATH, STALE_AFTER_DAYS, AlmanacStore

router = APIRouter(prefix="/api/almanac", tags=["almanac"])

REPO_ROOT = Path(__file__).resolve().parents[4]


def resolve_db_path() -> Path:
    """``WORLD_DB`` wins; otherwise ``<repo>/world.db`` (one SQLite file)."""
    env = os.environ.get("WORLD_DB")
    return Path(env) if env else REPO_ROOT / "world.db"


@lru_cache(maxsize=1)
def _store_for(path: str) -> AlmanacStore:
    return AlmanacStore(path)


def get_store() -> AlmanacStore:
    """FastAPI dependency; tests override it with a tmp-path store."""
    return _store_for(str(resolve_db_path()))


@router.get("/search")
def search(
    q: str = Query(..., min_length=2, max_length=300, description="Free-text query"),
    limit: int = Query(10, ge=1, le=50),
    store: AlmanacStore = Depends(get_store),
) -> dict[str, Any]:
    results = store.search(q, limit=limit)
    return {
        "query": q,
        "count": len(results),
        "stale_after_days": STALE_AFTER_DAYS,
        "results": results,
    }


@router.get("/doc/{doc_ref:path}")
def get_doc(
    doc_ref: str,
    store: AlmanacStore = Depends(get_store),
) -> dict[str, Any]:
    doc = store.get_doc(doc_ref)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"unknown document: {doc_ref}")
    if not doc.get("source_path") or not doc.get("fetched_at"):  # pragma: no cover - schema guards
        raise HTTPException(status_code=500, detail="document is missing provenance")
    return doc


@router.post("/verify")
def verify(
    payload: dict[str, Any],
    store: AlmanacStore = Depends(get_store),
) -> dict[str, Any]:
    claim = payload.get("claim") if isinstance(payload, dict) else None
    if not isinstance(claim, str) or len(claim.strip()) < 3:
        raise HTTPException(status_code=422, detail="'claim' (string, min 3 chars) is required")
    now = payload.get("now")
    if now is not None and not isinstance(now, str):
        raise HTTPException(status_code=422, detail="'now' must be an ISO-8601 string when supplied")
    return store.verify(claim, now=now)
