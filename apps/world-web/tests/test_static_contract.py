"""W3 render gate: the static page must render exactly the frozen contract.

`apps/world-web/static/index.html` carries the contract projection inline (no build
step), so these tests are what stops the page from drifting away from
`world/dayplan.yaml`: the embedded block must be byte-identical to the projector
output, and the DOM hooks that `docs/AC.md` asserts (W3-01 … W3-06) must exist.
"""

from __future__ import annotations

import re

import pytest

from contract_projection import (
    BLOCK_RE,
    DAYPLAN,
    INDEX_HTML,
    dumps,
    embedded,
    project,
)

HTML = INDEX_HTML.read_text(encoding="utf-8")
CONTRACT = embedded()


@pytest.fixture(scope="module")
def projection() -> dict:
    return project(DAYPLAN)


def test_contract_block_is_the_generated_projection(projection: dict) -> None:
    """AC-W3-02 (no local invention): the page cannot disagree with the YAML."""
    match = BLOCK_RE.search(HTML)
    assert match is not None, 'index.html lost its <script id="world-contract"> block'
    assert match.group(1).strip() == dumps(projection)


def test_page_meta_matches_contract_version(projection: dict) -> None:
    assert CONTRACT["contract_version"] == projection["contract_version"]
    assert CONTRACT["source"] == "world/dayplan.yaml"
    assert CONTRACT["ticket"] == "WORLD-1"


def test_frozen_vocabulary_is_carried_verbatim(projection: dict) -> None:
    assert CONTRACT["agents"] == projection["agents"]
    assert CONTRACT["rooms"] == projection["rooms"]
    assert CONTRACT["stations"] == projection["stations"]
    assert CONTRACT["states"] == ["idle", "working", "review", "break", "meeting"]
    assert CONTRACT["initial_state"] == "idle"
    assert CONTRACT["canonical_cycle"] == ["idle", "working", "review", "break", "meeting", "idle"]
    assert len(CONTRACT["event_types"]) == 8
    assert CONTRACT["event_types"] == projection["event_types"]


def test_six_agents_five_rooms_and_a_floor_that_fits() -> None:
    agents, rooms, stations = CONTRACT["agents"], CONTRACT["rooms"], CONTRACT["stations"]
    assert len(agents) == 6
    assert len(rooms) == 5
    assert len(stations) == 6

    room_ids = {room["id"] for room in rooms}
    assert {a["home_room"] for a in agents} <= room_ids
    assert {a["secondary_room"] for a in agents} <= room_ids

    bullpen = next(room for room in rooms if room["id"] == "bullpen")
    rect = bullpen["rect"]
    seats = set()
    for station in stations:
        assert station["room"] == "bullpen"
        assert rect["x"] <= station["x"] <= rect["x"] + rect["w"]
        assert rect["y"] <= station["y"] <= rect["y"] + rect["h"]
        seats.add((station["x"], station["y"]))
    assert len(seats) == 6, "two desks share a seat"
    assert {s["agent"] for s in stations} == {a["id"] for a in agents}
    assert {s["id"] for s in stations} == {a["desk"] for a in agents}

    canvas = CONTRACT["canvas"]
    assert canvas == {"w": 1200, "h": 700}
    for room in rooms:
        r = room["rect"]
        assert r["x"] + r["w"] <= canvas["w"] and r["y"] + r["h"] <= canvas["h"]
        assert all(isinstance(r[k], int) for k in ("x", "y", "w", "h")), "DT-3: integer geometry"


def test_schedule_used_for_next_milestone_is_inside_the_day(projection: dict) -> None:
    schedule = CONTRACT["schedule"]
    assert schedule == projection["schedule"]
    assert [entry["id"] for entry in schedule] == [e["id"] for e in projection["schedule"]]
    start, end = CONTRACT["sim"]["day_start"], CONTRACT["sim"]["day_end"]

    def minutes(hhmm: str) -> int:
        hours, mins = hhmm.split(":")
        return int(hours) * 60 + int(mins)

    times = [minutes(entry["at"]) for entry in schedule]
    assert times == sorted(times), "schedule must be chronological for 'next up' to be meaningful"
    assert all(minutes(start) <= t <= minutes(end) for t in times)


def test_dom_hooks_asked_for_by_ac_w3_exist() -> None:
    # AC-W3-01: one page, six dots, five rooms, state badges, office clock.
    assert '.agent-dot' in HTML
    assert '<svg id="floor"' in HTML
    assert 'id="layer-rooms"' in HTML and 'id="layer-desks"' in HTML and 'id="layer-agents"' in HTML
    assert 'id="clock"' in HTML
    assert CONTRACT["rooms"] and len(CONTRACT["rooms"]) == 5
    # six dots are created once, in contract declaration order
    assert re.search(r"AGENTS\.forEach\(\(agent\) => \{", HTML)
    # AC-W3-05: mission-control wall rows: ticket key / status / time in state.
    for hook in ('id="mc-body"', "mc-ticket", "mc-state", "mc-elapsed"):
        assert hook in HTML
    # AC-W3-06: click an agent -> panel with task_id, elapsed ticks, next milestone.
    for hook in ('id="detail-ticket"', 'id="detail-elapsed"', 'id="detail-next"'):
        assert hook in HTML
    assert "addEventListener('click', () => select(agent.id))" in HTML


def test_page_reads_the_wire_and_nothing_else() -> None:
    # §9.1 snapshot + §9.2 patches; both message shapes accepted, unknown types ignored.
    assert "/api/world/state" in HTML
    assert "/ws/world" in HTML
    for frame in ("'snapshot'", "'patch'", "'delta'", "'ping'"):
        assert frame in HTML
    # no build step, no framework, no bundle
    assert "<script src=" not in HTML
    assert "node_modules" not in HTML
    assert "package.json" not in HTML
    # contract-only vocabulary: no state name outside the five leaks in as a default
    for invented in ("'coffee'", "'busy'", "'away'", "'offline'", "'dnd'"):
        assert invented not in HTML.lower()


def test_no_arabic_in_repo_artifacts() -> None:
    """AC-W0-05 repo language rule (non-ASCII is only em dashes / arrows here)."""
    offenders = re.findall(r"[\u0600-\u06FF]", HTML)
    assert offenders == []
