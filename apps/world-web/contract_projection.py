"""Project `world/dayplan.yaml` into the JSON block the static page renders from.

The renderer (W3) has no build step: `static/index.html` ships with the contract
projection inlined in `<script id="world-contract" type="application/json">`.
This module is the only thing allowed to produce that block, so the page cannot
drift from the frozen contract by hand-editing names, rooms or geometry.

Usage:
    python3 apps/world-web/contract_projection.py --json      # compact JSON
    python3 apps/world-web/contract_projection.py --check     # diff vs index.html

Exit codes: 0 = in sync, 1 = drifted / unreadable contract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DAYPLAN = REPO_ROOT / "world" / "dayplan.yaml"
INDEX_HTML = Path(__file__).resolve().parent / "static" / "index.html"

TOKEN = "__WORLD_CONTRACT_JSON__"
BLOCK_RE = re.compile(
    r'<script id="world-contract" type="application/json">(.*?)</script>',
    re.DOTALL,
)

# Only the fields the renderer is allowed to read. Anything else in the YAML
# (guards, codes, out_of_scope, ...) stays server-side.
AGENT_KEYS = ("id", "name", "role", "desk", "home_room", "secondary_room")
ROOM_KEYS = ("id", "name", "kind", "capacity", "rect", "anchor")


def _schedule_entry(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": entry["id"], "at": entry["at"], "type": entry["type"]}
    for key in ("topic", "kind", "room", "rule", "participants", "planned_ticks", "emit"):
        if key in entry:
            out[key] = entry[key]
    return out


def project(dayplan_path: Path = DAYPLAN) -> dict[str, Any]:
    """Return the renderer's view of the frozen contract."""
    import yaml  # local import: only this module and the drift test need PyYAML

    cfg = yaml.safe_load(dayplan_path.read_text(encoding="utf-8"))
    sm = cfg["state_machine"]
    movement = cfg["movement"]
    sim = cfg["sim"]

    return {
        "contract_version": cfg["version"],
        "source": "world/dayplan.yaml",
        "ticket": cfg["contract"]["ticket"],
        "sim": {
            "tick_seconds": sim["tick_seconds"],
            "t0": sim["t0"],
            "day_start": sim["day_start"],
            "day_end": sim["day_end"],
            "day_length_ticks": sim["day_length_ticks"],
            "seed": sim["seed"],
        },
        "canvas": {"w": movement["canvas"]["w"], "h": movement["canvas"]["h"]},
        "states": list(sm["states"]),
        "initial_state": sm["initial"],
        "canonical_cycle": list(sm["canonical_cycle"]),
        "event_types": list(cfg["events"]["envelope"]["type"]["enum"]),
        "agents": [{k: a[k] for k in AGENT_KEYS} for a in cfg["agents"]],
        "rooms": [{k: r[k] for k in ROOM_KEYS} for r in cfg["rooms"]],
        "stations": [
            {"id": s["id"], "agent": s["agent"], "room": s["room"], "x": s["x"], "y": s["y"]}
            for s in cfg["stations"]
        ],
        "schedule": [_schedule_entry(s) for s in cfg["schedule"]],
    }


def dumps(projection: dict[str, Any]) -> str:
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def embedded(path: Path = INDEX_HTML) -> dict[str, Any]:
    """Read the projection actually shipped inside the page."""
    html = path.read_text(encoding="utf-8")
    match = BLOCK_RE.search(html)
    if match is None:
        raise ValueError(f"{path}: no <script id=\"world-contract\"> block")
    return json.loads(match.group(1))


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the compact projection")
    parser.add_argument("--check", action="store_true", help="compare index.html with the YAML")
    args = parser.parse_args(argv)

    expected = dumps(project(DAYPLAN))
    if args.json or not args.check:
        print(expected)
        return 0

    actual = BLOCK_RE.search(INDEX_HTML.read_text(encoding="utf-8"))
    got = actual.group(1).strip() if actual else TOKEN
    if got == expected:
        print(f"OK — index.html contract block matches {DAYPLAN.relative_to(REPO_ROOT)}")
        return 0
    print("DRIFT — index.html contract block differs from world/dayplan.yaml", file=sys.stderr)
    want, have = json.loads(expected), json.loads(got) if got.startswith("{") else {}
    for key in sorted(set(want) | set(have)):
        if want.get(key) != have.get(key):
            print(f"  field `{key}` drifted", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
