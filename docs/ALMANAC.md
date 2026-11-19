# Reading Room / Almanac (ticket WORLD-7)

The almanac is the world's **memory of the outside**: every fact the agents can
repeat carries a `source_path` and a `fetched_at`. Plan rule **D10** — *facts are
cited; unsourced = opinion* — is enforced at the storage layer, not just in the
HTTP handler, so no caller can insert an uncited fact.

Storage is one SQLite file, `world.db` (repo root by default, override with
`WORLD_DB`):

| object | purpose |
|---|---|
| `docs` | canon: `slug`, `title`, `body`, `source_path`, `fetched_at`, `sha256`, `created_at` |
| `docs_fts` | FTS5 index over `title`/`body` **plus** normalised twins `title_norm`/`body_norm`, `rowid = docs.id` |
| `meta` | `schema_version` |

`docs_fts` is a standalone FTS5 table (not external-content) because SQLite's
tokeniser cannot fold Arabic hamza/alef/ta-marbuta variants and triggers cannot
call Python. `AlmanacStore` therefore writes both copies in one transaction;
`rebuild_fts()` repairs the index from `docs` at any time.

## Endpoints

| method & path | returns |
|---|---|
| `GET /api/almanac/search?q=…&limit=10` | `{query, count, stale_after_days, results[]}`; each hit has `doc_id, slug, title, snippet, source_path, fetched_at, age_days, stale, score` |
| `GET /api/almanac/doc/{id or slug}` | full document incl. `body`, `source_path`, `fetched_at`, `sha256`; slugs may contain `/` (`…/doc/office-world/infra-port`); `404` when unknown |
| `POST /api/almanac/verify` `{"claim": "…", "now": "ISO-8601 (optional)"}` | `{claim, verdict, reason, source_path, fetched_at, age_days, stale, coverage, streak, numbers, doc, candidates, best_candidate?}` |

Mounting in the engine app:

```python
from app.routers.almanac import router as almanac_router
app.include_router(almanac_router)
```

## Verdicts (deterministic, no model in the loop)

| verdict | meaning |
|---|---|
| `supported` | one document contains **every content term** of the claim, no number conflict, and `fetched_at` ≤ 90 days old |
| `stale` | same match, but `age_days > 90` → re-read the source before repeating it |
| `unsupported` | nothing in the almanac covers the claim → it is an **opinion**, and no citation is invented. `reason` says why: `no_source`, `weak_match`, `numeric_conflict`, `empty_claim` |

`unsupported` means *"not verifiable from a cited source"*, never *"false"*.
The check is deliberately conservative: a missed support is recoverable, an
invented citation is not. An `unsupported` response carries **no** citation —
`source_path`/`fetched_at`/`doc` are `null`, and the closest document appears
under `best_candidate` so a renderer cannot present it as backing the claim.

Matching details:

* tokens are lower-cased, diacritics/tatweel stripped, `أإآ→ا`, `ى→ي`, `ة→ه`;
* `ال` (article) and `و` (conjunction) prefixes are matched both ways;
* numbers are compared after parsing Arabic number words, so
  «عشرون يوما» (20) does **not** pass against «واحد وعشرون يوما» (21);
* FTS5 search widens in three passes: all terms → all terms as prefixes → any term.

## Ops / seeding (no fourth endpoint)

```bash
cd apps/world-engine
python -m app.almanac_store --db ../../world.db --ingest seeds/almanac.json
python -m app.almanac_store --db ../../world.db --search "آلة الحالة"
python -m app.almanac_store --db ../../world.db --verify "منفذ العرض هو 8095"
```

`seeds/almanac.json` holds cited facts lifted from `PLAN.md` with their real
`source_path` values.

## Tests

```bash
python -m pytest tests/test_almanac.py -q
```

Acceptance coverage: known phrase → hit with `source_path` + `fetched_at`
(AC1) · false claim → `unsupported` with no citation (AC2) · `fetched_at`
older than 90 days → `stale` (AC3), plus boundary-at-90, re-index, upsert and
provenance-is-mandatory cases.

No Docker, no deploy: this wave ships code and a file-backed store only.
