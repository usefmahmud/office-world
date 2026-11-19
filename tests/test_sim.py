"""Determinism contract for the office world core (WORLD-2).

The spine of the whole project: if any of these tests go red, the world is no
longer reproducible and every later wave (engine, renderer, rewind) is built on
sand.  Run with ``pytest -q``.

Golden file: ``tests/golden/world_t10000.json``.  Regenerate *on purpose* with::

    WORLD_REGEN_GOLDEN=1 pytest tests/test_sim.py -k golden
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from world import (
    AGENT_IDS,
    CYCLE,
    DAY_LEN,
    EVENT_KINDS,
    ROOMS,
    STATES,
    TRANSITIONS,
    Event,
    initial_state,
    run,
    state_sha256,
    step,
)
from world.entities import FLOOR_H, FLOOR_W, ROOM_INDEX
from world.rng import Stream, stream_seed

GOLDEN = Path(__file__).parent / "golden" / "world_t10000.json"
WORLD_DIR = Path(__file__).resolve().parent.parent / "world"
TICKS = 10_000
SEED = 12_345


# --------------------------------------------------------------------------
# 1. The golden hash: 10 000 ticks, one frozen number
# --------------------------------------------------------------------------


def test_golden_hash_after_10k_ticks() -> None:
    world = run(initial_state(SEED), TICKS)
    digest = state_sha256(world)

    if os.environ.get("WORLD_REGEN_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(
                {
                    "seed": SEED,
                    "ticks": TICKS,
                    "sha256": digest,
                    "event_chain": world.digest,
                    "tick": world.tick,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["seed"] == SEED
    assert golden["ticks"] == TICKS
    assert world.tick == golden["tick"] == TICKS
    assert digest == golden["sha256"], (
        "world state hash drifted from the golden file — determinism is broken "
        "or the contract changed on purpose (then regenerate it)"
    )
    assert world.digest == golden["event_chain"]


# --------------------------------------------------------------------------
# 2. Same seed twice ⇒ byte-identical world
# --------------------------------------------------------------------------


def test_same_seed_twice_is_byte_identical() -> None:
    first = run(initial_state(SEED), 3_000)
    second = run(initial_state(SEED), 3_000)

    assert first.canonical_json() == second.canonical_json()
    assert state_sha256(first) == state_sha256(second)
    assert first.digest == second.digest
    assert first.feed == second.feed
    assert [a.as_dict() for a in first.agents] == [a.as_dict() for a in second.agents]


def test_same_seed_replays_identical_intermediate_frames() -> None:
    left = initial_state(SEED)
    right = initial_state(SEED)
    for tick in range(1, 1_201):
        left = step(left)
        right = step(right)
        if tick % 200 == 0:
            assert state_sha256(left) == state_sha256(right)


def test_different_seed_changes_the_day() -> None:
    hashes = {seed: state_sha256(run(initial_state(seed), 2_000)) for seed in (1, 7, SEED)}
    assert len(set(hashes.values())) == 3


# --------------------------------------------------------------------------
# 3. Purity: step() never touches the state it is given
# --------------------------------------------------------------------------


def test_step_does_not_mutate_its_input() -> None:
    world = initial_state(SEED)
    for _ in range(500):
        world = step(world)
    before = world.canonical_json()

    nxt = step(world, [Event(tick=world.tick + 1, kind="break", agent_id="omar")])

    assert world.canonical_json() == before
    assert nxt.tick == world.tick + 1
    assert nxt is not world


def test_run_matches_repeated_step() -> None:
    assert state_sha256(run(initial_state(SEED), 300)) == state_sha256(
        _manual(initial_state(SEED), 300)
    )


def _manual(world, ticks: int):
    for _ in range(ticks):
        world = step(world)
    return world


# --------------------------------------------------------------------------
# 4. The frozen contract (state machine + event vocabulary)
# --------------------------------------------------------------------------


def test_states_and_cycle_are_frozen() -> None:
    assert STATES == ("idle", "working", "review", "break", "meeting")
    assert CYCLE == ("idle", "working", "review", "break", "meeting")


def test_event_vocabulary_is_frozen() -> None:
    assert EVENT_KINDS == (
        "task_start",
        "task_done",
        "message",
        "meeting_open",
        "meeting_close",
        "break",
        "handoff",
        "idle",
    )


def test_every_transition_targets_a_real_state() -> None:
    for (source, kind), target in TRANSITIONS.items():
        assert source in STATES
        assert kind in EVENT_KINDS
        assert target in STATES


def test_frozen_cycle_edges_all_exist() -> None:
    cycle_edges = {
        ("idle", "task_start", "working"),
        ("working", "task_done", "review"),
        ("review", "handoff", "break"),
        ("break", "meeting_open", "meeting"),
        ("meeting", "meeting_close", "idle"),
    }
    table = {(source, kind, target) for (source, kind), target in TRANSITIONS.items()}
    assert cycle_edges <= table


def test_message_never_changes_state_and_idle_resets() -> None:
    for state in STATES:
        for kind, expected in (("message", state), ("idle", "idle")):
            assert _next(state, kind) == expected


def _next(state: str, kind: str) -> str | None:
    from world.entities import next_state

    return next_state(state, kind)


# --------------------------------------------------------------------------
# 5. No clock, no global randomness, no LLM: the loop is pure rule-based code
# --------------------------------------------------------------------------

FORBIDDEN_IMPORTS = {
    "random",
    "time",
    "datetime",
    "os",
    "socket",
    "subprocess",
    "requests",
    "httpx",
    "urllib",
    "aiohttp",
    "openai",
    "anthropic",
    "uuid",
    "secrets",
}


def test_world_package_imports_no_clock_no_network_no_llm() -> None:
    offenders: list[str] = []
    for path in sorted(WORLD_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in FORBIDDEN_IMPORTS:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.name}: from {node.module}")
    assert offenders == []


def test_snapshot_contains_no_floats() -> None:
    world = run(initial_state(SEED), 1_500)
    payload = json.loads(world.canonical_json())
    assert _float_paths(payload) == []


def _float_paths(node, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, float):
        found.append(path)
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(_float_paths(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_float_paths(value, f"{path}[{index}]"))
    return found


# --------------------------------------------------------------------------
# 6. Seeded substreams: one agent's dice never move another agent's day
# --------------------------------------------------------------------------


def test_substreams_are_addressed_by_label_not_by_draw_order() -> None:
    a = [Stream(SEED, "schedule", "omar").below(1000) for _ in range(5)]
    Stream(SEED, "schedule", "layla").below(1000)      # somebody else draws
    b = [Stream(SEED, "schedule", "omar").below(1000) for _ in range(5)]
    assert a == b
    assert a != [Stream(SEED, "schedule", "layla").below(1000) for _ in range(5)]


def test_stream_seed_is_unambiguous() -> None:
    assert stream_seed(1, "ab", "c") != stream_seed(1, "a", "bc")
    assert stream_seed(1, "a") != stream_seed(2, "a")


def test_stream_derive_is_pure() -> None:
    parent = Stream(SEED, "schedule")
    assert [parent.derive("omar").below(100) for _ in range(3)] == [
        parent.derive("omar").below(100) for _ in range(3)
    ]


# --------------------------------------------------------------------------
# 7. The world stays believable and inside the floor
# --------------------------------------------------------------------------


def test_all_agents_stay_on_the_floor() -> None:
    world = initial_state(SEED)
    for _ in range(3_000):
        world = step(world)
        for agent in world.agents:
            assert 0 <= agent.x <= FLOOR_W, (world.tick, agent.agent_id, agent.x)
            assert 0 <= agent.y <= FLOOR_H, (world.tick, agent.agent_id, agent.y)


def test_every_agent_visits_every_state_in_one_day() -> None:
    world = initial_state(SEED)
    seen: dict[str, set[str]] = {agent_id: set() for agent_id in AGENT_IDS}
    for _ in range(DAY_LEN):
        world = step(world)
        for agent in world.agents:
            assert agent.state in STATES
            seen[agent.agent_id].add(agent.state)
    for agent_id, states in seen.items():
        assert states == set(STATES), f"{agent_id} never reached {set(STATES) - states}"


def test_healthy_day_has_no_rejected_events() -> None:
    world = run(initial_state(SEED), DAY_LEN)
    assert world.stat("rejected") == 0
    assert world.stat("task_starts") >= len(AGENT_IDS)
    assert world.stat("task_dones") >= len(AGENT_IDS)
    assert world.stat("messages") > 0


def test_working_agents_always_own_a_task_id() -> None:
    world = initial_state(SEED)
    for _ in range(4_000):
        world = step(world)
        for agent in world.agents:
            if agent.state == "working":
                assert agent.task_id, f"{agent.agent_id} working with no task at {world.tick}"


def test_emitted_events_only_use_the_frozen_vocabulary() -> None:
    world = initial_state(SEED)
    for _ in range(2_000):
        world = step(world)
    for entry in world.feed:
        assert entry[1] in EVENT_KINDS


# --------------------------------------------------------------------------
# 8. Movement: straight lines only, integer geometry only
# --------------------------------------------------------------------------


def test_walking_follows_a_straight_line() -> None:
    world = initial_state(SEED)
    start = world.agent("omar")
    entered = step(
        world,
        [Event(tick=world.tick + 1, kind="task_start", agent_id="omar", task_id="T-test")],
    )
    walker = entered.agent("omar")
    assert walker.walking, "omar should be walking to his desk"

    x0, y0 = walker.x, walker.y
    x1, y1 = walker.target_x, walker.target_y
    dx, dy = x1 - x0, y1 - y0
    assert dx or dy

    cur = entered
    for _ in range(2_000):
        if not cur.agent("omar").walking:
            break
        cur = step(cur)
        agent = cur.agent("omar")
        cross = (agent.x - x0) * dy - (agent.y - y0) * dx
        assert abs(cross) <= max(abs(dx), abs(dy)), (
            agent.x,
            agent.y,
            "left the straight line",
        )
    assert (cur.agent("omar").x, cur.agent("omar").y) == (x1, y1)
    assert start.state == "idle"


# --------------------------------------------------------------------------
# 9. External events (the engine's only way in) obey the same machine
# --------------------------------------------------------------------------


def test_external_task_start_requires_a_task_id() -> None:
    world = initial_state(SEED)
    tick = world.tick + 1
    rejected = step(world, [Event(tick=tick, kind="task_start", agent_id="omar")])
    assert rejected.agent("omar").state == "idle"
    assert rejected.stat("rejected") == 1

    accepted = step(
        world,
        [Event(tick=tick, kind="task_start", agent_id="omar", task_id="T-42", source="external")],
    )
    assert accepted.agent("omar").state == "working"
    assert accepted.agent("omar").task_id == "T-42"
    assert accepted.stat("rejected") == 0


def test_illegal_transition_is_rejected_not_applied() -> None:
    world = initial_state(SEED)
    tick = world.tick + 1
    # task_done from idle is not an edge of the frozen machine.
    after = step(world, [Event(tick=tick, kind="task_done", agent_id="omar", task_id="T-1")])
    assert after.agent("omar").state == "idle"
    assert after.stat("rejected") == 1


def test_unknown_event_kind_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        Event(tick=1, kind="dance", agent_id="omar")


# --------------------------------------------------------------------------
# 10. Rooms and routing
# --------------------------------------------------------------------------


def test_rooms_tile_inside_the_floor() -> None:
    for room in ROOMS:
        assert 0 <= room.x and room.x + room.w <= FLOOR_W
        assert 0 <= room.y and room.y + room.h <= FLOOR_H


def test_agents_are_inside_the_room_they_report() -> None:
    world = initial_state(SEED)
    for _ in range(1_000):
        world = step(world)
        for agent in world.agents:
            if agent.walking:
                continue
            assert ROOM_INDEX[agent.room].contains(agent.x, agent.y), (
                agent.agent_id,
                agent.room,
                agent.x,
                agent.y,
            )
