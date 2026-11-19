"""Citation-first fact store for the Office World Reading Room (WORLD-7).

Plan rule **D10**: any claim about the outside world must carry
``source_path`` + ``fetched_at``; a fact with no source is an opinion.
This module owns the ``world.db`` schema — ``docs`` (the canon) plus
``docs_fts`` (SQLite FTS5, external-content, kept in sync by triggers) — and
the three operations the Reading Room needs: ``ingest``, ``search``, ``verify``.

Determinism: everything is a pure function of the stored rows plus an explicit
``now``. No LLM, no network, no wall-clock reads when ``now`` is supplied.

CLI (ops/seed path, no fourth endpoint)::

    python -m app.almanac_store --db world.db --ingest seed.json
    python -m app.almanac_store --db world.db --search "الاجتماع الأسبوعي"
    python -m app.almanac_store --db world.db --verify "الاجتماع يوم الأحد"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1
STALE_AFTER_DAYS = 90
DEFAULT_DB_PATH = Path(os.environ.get("WORLD_DB", "world.db"))

# --- tokenisation -----------------------------------------------------------

_TOKEN_RE = re.compile(r"[0-9A-Za-z\u0600-\u06FF]+")
_DIACRITICS_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")

# Deliberately small and domain-neutral: words that carry no retrieval signal.
_STOPWORDS = frozenset(
    """
    في من على الى إلى عن ان أن إن كان كانت يكون تكون هذا هذه ذلك تلك التي الذي
    مع او أو ثم كل بعد قبل بين هو هي هم ما لا لم لن قد كما عند حتى به بها له لها
    وقد وكان هناك ايضا أيضا وهو وهي
    the a an of in on at to for and or is are was were be been being it this that
    these those with by from as not no but if then than into over under about
    """.split()
)

_NORMALIZE_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي",
    }
)


def normalize_token(token: str) -> str:
    """Lower-case, strip Arabic diacritics/tatweel, unify alef/ya/ta-marbuta."""
    return _DIACRITICS_RE.sub("", token).lower().translate(_NORMALIZE_MAP)


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return [normalize_token(m.group(0)) for m in _TOKEN_RE.finditer(text)]


def content_tokens(text: str | None) -> list[str]:
    """Tokens minus stopwords — what a claim is actually about."""
    return [t for t in tokenize(text) if t and t not in _STOPWORDS]


def token_variants(token: str) -> set[str]:
    """Match with/without the Arabic article (ال) and conjunction (و)."""
    variants = {token}
    if len(token) > 3 and token.startswith("ال"):
        variants.add(token[2:])
    elif len(token) >= 3:
        variants.add("ال" + token)
    if len(token) > 4 and token.startswith("و"):
        stripped = token[1:]
        variants.add(stripped)
        if len(stripped) > 3 and stripped.startswith("ال"):
            variants.add(stripped[2:])
    return variants


# --- numbers (used by verification to catch 20-vs-21 style claims) ---------

_UNITS = {
    "صفر": 0,
    "واحد": 1,
    "واحده": 1,
    "اثنان": 2,
    "اثنين": 2,
    "ثلاثه": 3,
    "اربعه": 4,
    "خمسه": 5,
    "سته": 6,
    "سبعه": 7,
    "ثمانيه": 8,
    "تسعه": 9,
    "عشره": 10,
    # ordinals
    "الاول": 1,
    "الاولي": 1,
    "الثاني": 2,
    "الثالث": 3,
    "الرابع": 4,
    "الخامس": 5,
    "السادس": 6,
    "السابع": 7,
    "الثامن": 8,
    "التاسع": 9,
    "العاشر": 10,
    "العاشره": 10,
}

_TENS = {
    "عشرون": 20,
    "ثلاثون": 30,
    "اربعون": 40,
    "خمسون": 50,
    "ستون": 60,
    "سبعون": 70,
    "ثمانون": 80,
    "تسعون": 90,
}


def _strip_conjunction(token: str) -> str:
    return token[1:] if len(token) > 4 and token.startswith("و") else token


def extract_numbers(text: str | None) -> list[int]:
    """Numbers in a text: ASCII/Arabic-Indic digits and Arabic number words.

    Handles ``٢١``, ``21``, ``عشرون``, ``واحد وعشرون`` (→ 21) and ordinals such
    as ``العاشرة`` (→ 10). Used only for the conflict check in ``verify``.
    """
    tokens = [t.replace("٠", "0").replace("١", "1").replace("٢", "2").replace("٣", "3")
              .replace("٤", "4").replace("٥", "5").replace("٦", "6").replace("٧", "7")
              .replace("٨", "8").replace("٩", "9") for t in tokenize(text)]
    found: list[int] = []
    i, n = 0, len(tokens)
    while i < n:
        token = tokens[i]
        if token.isdigit():
            found.append(int(token))
            i += 1
            continue
        base = _strip_conjunction(token)
        nxt = _strip_conjunction(tokens[i + 1]) if i + 1 < n else ""
        if base in _UNITS and base != "عشره" and nxt == "عشر":
            found.append(10 + _UNITS[base])
            i += 2
            continue
        if base in _UNITS and nxt in _TENS:
            found.append(_TENS[nxt] + _UNITS[base])
            i += 2
            continue
        if base in _UNITS:
            found.append(_UNITS[base])
            i += 1
            continue
        if base in _TENS:
            found.append(_TENS[base])
            i += 1
            continue
        i += 1
    return found



# --- time helpers -----------------------------------------------------------


def parse_timestamp(value: str | datetime | None) -> datetime:
    """Parse ISO-8601 (``Z`` accepted), date-only, or datetime → aware UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:  # date-only fallback
            try:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"invalid timestamp: {value!r}") from exc
    else:
        raise ValueError(f"invalid timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def age_days(fetched_at: str | datetime, now: str | datetime | None = None) -> int:
    now_dt = parse_timestamp(now) if now is not None else datetime.now(timezone.utc)
    delta = now_dt - parse_timestamp(fetched_at)
    return max(delta.days, 0)


# --- schema -----------------------------------------------------------------

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    source_path TEXT    NOT NULL CHECK (length(trim(source_path)) > 0),
    fetched_at  TEXT    NOT NULL,
    sha256      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

-- ``docs_fts`` is a *standalone* FTS5 index (not external-content) so it can
-- hold a normalised twin of every column: SQLite's tokeniser cannot fold
-- Arabic hamza/alef/ta-marbuta variants, and a trigger cannot call Python,
-- so ``AlmanacStore`` keeps this index in sync itself. ``rowid`` == docs.id.
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title,
    body,
    title_norm,
    body_norm,
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class AlmanacStore:
    """SQLite-backed, citation-first fact store (one file: ``world.db``)."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if str(self.db_path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # -- lifecycle ---------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "AlmanacStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- write -------------------------------------------------------------

    def normalize_text(self, text: str | None) -> str:
        """Space-joined normalised tokens, used for the ``*_norm`` FTS columns."""
        return " ".join(tokenize(text))

    def _fts_upsert(self, doc_id: int, title: str, body: str) -> None:
        self._conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
        self._conn.execute(
            "INSERT INTO docs_fts(rowid, title, body, title_norm, body_norm) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc_id, title, body, self.normalize_text(title), self.normalize_text(body)),
        )

    def rebuild_fts(self) -> int:
        """Re-index every row from ``docs`` (idempotent repair path)."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM docs_fts")
            rows = self._conn.execute("SELECT id, title, body FROM docs").fetchall()
            for row in rows:
                self._fts_upsert(row["id"], row["title"], row["body"])
        return len(rows)

    def ingest(
        self,
        *,
        slug: str,
        title: str,
        body: str,
        source_path: str,
        fetched_at: str | datetime,
        now: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Insert or replace one cited document.

        Raises ``ValueError`` when the citation is missing — D10 is enforced at
        the storage layer, not just in the API, so no caller can smuggle in an
        unsourced fact.
        """
        if not slug or not slug.strip():
            raise ValueError("slug is required")
        if not title or not title.strip():
            raise ValueError("title is required")
        if not body or not body.strip():
            raise ValueError("body is required")
        if not source_path or not str(source_path).strip():
            raise ValueError("source_path is required (D10: unsourced = opinion)")
        fetched = parse_timestamp(fetched_at)
        stamp = to_iso(parse_timestamp(now) if now is not None else datetime.now(timezone.utc))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO docs(slug, title, body, source_path, fetched_at, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    title=excluded.title,
                    body=excluded.body,
                    source_path=excluded.source_path,
                    fetched_at=excluded.fetched_at,
                    sha256=excluded.sha256
                """,
                (
                    slug.strip(),
                    title.strip(),
                    body.strip(),
                    str(source_path).strip(),
                    to_iso(fetched),
                    digest,
                    stamp,
                ),
            )
            row = self._conn.execute(
                "SELECT id, title, body FROM docs WHERE slug = ?", (slug.strip(),)
            ).fetchone()
            assert row is not None
            self._fts_upsert(row["id"], row["title"], row["body"])
        doc = self.get_doc(slug)
        assert doc is not None
        return doc

    # -- read --------------------------------------------------------------

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT count(*) FROM docs").fetchone()[0])

    def get_doc(
        self,
        doc_ref: str | int,
        *,
        now: str | datetime | None = None,
    ) -> dict[str, Any] | None:
        """Fetch by numeric id or by slug; returns ``None`` when absent."""
        if isinstance(doc_ref, int) or (isinstance(doc_ref, str) and doc_ref.isdigit()):
            sql, param = "SELECT * FROM docs WHERE id = ?", int(doc_ref)
        else:
            sql, param = "SELECT * FROM docs WHERE slug = ?", str(doc_ref)
        with self._lock:
            row = self._conn.execute(sql, (param,)).fetchone()
        if row is None:
            return None
        return self._row_to_doc(row, now=now)

    def _row_to_doc(self, row: sqlite3.Row, *, now: str | datetime | None = None) -> dict[str, Any]:
        doc = dict(row)
        days = age_days(doc["fetched_at"], now=now)
        doc["doc_id"] = doc.pop("id")
        doc["age_days"] = days
        doc["stale"] = days > STALE_AFTER_DAYS
        return doc

    @staticmethod
    def _match_expression(tokens: Sequence[str], mode: str) -> str:
        groups: list[str] = []
        for token in tokens:
            variants = sorted(token_variants(token))
            if mode == "strict":
                inner = " OR ".join(f'"{v}"' for v in variants)
            else:  # prefix / any
                inner = " OR ".join(f'"{v}"*' for v in variants)
            groups.append(f"({inner})")
        joiner = " OR " if mode == "any" else " AND "
        return joiner.join(groups)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        now: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 search, strictest match mode first, then widening.

        Order: all terms (with/without ال) → all terms as prefixes → any term.
        Ranking is ``bm25`` with the title weighted 4×; Arabic and English are
        tokenised by the same pipeline so one index serves both.
        """
        tokens = content_tokens(query) or tokenize(query)
        if not tokens:
            return []
        tokens = list(dict.fromkeys(tokens))[:16]
        limit = max(1, min(int(limit), 50))

        rows: list[sqlite3.Row] = []
        for mode in ("strict", "prefix", "any"):
            expression = self._match_expression(tokens, mode)
            sql = """
                SELECT d.id, d.slug, d.title, d.source_path, d.fetched_at,
                       snippet(docs_fts, 1, '[', ']', '…', 12) AS snippet,
                       bm25(docs_fts, 4.0, 1.0, 4.0, 1.0) AS rank
                FROM docs_fts
                JOIN docs d ON d.id = docs_fts.rowid
                WHERE docs_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            try:
                with self._lock:
                    rows = self._conn.execute(sql, (expression, limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                break
        return [self._row_to_hit(row, now=now) for row in rows]

    @staticmethod
    def _row_to_hit(row: sqlite3.Row, *, now: str | datetime | None = None) -> dict[str, Any]:
        days = age_days(row["fetched_at"], now=now)
        return {
            "doc_id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "snippet": row["snippet"] or "",
            "source_path": row["source_path"],
            "fetched_at": row["fetched_at"],
            "age_days": days,
            "stale": days > STALE_AFTER_DAYS,
            "score": round(-float(row["rank"]), 6),
        }

    # -- verification ------------------------------------------------------

    @staticmethod
    def _coverage_and_streak(
        claim_tokens: Sequence[str], doc_tokens: Sequence[str]
    ) -> tuple[float, int]:
        if not claim_tokens or not doc_tokens:
            return 0.0, 0
        window = doc_tokens[:6000]
        doc_variants: set[str] = set()
        index: dict[str, list[int]] = {}
        for position, token in enumerate(window):
            variants = token_variants(token)
            doc_variants |= variants
            for variant in variants:
                index.setdefault(variant, []).append(position)
        matched = sum(1 for t in claim_tokens if token_variants(t) & doc_variants)
        coverage = matched / len(claim_tokens)

        # Longest run of consecutive claim tokens found consecutively in the doc.
        best = 0
        for start in range(len(claim_tokens)):
            variants = token_variants(claim_tokens[start])
            positions: set[int] = set()
            for variant in variants:
                positions.update(index.get(variant, ()))
            for position in positions:
                streak = 1
                while start + streak < len(claim_tokens) and position + streak < len(doc_tokens):
                    if doc_tokens[position + streak] not in token_variants(claim_tokens[start + streak]):
                        break
                    streak += 1
                best = max(best, streak)
            if best >= len(claim_tokens):
                break
        return coverage, best

    def verify(
        self,
        claim: str,
        *,
        now: str | datetime | None = None,
        limit: int = 20,
        min_coverage: float = 1.0,
        min_streak: int = 0,
    ) -> dict[str, Any]:
        """Decide whether ``claim`` is supported by a cited document.

        Verdicts:
          * ``supported``   — one document contains every content term of the
                              claim, no number conflicts, and is fresh;
          * ``stale``       — as above, but ``fetched_at`` is older than
                              ``STALE_AFTER_DAYS`` (90) ⇒ re-read the source;
          * ``unsupported`` — nothing in the almanac covers the claim ⇒ it is
                              an opinion, and no citation is invented for it.

        This is a *citational* check, not an NLI model: ``unsupported`` means
        "not verifiable from a cited source", never "false". Conservative on
        purpose — a missed support is recoverable, an invented citation is not.
        """
        claim = (claim or "").strip()
        tokens = content_tokens(claim)
        claim_numbers = extract_numbers(claim)
        result: dict[str, Any] = {
            "claim": claim,
            "verdict": "unsupported",
            "reason": "no_source",
            "source_path": None,
            "fetched_at": None,
            "age_days": None,
            "stale": None,
            "coverage": 0.0,
            "streak": 0,
            "numbers": {"claim": sorted(set(claim_numbers)), "doc": []},
            "doc": None,
            "candidates": 0,
        }
        if not tokens:
            result["reason"] = "empty_claim"
            return result

        hits = self.search(claim, limit=limit, now=now)
        result["candidates"] = len(hits)
        if not hits:
            return result

        best: tuple[float, int, dict[str, Any]] | None = None
        for hit in hits:
            doc = self.get_doc(hit["doc_id"], now=now)
            if doc is None:
                continue
            doc_tokens = tokenize(f"{doc['title']} {doc['body']}")
            coverage, streak = self._coverage_and_streak(tokens, doc_tokens)
            score = (coverage, streak)
            if best is None or score > (best[0], best[1]):
                best = (coverage, streak, {**hit, "stale": doc["stale"], "age_days": doc["age_days"]})

        if best is None:
            return result

        coverage, streak, hit = best
        doc = self.get_doc(hit["doc_id"], now=now) or {}
        doc_numbers = sorted(set(extract_numbers(f"{doc.get('title', '')} {doc.get('body', '')}")))
        result.update(
            {
                "coverage": round(coverage, 3),
                "streak": streak,
                "source_path": hit["source_path"],
                "fetched_at": hit["fetched_at"],
                "age_days": hit["age_days"],
                "stale": hit["stale"],
                "numbers": {"claim": sorted(set(claim_numbers)), "doc": doc_numbers},
                "doc": {"doc_id": hit["doc_id"], "slug": hit["slug"], "title": hit["title"]},
            }
        )
        if claim_numbers:
            conflicting = sorted({n for n in claim_numbers if n not in doc_numbers})
            if conflicting and coverage >= 0.5:
                result["reason"] = "numeric_conflict"
                result["conflicting_numbers"] = conflicting
                return self._demote_citation(result)
        if coverage < min_coverage:
            result["reason"] = "weak_match"
            return self._demote_citation(result)
        if min_streak and streak < min(min_streak, len(tokens)):
            result["reason"] = "weak_match"
            return self._demote_citation(result)
        if hit["stale"]:
            result["reason"] = f"stale_source (> {STALE_AFTER_DAYS} days)"
            result["verdict"] = "stale"
            return result
        result["reason"] = "matched"
        result["verdict"] = "supported"
        return result

    @staticmethod
    def _demote_citation(result: dict[str, Any]) -> dict[str, Any]:
        """An unsupported claim must not appear to carry a citation.

        The closest document is still reported, but under ``best_candidate`` so
        a renderer cannot mistake it for a source backing the claim.
        """
        result["best_candidate"] = {
            "source_path": result["source_path"],
            "fetched_at": result["fetched_at"],
            "age_days": result["age_days"],
            "stale": result["stale"],
            "doc": result["doc"],
            "coverage": result["coverage"],
            "streak": result["streak"],
        }
        result.update(source_path=None, fetched_at=None, age_days=None, stale=None, doc=None)
        result["verdict"] = "unsupported"
        return result


# --- CLI --------------------------------------------------------------------


def _load_seed(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    docs = payload.get("docs") if isinstance(payload, dict) else payload
    if not isinstance(docs, list):
        raise SystemExit(f"seed file {path} must be a list or {{'docs': [...]}}")
    return docs


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Almanac store CLI (WORLD-7)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--ingest", metavar="JSON", help="JSON file: list of docs or {'docs': [...]}")
    parser.add_argument("--search", metavar="QUERY")
    parser.add_argument("--verify", metavar="CLAIM")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(list(argv) if argv is not None else None)

    store = AlmanacStore(args.db)
    try:
        if args.ingest:
            for doc in _load_seed(Path(args.ingest)):
                store.ingest(**doc)
            print(f"ingested: {store.count()} docs total")
        if args.search:
            print(json.dumps(store.search(args.search, limit=args.limit), ensure_ascii=False, indent=2))
        if args.verify:
            print(json.dumps(store.verify(args.verify), ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
