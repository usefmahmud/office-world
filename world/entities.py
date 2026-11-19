"""Static world model: rooms, agents, the frozen state machine, snapshots.

Pure data structures plus pure helpers.  Nothing here reads the clock, the
filesystem, the network or a random source; the only source of variation in
the whole package is the ``seed`` argument of :func:`world.sim.step`.

Coordinates are **integer centi-units** (1 unit = 100 centi-units).  Floats
are banned on purpose: integer geometry hashes byte-identically everywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------
# Frozen contract (WORLD-1 / W0).  Changing anything below breaks determinism.
# --------------------------------------------------------------------------

#: Every state the world knows.  Nothing else may appear in a snapshot.
STATES: tuple[str, ...] = ("idle", "working", "review", "break", "meeting")

#: The frozen day cycle: idle → working → review → break → meeting → idle.
CYCLE: tuple[str, ...] = ("idle", "working", "review", "break", "meeting")

#: The frozen event vocabulary.  The sim never emits anything outside it.
EVENT_KINDS: tuple[str, ...] = (
    "task_start",
    "task_done",
    "message",
    "meeting_open",
    "meeting_close",
    "break",
    "handoff",
    "idle",
)

#: ``(current_state, event_kind) -> next_state``.  Every edge of CYCLE is
#: present; the extra edges are the legal shortcuts (leaving a meeting to work,
#: rework after review, break from idle).  Anything not in this table is a
#: *rejected* event: counted, never silently applied.
TRANSITIONS: dict[tuple[str, str], str] = {
    ("idle", "task_start"): "working",
    ("break", "task_start"): "working",
    ("review", "task_start"): "working",
    ("meeting", "task_start"): "working",
    ("working", "task_done"): "review",
    ("working", "handoff"): "review",
    ("review", "handoff"): "break",
    ("working", "break"): "break",
    ("review", "break"): "break",
    ("idle", "break"): "break",
    ("idle", "meeting_open"): "meeting",
    ("working", "meeting_open"): "meeting",
    ("review", "meeting_open"): "meeting",
    ("break", "meeting_open"): "meeting",
    ("meeting", "meeting_close"): "idle",
}

#: ``message`` never moves an agent; ``idle`` is the explicit reset event.
NON_TRANSITION_EVENTS: tuple[str, ...] = ("message", "idle")


def next_state(current: str, kind: str) -> str | None:
    """Return the state ``current`` moves to on ``kind``, or ``None`` if illegal."""
    if current not in STATES:
        raise ValueError(f"unknown state: {current!r}")
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind!r}")
    if kind == "message":
        return current          # side effect only: an agent speaks
    if kind == "idle":
        return "idle"           # explicit reset, legal from anywhere
    return TRANSITIONS.get((current, kind))


# --------------------------------------------------------------------------
# Floor plan (one floor, five rooms — v1 scope)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Room:
    room_id: str
    label: str
    x: int
    y: int
    w: int
    h: int

    @property
    def anchor(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    def as_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
        }


#: Floor is 12000 x 7000 centi-units (120 x 70 units).
FLOOR_W = 12000
FLOOR_H = 7000

ROOMS: tuple[Room, ...] = (
    Room("desks", "قاعة العمل", 200, 200, 7600, 4000),
    Room("meeting", "غرفة الاجتماعات", 8000, 200, 3800, 3000),
    Room("coffee", "المطبخ", 8000, 3400, 3800, 1800),
    Room("lobby", "الاستقبال", 200, 4400, 5000, 2400),
    Room("library", "المكتبة", 5400, 4400, 6400, 2400),
)

ROOM_INDEX: dict[str, Room] = {room.room_id: room for room in ROOMS}

#: Which room an agent walks to for a given state (``working``/``review``
#: route to the agent's own desk, handled in :func:`target_for`).
STATE_ROOM: dict[str, str] = {
    "idle": "lobby",
    "break": "coffee",
    "meeting": "meeting",
}


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Agent:
    """One occupant.  Immutable: every change produces a new instance."""

    agent_id: str
    name: str
    desk_x: int
    desk_y: int
    state: str = "idle"
    task_id: str | None = None
    x: int = 0
    y: int = 0
    room: str = "lobby"
    dest_room: str = ""
    target_x: int = 0
    target_y: int = 0
    leg_x0: int = 0
    leg_y0: int = 0
    leg_k: int = 0
    leg_n: int = 0
    since_tick: int = 0
    messages: int = 0
    last_message_tick: int = -1_000_000

    @property
    def walking(self) -> bool:
        return self.leg_n > 0 and self.leg_k < self.leg_n

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "state": self.state,
            "task_id": self.task_id or "",
            "x": self.x,
            "y": self.y,
            "room": self.room,
            "dest_room": self.dest_room,
            "leg_k": self.leg_k,
            "leg_n": self.leg_n,
            "since_tick": self.since_tick,
            "messages": self.messages,
        }


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Event:
    """A single world event.  The only way state is allowed to change."""

    tick: int
    kind: str
    agent_id: str
    task_id: str | None = None
    room: str | None = None
    participants: tuple[str, ...] = ()
    text_id: str | None = None
    source: str = "schedule"   # schedule | external | ambient

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind: {self.kind!r}")

    def sort_key(self) -> tuple[str, str, str, str, str]:
        """Total order, so two identical inputs always apply in the same order."""
        return (
            self.agent_id,
            self.kind,
            self.task_id or "",
            self.text_id or "",
            self.source,
        )

    def feed_tuple(self) -> tuple[str, str, str, str]:
        return (self.agent_id, self.kind, self.task_id or "", self.text_id or "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "kind": self.kind,
            "agent_id": self.agent_id,
            "task_id": self.task_id or "",
            "room": self.room or "",
            "participants": list(self.participants),
            "text_id": self.text_id or "",
            "source": self.source,
        }


# --------------------------------------------------------------------------
# World snapshots
# --------------------------------------------------------------------------

#: How many recent events the snapshot keeps for the UI feed.
FEED_LEN = 25


@dataclass(frozen=True, slots=True)
class WorldState:
    """A complete world snapshot.  ``step`` takes one and returns a new one."""

    tick: int
    seed: int
    agents: tuple[Agent, ...]
    digest: str
    feed: tuple[tuple[str, str, str, str], ...]
    stats: tuple[tuple[str, int], ...]

    # -- helpers -----------------------------------------------------------
    def agent(self, agent_id: str) -> Agent:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(agent_id)

    def stat(self, key: str) -> int:
        for name, value in self.stats:
            if name == key:
                return value
        return 0

    def with_stats(self, **deltas: int) -> tuple[tuple[str, int], ...]:
        current = dict(self.stats)
        for key, delta in deltas.items():
            current[key] = current.get(key, 0) + delta
        return tuple(sorted(current.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "seed": self.seed,
            "agents": [agent.as_dict() for agent in self.agents],
            "digest": self.digest,
            "feed": [list(item) for item in self.feed],
            "stats": dict(self.stats),
            "rooms": [room.as_dict() for room in ROOMS],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def target_for(
    agent: Agent,
    state: str,
    room_id: str | None = None,
    slot: int = 0,
) -> tuple[int, int, str]:
    """Return ``(x, y, room_id)`` the agent should stand at for ``state``.

    ``slot`` (the agent's index in the roster) spreads people around a shared
    room instead of stacking six dots on one pixel.
    """
    if state in ("working", "review"):
        return (agent.desk_x, agent.desk_y, "desks")
    chosen = room_id if room_id is not None else STATE_ROOM.get(state, "lobby")
    room = ROOM_INDEX[chosen]
    ax, ay = room.anchor
    offset_x = (slot % 3 - 1) * 500
    offset_y = (slot // 3 - 1) * 400
    x = min(max(ax + offset_x, room.x + 300), room.x + room.w - 300)
    y = min(max(ay + offset_y, room.y + 300), room.y + room.h - 300)
    return (x, y, chosen)
