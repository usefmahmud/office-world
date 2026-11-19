"""Tests for the Reading Room almanac (ticket WORLD-7).

Covers the three acceptance criteria from the ticket:

1. a known phrase returns a hit that carries ``source_path`` + ``fetched_at``;
2. a false claim comes back ``unsupported`` (no citation invented);
3. a document older than 90 days makes its claim ``stale``.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPO_ROOT / "apps" / "world-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from app.almanac_store import (  # noqa: E402
    STALE_AFTER_DAYS,
    AlmanacStore,
    age_days,
    normalize_token,
    tokenize,
)
from app.routers import almanac as almanac_router_module  # noqa: E402
from app.routers.almanac import get_store, router  # noqa: E402

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")


def iso(days_ago: int, *, now: datetime = NOW) -> str:
    return (now - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


# --- fixtures ---------------------------------------------------------------

SEED = [
    {
        "slug": "weekly-sync",
        "title": "الاجتماع الأسبوعي للفريق",
        "body": (
            "الاجتماع الأسبوعي للفريق يعقد يوم الأحد الساعة العاشرة صباحا في غرفة الاجتماعات. "
            "الحضور إلزامي لكل الأعضاء الستة، ومدة الاجتماع خمس وأربعون دقيقة."
        ),
        "source_path": "shared/projects/office-world/PLAN.md",
        "fetched_at": iso(5),
    },
    {
        "slug": "annual-leave",
        "title": "الإجازة السنوية",
        "body": (
            "الإجازة السنوية لكل عضو في الفريق واحد وعشرون يوما مدفوعة الأجر، "
            "ويجب تقديم طلب الإجازة قبل أسبوعين على الأقل."
        ),
        "source_path": "shared/projects/office-world/HR-POLICY.md",
        "fetched_at": iso(30),
    },
    {
        "slug": "engine-port",
        "title": "World engine port",
        "body": (
            "The world engine runs behind nginx and listens on port 8095. "
            "No public exposure without explicit owner approval."
        ),
        "source_path": "shared/projects/office-world/PLAN.md#6-infra",
        "fetched_at": iso(3),
    },
    {
        "slug": "docker-policy",
        "title": "سياسة النشر",
        "body": (
            "النشر عبر دوكر ممنوع بدون موافقة صريحة من المالك. "
            "لا يتم تشغيل أي خدمة جديدة على السيرفر قبل الموافقة."
        ),
        "source_path": "shared/projects/office-world/TEAM_RULES.md",
        "fetched_at": iso(130),
    },
]


@pytest.fixture()
def store(tmp_path: Path) -> AlmanacStore:
    db = AlmanacStore(tmp_path / "world.db")
    for doc in SEED:
        db.ingest(**doc, now=NOW)
    yield db
    db.close()


@pytest.fixture()
def client(store: AlmanacStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


# --- schema -----------------------------------------------------------------


def test_schema_creates_docs_and_fts5_index(store: AlmanacStore) -> None:
    raw = sqlite3.connect(store.db_path)
    try:
        tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"docs", "docs_fts", "meta"} <= tables
        ddl = raw.execute("SELECT sql FROM sqlite_master WHERE name='docs_fts'").fetchone()[0]
        assert "fts5" in ddl.lower()
        assert raw.execute("SELECT count(*) FROM docs").fetchone()[0] == len(SEED)
        assert raw.execute("SELECT count(*) FROM docs_fts").fetchone()[0] == len(SEED)
        version = raw.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        assert version == "1"
    finally:
        raw.close()


def test_rebuild_fts_repairs_index(tmp_path: Path) -> None:
    db = AlmanacStore(tmp_path / "world.db")
    try:
        for doc in SEED[:2]:
            db.ingest(**doc, now=NOW)
        db._conn.execute("DELETE FROM docs_fts")  # simulate index loss
        db._conn.commit()
        assert db.search("الاجتماع الأسبوعي", now=NOW) == []
        assert db.rebuild_fts() == 2
        assert db.search("الاجتماع الأسبوعي", now=NOW)
    finally:
        db.close()


# --- AC1: known phrase → cited hit -----------------------------------------


def test_search_known_phrase_returns_source_and_fetched_at(client: TestClient) -> None:
    response = client.get("/api/almanac/search", params={"q": "الاجتماع الأسبوعي"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    hit = payload["results"][0]
    assert hit["slug"] == "weekly-sync"
    assert hit["source_path"] == "shared/projects/office-world/PLAN.md"
    assert hit["fetched_at"].startswith("2026-11-14")
    assert hit["stale"] is False
    assert "[" in hit["snippet"]  # FTS5 snippet markers around the match


def test_search_ignores_arabic_article_and_hamza_variants(client: TestClient) -> None:
    for query in ("اجتماع الأسبوعي", "الاجتماع الاسبوعي", "الاجتماع"):
        payload = client.get("/api/almanac/search", params={"q": query}).json()
        assert payload["count"] >= 1, query
        assert payload["results"][0]["slug"] == "weekly-sync", query


def test_search_matches_english_query(client: TestClient) -> None:
    payload = client.get("/api/almanac/search", params={"q": "nginx port"}).json()
    assert payload["count"] >= 1
    assert payload["results"][0]["slug"] == "engine-port"
    assert payload["results"][0]["source_path"].endswith("#6-infra")


def test_search_no_match_is_empty_not_invented(client: TestClient) -> None:
    payload = client.get("/api/almanac/search", params={"q": "quantum blockchain"}).json()
    assert payload["count"] == 0
    assert payload["results"] == []


def test_search_validates_query_length(client: TestClient) -> None:
    assert client.get("/api/almanac/search", params={"q": "x"}).status_code == 422


def test_search_ranks_title_matches_and_respects_limit(store: AlmanacStore) -> None:
    hits = store.search("الإجازة", limit=1, now=NOW)
    assert len(hits) == 1
    assert hits[0]["slug"] == "annual-leave"


# --- document endpoint ------------------------------------------------------


def test_doc_endpoint_by_slug_and_id(client: TestClient) -> None:
    by_slug = client.get("/api/almanac/doc/weekly-sync")
    assert by_slug.status_code == 200
    body = by_slug.json()
    assert body["source_path"] and body["fetched_at"]
    assert "غرفة الاجتماعات" in body["body"]
    by_id = client.get(f"/api/almanac/doc/{body['doc_id']}")
    assert by_id.status_code == 200
    assert by_id.json()["slug"] == "weekly-sync"


def test_doc_endpoint_unknown_is_404(client: TestClient) -> None:
    assert client.get("/api/almanac/doc/does-not-exist").status_code == 404
    assert client.get("/api/almanac/doc/99999").status_code == 404


def test_doc_endpoint_accepts_namespaced_slug(store: AlmanacStore) -> None:
    store.ingest(
        slug="office-world/infra-port",
        title="المنفذ",
        body="العرض على المنفذ 8095",
        source_path="PLAN.md#6-infra",
        fetched_at=NOW,
        now=NOW,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    response = TestClient(app).get("/api/almanac/doc/office-world/infra-port")
    assert response.status_code == 200
    assert response.json()["slug"] == "office-world/infra-port"


# --- AC2: false claim → unsupported ---------------------------------------


def test_verify_supported_claim_carries_citation(client: TestClient) -> None:
    claim = "الاجتماع الأسبوعي للفريق يعقد يوم الأحد الساعة العاشرة صباحا"
    payload = client.post("/api/almanac/verify", json={"claim": claim}).json()
    assert payload["verdict"] == "supported"
    assert payload["reason"] == "matched"
    assert payload["source_path"] == "shared/projects/office-world/PLAN.md"
    assert payload["fetched_at"]
    assert payload["doc"]["slug"] == "weekly-sync"


def test_verify_english_claim_is_supported(client: TestClient) -> None:
    claim = "The world engine listens on port 8095 behind nginx"
    payload = client.post("/api/almanac/verify", json={"claim": claim}).json()
    assert payload["verdict"] == "supported"
    assert payload["source_path"].endswith("#6-infra")


def test_verify_false_claim_is_unsupported_and_uncited(client: TestClient) -> None:
    claim = "ميزانية الشركة للعام القادم خمسة ملايين دولار"
    payload = client.post("/api/almanac/verify", json={"claim": claim}).json()
    assert payload["verdict"] == "unsupported"
    assert payload["source_path"] is None
    assert payload["fetched_at"] is None
    assert payload["doc"] is None


def test_verify_near_miss_claim_is_unsupported(client: TestClient) -> None:
    # Same topic, wrong number: 20 days vs. the documented 21.
    claim = "الإجازة السنوية لكل عضو في الفريق عشرون يوما"
    payload = client.post("/api/almanac/verify", json={"claim": claim}).json()
    assert payload["verdict"] == "unsupported"
    assert payload["reason"] == "numeric_conflict"
    assert payload["numbers"]["claim"] == [20]
    assert 21 in payload["numbers"]["doc"]


def test_verify_claim_with_unknown_term_is_unsupported(client: TestClient) -> None:
    claim = "الإجازة السنوية لكل عضو في الفريق مدفوعة بالذهب"
    payload = client.post("/api/almanac/verify", json={"claim": claim}).json()
    assert payload["verdict"] == "unsupported"
    assert payload["reason"] == "weak_match"
    assert payload["source_path"] is None  # unsupported claims carry no citation
    assert payload["best_candidate"]["source_path"].endswith("HR-POLICY.md")


def test_verify_rejects_missing_claim(client: TestClient) -> None:
    assert client.post("/api/almanac/verify", json={}).status_code == 422
    assert client.post("/api/almanac/verify", json={"claim": "x"}).status_code == 422
    assert client.post("/api/almanac/verify", json={"claim": "ok", "now": 5}).status_code == 422


# --- AC3: older than 90 days → stale --------------------------------------


def test_verify_stale_document_returns_stale_with_source(client: TestClient) -> None:
    claim = "النشر عبر دوكر ممنوع بدون موافقة صريحة من المالك"
    payload = client.post("/api/almanac/verify", json={"claim": claim, "now": NOW_ISO}).json()
    assert payload["verdict"] == "stale"
    assert "stale_source" in payload["reason"]
    assert payload["source_path"].endswith("TEAM_RULES.md")
    assert payload["age_days"] > STALE_AFTER_DAYS
    assert payload["stale"] is True


def test_stale_boundary_is_exclusive_at_90_days(tmp_path: Path) -> None:
    db = AlmanacStore(tmp_path / "world.db")
    try:
        db.ingest(
            slug="boundary",
            title="حدود الحداثة",
            body="الحد الأقصى لعمر المعلومة تسعون يوما بالضبط قبل اعتبارها قديمة",
            source_path="docs/boundary.md",
            fetched_at=iso(90),
            now=NOW,
        )
        assert db.verify("الحد الأقصى لعمر المعلومة تسعون يوما", now=NOW)["verdict"] == "supported"
        assert db.verify("الحد الأقصى لعمر المعلومة تسعون يوما", now=NOW + timedelta(days=1))["verdict"] == "stale"
    finally:
        db.close()


def test_verify_is_deterministic_for_same_now(client: TestClient) -> None:
    claim = "الإجازة السنوية لكل عضو في الفريق واحد وعشرون يوما"
    first = client.post("/api/almanac/verify", json={"claim": claim, "now": NOW_ISO}).json()
    second = client.post("/api/almanac/verify", json={"claim": claim, "now": NOW_ISO}).json()
    assert first == second
    assert first["verdict"] == "supported"


# --- provenance enforcement (D10) -----------------------------------------


def test_ingest_without_source_is_rejected(store: AlmanacStore) -> None:
    with pytest.raises(ValueError):
        store.ingest(slug="x", title="t", body="b", source_path="", fetched_at=NOW)
    with pytest.raises(ValueError):
        store.ingest(slug="x", title="t", body="b", source_path="p", fetched_at="")


def test_ingest_is_idempotent_and_reindexes(tmp_path: Path) -> None:
    db = AlmanacStore(tmp_path / "world.db")
    try:
        first = db.ingest(
            slug="live",
            title="حالة المشروع",
            body="المشروع في مرحلة البناء الأولى",
            source_path="docs/live.md",
            fetched_at=iso(1),
            now=NOW,
        )
        second = db.ingest(
            slug="live",
            title="حالة المشروع",
            body="المشروع انتقل إلى مرحلة النشر",
            source_path="docs/live.md",
            fetched_at=iso(0),
            now=NOW,
        )
        assert first["doc_id"] == second["doc_id"]
        assert db.count() == 1
        assert db.search("البناء", now=NOW) == []
        hits = db.search("مرحلة النشر", now=NOW)
        assert hits and hits[0]["slug"] == "live"
        assert "البناء" not in hits[0]["snippet"]
    finally:
        db.close()


def test_helpers_normalize_arabic() -> None:
    assert normalize_token("الأسبوعيّ") == "الاسبوعي"
    assert tokenize("الإجازة") == ["الاجازه"]
    assert age_days(iso(95), now=NOW) == 95
