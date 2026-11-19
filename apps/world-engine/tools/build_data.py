"""Generate the two data files the engine reads at runtime (WORLD-11).

The engine must not invent facts about the office: the panel's `thought` line is
composed from **real** rows only — the ticket an agent holds, or the block of the
day the contract schedule puts them in. Neither the contract YAML nor the Atlas
ledger is readable from the engine image (no PyYAML, no ledger mount), so both are
projected here into small JSON files under ``app/data/`` that ship with the code
(``COPY app ./app``), exactly like the page's inline contract block.

  app/data/schedule.json   <- world/dayplan.yaml  (deterministic, ``--check``-able)
  app/data/tickets.json    <- the Atlas ledger    (carries source + fetched_at)

Usage::

    python3 apps/world-engine/tools/build_data.py --schedule
    python3 apps/world-engine/tools/build_data.py --tickets ~/ai-team/projects/atlas/data/atlas.db
    python3 apps/world-engine/tools/build_data.py --check

Exit codes: 0 = written / in sync, 1 = drift or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ENGINE_ROOT = HERE.parent
REPO_ROOT = ENGINE_ROOT.parents[1]
DAYPLAN = REPO_ROOT / "world" / "dayplan.yaml"
DATA_DIR = ENGINE_ROOT / "app" / "data"
SCHEDULE_JSON = DATA_DIR / "schedule.json"
TICKETS_JSON = DATA_DIR / "tickets.json"

#: Only the fields the thought composer needs; the day plan stays the source.
SCHEDULE_KEYS = ("id", "at", "type", "topic", "kind", "room", "planned_ticks", "participants")

#: The projects whose tickets the sim can put on somebody's desk (``sim._TICKETS``).
TICKET_PREFIXES = ("WORLD-", "CHAT-")


def schedule_payload(dayplan_path: Path = DAYPLAN) -> dict[str, Any]:
    """Project the contract's day schedule into the engine's block list."""
    import yaml  # local import: only this generator (and its test) needs PyYAML

    cfg = yaml.safe_load(dayplan_path.read_text(encoding="utf-8"))
    entries = []
    for entry in cfg["schedule"]:
        out: dict[str, Any] = {"id": entry["id"], "at": entry["at"], "type": entry["type"]}
        for key in SCHEDULE_KEYS[3:]:
            if key in entry:
                out[key] = entry[key]
        entries.append(out)
    return {
        "source": "world/dayplan.yaml",
        "contract_version": cfg["version"],
        "day": {"start": cfg["sim"]["day_start"], "end": cfg["sim"]["day_end"]},
        "entries": entries,
    }


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def ticket_payload(db_path: Path, *, now: str | None = None) -> dict[str, Any]:
    """Snapshot the office tickets from the Atlas ledger (read-only)."""
    if not db_path.exists():
        raise FileNotFoundError(f"no Atlas ledger at {db_path}")
    where = " OR ".join("key LIKE ?" for _ in TICKET_PREFIXES)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT key, title, status, updated_at FROM tickets "
            f"WHERE ({where}) AND deleted_at IS NULL",
            tuple(f"{prefix}%" for prefix in TICKET_PREFIXES),
        ).fetchall()
    finally:
        con.close()
    tickets = [
        {
            "key": row["key"],
            "title": row["title"],
            "status": row["status"],
            "updated_at": row["updated_at"],
        }
        for row in sorted(rows, key=lambda r: (r["key"].split("-")[0], int(r["key"].split("-")[-1])))
    ]
    return {
        "source": f"atlas:tickets?prefix={','.join(TICKET_PREFIXES)}",
        "fetched_at": now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(tickets),
        "tickets": tickets,
    }


def write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(payload), encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size} bytes)")


def check(dayplan_path: Path = DAYPLAN) -> int:
    expected = dumps(schedule_payload(dayplan_path))
    actual = SCHEDULE_JSON.read_text(encoding="utf-8") if SCHEDULE_JSON.exists() else ""
    if actual == expected:
        print(f"OK — {SCHEDULE_JSON.relative_to(REPO_ROOT)} matches {dayplan_path.relative_to(REPO_ROOT)}")
        return 0
    print("DRIFT — app/data/schedule.json differs from world/dayplan.yaml", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", action="store_true", help="write app/data/schedule.json")
    parser.add_argument("--tickets", metavar="ATLAS_DB", help="write app/data/tickets.json from a ledger")
    parser.add_argument("--check", action="store_true", help="diff schedule.json against the contract")
    args = parser.parse_args(argv)

    if args.schedule:
        write(SCHEDULE_JSON, schedule_payload())
    if args.tickets:
        write(TICKETS_JSON, ticket_payload(Path(args.tickets).expanduser()))
    if args.check:
        return check()
    if not (args.schedule or args.tickets):
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
