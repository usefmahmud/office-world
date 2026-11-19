"""WORLD-11 — every agent's `thought` is derived from a real row, never generated.

Three guarantees, each checked against the thing that owns it:

* `app/data/schedule.json` is the contract's day plan, not a hand-kept copy
  (drift-checked against `world/dayplan.yaml`);
* `app/data/tickets.json` is a snapshot of the Atlas ledger — every ticket key the sim
  can emit has a real title and status, and the file says where it came from;
* the composer (`app/thought.py`) picks ticket → meeting → dayplan → state, and says so
  in `thought_source`, so the panel can never show a line nobody can trace.

No LLM, no network, and no wall-clock read anywhere in the chain (DT-2).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import main, sim, thought
from app.world import World

REPO_ROOT = Path(__file__).resolve().parents[3]
DAYPLAN = REPO_ROOT / "world" / "dayplan.yaml"
DATA = REPO_ROOT / "apps" / "world-engine" / "app" / "data"


def make_client() -> TestClient:
    return TestClient(main.app)


# --- the two data files --------------------------------------------------
def test_schedule_data_is_the_contract_day_plan() -> None:
    """AC-W0-05 / no drift: the engine's blocks are the YAML's blocks."""
    cfg = yaml.safe_load(DAYPLAN.read_text(encoding="utf-8"))
    entries = thought.SCHEDULE_ENTRIES
    assert [e["id"] for e in entries] == [s["id"] for s in cfg["schedule"]]
    for projected, contract in zip(entries, cfg["schedule"]):
        assert projected["at"] == contract["at"]
        assert projected["type"] == contract["type"]
        for key in ("topic", "kind", "room", "planned_ticks", "participants"):
            assert projected.get(key) == contract.get(key), f"{contract['id']}: {key} drifted"
    assert thought.CONTRACT_VERSION == cfg["version"]
    assert (thought.DAY_START, thought.DAY_END) == (cfg["sim"]["day_start"], cfg["sim"]["day_end"])


def test_the_data_files_are_committed_and_parse() -> None:
    schedule = json.loads((DATA / "schedule.json").read_text(encoding="utf-8"))
    tickets = json.loads((DATA / "tickets.json").read_text(encoding="utf-8"))
    assert schedule["source"] == "world/dayplan.yaml"
    assert schedule["entries"] == list(thought.SCHEDULE_ENTRIES)
    assert tickets["count"] == len(tickets["tickets"]) == len(thought.TICKET_CATALOG)
    assert tickets["count"] >= len({k for keys in sim._TICKETS.values() for k in keys})


def test_the_ticket_catalog_covers_every_ticket_the_sim_emits() -> None:
    """A working agent must never show "no title" because the catalog forgot its key."""
    world = World(seed=7)
    keys = {key for agent_keys in sim._TICKETS.values() for key in agent_keys}
    assert keys, "the sim emits no tickets at all"
    for key in sorted(keys):
        meta = thought.ticket_meta(key)
        assert meta is not None, f"{key}: the sim can hand this out but the catalog has no row"
        assert meta["title"], f"{key}: catalog row has no title"
        assert meta["status"], f"{key}: catalog row has no status"
    assert world.agents  # the roster the sim walks


def test_the_catalog_carries_its_provenance() -> None:
    assert thought.TICKETS_SOURCE.startswith("atlas:"), thought.TICKETS_SOURCE
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", thought.TICKETS_FETCHED_AT)
    row = thought.ticket_meta("WORLD-11")
    assert row is not None
    assert row["key"] == "WORLD-11" and row["title"] and row["status"]
    assert row["source"] == thought.TICKETS_SOURCE
    assert thought.ticket_meta("WORLD-9999") is None, "an unknown key must not invent a row"


# --- the composer --------------------------------------------------------
def test_working_thought_is_state_plus_key_plus_atlas_title() -> None:
    line, source = thought.compose(
        state="working", agent_id="omar", ticket="WORLD-11", title="Agent panel on click", now_minute=600
    )
    assert source == "ticket"
    assert line == "شغّال على WORLD-11 — Agent panel on click"


def test_review_uses_a_different_verb() -> None:
    working, _ = thought.compose(state="working", ticket="WORLD-11", title="Agent panel")
    review, _ = thought.compose(state="review", ticket="WORLD-11", title="Agent panel")
    assert working != review
    assert "WORLD-11" in working and "WORLD-11" in review


def test_a_missing_title_is_stated_instead_of_invented() -> None:
    line, source = thought.compose(state="working", ticket="ZZZ-1", title=None, now_minute=600)
    assert source == "ticket"
    assert "ZZZ-1" in line
    assert "—" not in line, "no title means no title em dash with nothing after it"
    assert line == thought.COPY["no_title"].format(verb=thought.COPY["state_verb"]["working"].format(ticket="ZZZ-1"))


def test_meeting_thought_is_the_open_topic() -> None:
    line, source = thought.compose(state="meeting", agent_id="hakim", meeting_topic="standup", now_minute=615)
    assert (line, source) == ("في اجتماع — standup", "meeting")
    bare, bare_source = thought.compose(state="meeting", agent_id="hakim", now_minute=615)
    assert bare == thought.COPY["meeting_topic"].format(topic="standup"), "the dayplan block names it"
    assert bare_source == "meeting"


def test_an_idle_agent_without_a_ticket_falls_back_to_the_dayplan() -> None:
    line, source = thought.compose(state="idle", agent_id="omar", now_minute=540)      # 09:00
    assert source == "dayplan"
    block = thought.slot_at(540, "omar")
    assert block is not None, "09:00 is the day_open block"
    assert line == thought.slot_text(block)

    waiting, waiting_source = thought.compose(state="idle", agent_id="omar", now_minute=690)  # 11:30
    assert waiting_source == "dayplan"
    assert "S3" in waiting and "13:00" in waiting, waiting


def test_a_break_thought_uses_its_own_block() -> None:
    line, source = thought.compose(state="break", agent_id="layla", now_minute=790)   # 13:10 lunch
    assert source == "dayplan"
    block = thought.slot_at(790, "layla")
    assert block is not None and block["type"] == "break"
    assert line == thought.slot_text(block)
    assert thought.COPY["kind"]["lunch"] in line


def test_someone_elses_block_is_not_this_agents_thought() -> None:
    """15:30 coffee is only layla/salim/yassin; hakim is not in it."""
    assert thought.slot_at(935, "layla") is not None
    assert thought.slot_at(935, "hakim") is None
    line, source = thought.compose(state="break", agent_id="hakim", now_minute=935)
    assert source == "state" and line == thought.COPY["state_verb"]["break"]


def test_unknown_state_gets_an_explicit_empty_line() -> None:
    assert thought.compose(state="vibing", agent_id="omar", now_minute=540) == (thought.COPY["no_data"], "none")
    assert thought.compose(state=None, agent_id="omar") == (thought.COPY["no_data"], "none")


def test_a_join_marker_is_not_a_topic() -> None:
    world = World(seed=7)
    world.emit("layla", "meeting_open", room="meeting-room", note="join")
    layla = next(a for a in world.snapshot()["agents"] if a["id"] == "layla")
    assert layla["state"] == "meeting"
    assert layla["thought"] == thought.COPY["state_verb"]["meeting"]
    assert layla["thought_source"] == "meeting"


def test_a_message_inside_a_meeting_never_rewrites_the_topic() -> None:
    world = World(seed=7)
    world.emit("hakim", "meeting_open", room="meeting-room", topic="standup")
    world.emit("hakim", "message", note="standup in 5")
    hakim = next(a for a in world.snapshot()["agents"] if a["id"] == "hakim")
    assert hakim["thought"] == thought.COPY["meeting_topic"].format(topic="standup")


def test_leaving_the_meeting_forgets_the_topic() -> None:
    world = World(seed=7)
    world.emit("hakim", "meeting_open", room="meeting-room", topic="standup")
    world.emit("hakim", "meeting_close")
    hakim = next(a for a in world.snapshot()["agents"] if a["id"] == "hakim")
    assert hakim["state"] == "idle"
    assert hakim["thought"] != thought.COPY["meeting_topic"].format(topic="standup")


def test_no_wall_clock_is_read_for_a_derived_line() -> None:
    """A bare World has no sim clock, so the dayplan half must not be consulted."""
    world = World(seed=7)
    snap = world.snapshot()
    assert {a["thought_source"] for a in snap["agents"]} == {"state"}
    assert all(a["thought"] for a in snap["agents"])


def test_every_thought_name_is_a_declared_source() -> None:
    world = World(seed=7, clock=main.sim_time, sim_minutes=main.sim_minutes)
    plan = sim.initial_durations(world)
    for _ in range(120):
        sim.step(world, plan)
        for agent in world.snapshot()["agents"]:
            assert agent["thought_source"] in thought.SOURCES
            assert isinstance(agent["thought"], str) and agent["thought"], agent


def test_a_working_agent_thought_names_the_ticket_it_holds() -> None:
    world = World(seed=3, clock=main.sim_time, sim_minutes=main.sim_minutes)
    plan = sim.initial_durations(world)
    checked = 0
    for _ in range(200):
        sim.step(world, plan)
        for agent in world.snapshot()["agents"]:
            if agent["state"] in ("working", "review") and agent["ticket"]:
                assert agent["ticket"] in agent["thought"]
                if agent["ticket_title"]:
                    assert agent["ticket_title"] in agent["thought"]
                    checked += 1
    assert checked > 0, "no titled ticket was ever on a desk in 200 ticks"


def test_replay_rebuilds_the_same_thought(tmp_path) -> None:
    world = World(seed=5, state_dir=str(tmp_path), clock=main.sim_time, sim_minutes=main.sim_minutes)
    plan = sim.initial_durations(world)
    for _ in range(90):
        sim.step(world, plan)
    before = {a["id"]: (a["ticket"], a["ticket_title"], a["thought"]) for a in world.snapshot()["agents"]}

    revived = World(seed=5, state_dir=str(tmp_path), clock=main.sim_time, sim_minutes=main.sim_minutes)
    assert revived.replay() > 0
    revived.tick = world.tick
    after = {a["id"]: (a["ticket"], a["ticket_title"], a["thought"]) for a in revived.snapshot()["agents"]}
    assert after == before


# --- the wire ------------------------------------------------------------
def test_every_snapshot_agent_carries_a_thought() -> None:
    with make_client() as client:
        state = client.get("/api/world/state").json()
    assert len(state["agents"]) == 6
    for agent in state["agents"]:
        assert set(agent) >= {"thought", "thought_source", "ticket_title", "ticket_status"}
        assert isinstance(agent["thought"], str) and agent["thought"]
        assert agent["thought_source"] in thought.SOURCES


def test_a_posted_ticket_gets_the_atlas_title_and_a_ticketed_thought() -> None:
    meta = thought.ticket_meta("WORLD-11")
    assert meta is not None
    with make_client() as client:
        state = client.get("/api/world/state").json()
        target = next(a for a in state["agents"] if a["state"] != "meeting")
        posted = client.post(
            "/api/world/events",
            json={"agent": target["id"], "type": "task_start", "ticket": "WORLD-11"},
        )
        assert posted.status_code == 200, posted.text
        agent = next(a for a in posted.json()["state"]["agents"] if a["id"] == target["id"])
        assert agent["ticket"] == "WORLD-11"
        assert agent["ticket_title"] == meta["title"]
        assert agent["ticket_status"] == meta["status"]
        assert agent["thought"].startswith(thought.COPY["state_verb"]["working"].format(ticket="WORLD-11"))
        assert agent["ticket_title"] in agent["thought"]
        assert agent["thought_source"] == "ticket"


def test_a_meeting_posts_its_topic_into_the_thought() -> None:
    with make_client() as client:
        state = client.get("/api/world/state").json()
        target = next(a for a in state["agents"] if a["state"] != "meeting")
        posted = client.post(
            "/api/world/events",
            json={
                "agent": target["id"],
                "type": "meeting_open",
                "room": "meeting_room",
                "topic": "contract review",
            },
        )
        assert posted.status_code == 200, posted.text
        agent = next(a for a in posted.json()["state"]["agents"] if a["id"] == target["id"])
        assert agent["thought"] == thought.COPY["meeting_topic"].format(topic="contract review")
        assert agent["thought_source"] == "meeting"


def test_delta_patches_carry_the_thought() -> None:
    meta = thought.ticket_meta("CHAT-11")
    assert meta is not None
    with make_client() as client:
        with client.websocket_connect("/ws/world") as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            for agent in first["world"]["agents"]:
                assert "thought" in agent
            target = next(a for a in first["world"]["agents"] if a["state"] != "meeting")
            client.post(
                "/api/world/events",
                json={"agent": target["id"], "type": "task_start", "ticket": "CHAT-11"},
            )
            delta = ws.receive_json()
            assert delta["type"] == "delta"
            patch = next(p for p in delta["patches"] if p["id"] == target["id"])
            assert patch["ticket"] == "CHAT-11"
            assert patch["ticket_title"] == meta["title"]
            assert patch["thought"].startswith("شغّال على CHAT-11")
            assert patch["thought_source"] == "ticket"


@pytest.mark.parametrize("minute,expected_source", [(540, "dayplan"), (1000, "dayplan")])
def test_the_dayplan_thought_tracks_the_sim_clock(minute: int, expected_source: str) -> None:
    """11:30 and 16:40 are both inside the day, so a ticketless agent has a block."""
    line, source = thought.compose(state="idle", agent_id="salim", now_minute=minute)
    assert source == expected_source
    assert line
