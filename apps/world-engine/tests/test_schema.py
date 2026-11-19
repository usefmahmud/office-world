"""The frozen v1 state machine: allowed transitions only, nothing invented."""

import pytest

from app.schema import EVENT_TYPES, STATES, resolve
from app.world import World


def test_every_state_is_in_the_fsm():
    assert STATES == ("idle", "working", "review", "break", "meeting")


def test_event_vocabulary_is_frozen():
    assert EVENT_TYPES == (
        "task_start",
        "task_done",
        "message",
        "meeting_open",
        "meeting_close",
        "break",
        "handoff",
        "idle",
    )


@pytest.mark.parametrize(
    "from_state,event,to_state",
    [
        ("idle", "task_start", "working"),
        ("working", "task_done", "review"),
        ("review", "handoff", "idle"),
        ("working", "break", "break"),
        ("idle", "meeting_open", "meeting"),
        ("meeting", "meeting_close", "idle"),
        ("meeting", "meeting_open", "meeting"),
        ("break", "idle", "idle"),
    ],
)
def test_allowed_transitions(from_state, event, to_state):
    assert resolve(from_state, event) == to_state


@pytest.mark.parametrize(
    "from_state,event",
    [
        ("idle", "task_done"),        # cannot close a task you never started
        ("idle", "meeting_close"),    # cannot close a meeting you are not in
        ("meeting", "task_done"),
        ("meeting", "break"),
        ("break", "task_done"),
        ("working", "meeting_close"),
    ],
)
def test_forbidden_transitions_are_rejected(from_state, event):
    assert resolve(from_state, event) is None


def test_unknown_event_is_rejected():
    assert resolve("idle", "teleport") is None


def test_message_never_moves_an_agent():
    for state in STATES:
        assert resolve(state, "message") == state


def test_world_rejects_and_keeps_state():
    world = World(seed=1)
    assert world.emit("hakim", "task_done") is None
    assert world.agents["hakim"].state == "idle"
    assert world.rejections[-1].reason == "invalid_transition"


def test_world_rejects_unknown_agent():
    world = World(seed=1)
    assert world.emit("nobody", "task_start") is None
    assert world.rejections[-1].reason == "unknown_agent"


def test_working_requires_a_ticket():
    world = World(seed=1)
    event = world.emit("omar", "task_start", ticket="WORLD-2")
    assert event is not None
    assert world.agents["omar"].state == "working"
    assert world.agents["omar"].ticket == "WORLD-2"
    assert world.agents["omar"].room == "desk-omar"


def test_meeting_moves_agents_to_meeting_room():
    world = World(seed=1)
    world.emit("yassin", "meeting_open", room="meeting-room")
    assert world.agents["yassin"].state == "meeting"
    assert world.agents["yassin"].room == "meeting-room"


def test_snapshot_shape():
    world = World(seed=1)
    snap = world.snapshot()
    assert set(snap) == {"tick", "seed", "ts", "agents", "meeting", "counts"}
    assert len(snap["agents"]) == 6
    assert snap["counts"]["idle"] == 6
    assert {"id", "name", "role", "state", "room", "ticket", "since_tick"} <= set(snap["agents"][0])
