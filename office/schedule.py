"""Deterministic daily schedule generator for Agents Office World (WORLD-3).

Pure Python: no I/O, no randomness, no LLM. The same input always yields the
same output (byte-identical JSON), so the result can be golden-tested.

Timeline states (ticket WORLD-3): ``work`` | ``break`` | ``meeting`` | ``idle``.
Priority when minutes collide: ``meeting`` > ``break`` > ``work`` > ``idle``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Tuple

MINUTES_PER_DAY = 24 * 60
STATES = ("work", "break", "meeting", "idle")

AGENTS: Tuple[str, ...] = ("hakim", "layla", "maryam", "omar", "salim", "yassin")
ROOMS: Tuple[str, ...] = ("room-1", "room-2", "room-3")

WORK_START = 9 * 60   # 09:00
WORK_END = 17 * 60    # 17:00
LUNCH_AT = 12 * 60    # 12:00 (base, staggered per agent)
LUNCH_LEN = 30
COFFEE_AT = 15 * 60   # 15:00 (base, staggered per agent)
COFFEE_LEN = 15
STAGGER_LUNCH = 5     # minutes between agents' lunch starts
STAGGER_COFFEE = 3


@dataclass(frozen=True)
class Meeting:
    id: str
    room: str
    start: int  # minutes from midnight
    end: int
    attendees: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not (0 <= self.start < self.end <= MINUTES_PER_DAY):
            raise ValueError(f"meeting {self.id}: bad interval {self.start}-{self.end}")
        if self.room not in ROOMS:
            raise ValueError(f"meeting {self.id}: unknown room {self.room}")
        if not self.attendees:
            raise ValueError(f"meeting {self.id}: no attendees")


def default_meetings() -> List[Meeting]:
    """Fixed sample day. Rooms and times are chosen so no room double-books."""
    return [
        Meeting("standup", "room-1", 9 * 60 + 30, 9 * 60 + 45, AGENTS),
        Meeting("design-sync", "room-2", 11 * 60, 11 * 60 + 45,
                ("hakim", "layla", "maryam")),
        Meeting("one-on-one", "room-3", 14 * 60, 14 * 60 + 30, ("omar", "salim")),
        Meeting("handoff", "room-2", 15 * 60 + 30, 16 * 60,
                ("layla", "omar", "yassin")),
    ]


def validate_meetings(meetings: Iterable[Meeting]) -> None:
    """Raise ValueError if two meetings share a room or an attendee at once."""
    items = sorted(meetings, key=lambda m: (m.start, m.end, m.id))
    ids = [m.id for m in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate meeting id")
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if b.start >= a.end:
                break  # sorted by start: no later meeting can overlap a
            if a.room == b.room:
                raise ValueError(f"room conflict in {a.room}: {a.id} vs {b.id}")
            shared = set(a.attendees) & set(b.attendees)
            if shared:
                raise ValueError(f"attendee conflict {sorted(shared)}: {a.id} vs {b.id}")


def _agent_grid(agent: str, index: int, meetings: Sequence[Meeting]) -> List[str]:
    """Build a per-minute state list for one agent (priority-resolved)."""
    grid = ["idle"] * MINUTES_PER_DAY
    for m in range(WORK_START, WORK_END):
        grid[m] = "work"

    lunch = LUNCH_AT + index * STAGGER_LUNCH
    coffee = COFFEE_AT + index * STAGGER_COFFEE
    for start, length in ((lunch, LUNCH_LEN), (coffee, COFFEE_LEN)):
        for m in range(start, min(start + length, MINUTES_PER_DAY)):
            grid[m] = "break"

    for meet in meetings:
        if agent in meet.attendees:
            for m in range(meet.start, meet.end):
                grid[m] = "meeting"
    return grid


def _to_blocks(grid: List[str]) -> List[dict]:
    blocks: List[dict] = []
    start = 0
    for minute in range(1, MINUTES_PER_DAY + 1):
        if minute == MINUTES_PER_DAY or grid[minute] != grid[start]:
            blocks.append({
                "start": _hhmm(start),
                "end": _hhmm(minute),
                "state": grid[start],
            })
            start = minute
    return blocks


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_day(agents: Sequence[str] = AGENTS,
              meetings: Sequence[Meeting] | None = None) -> dict:
    """Return the full-day plan: {"agents": [{"agent", "blocks"}...], "meetings": [...]}."""
    meetings = list(default_meetings() if meetings is None else meetings)
    validate_meetings(meetings)
    for m in meetings:
        unknown = set(m.attendees) - set(agents)
        if unknown:
            raise ValueError(f"meeting {m.id}: unknown attendees {sorted(unknown)}")

    out_agents = []
    for index, agent in enumerate(agents):
        grid = _agent_grid(agent, index, meetings)
        if "break" not in grid:
            # Guarantee at least one break: take the first free work minute.
            free = next((i for i in range(WORK_START, WORK_END)
                         if grid[i] == "work"), None)
            if free is None:
                raise ValueError(f"{agent}: no free minute for a break")
            grid[free] = "break"
        out_agents.append({"agent": agent, "blocks": _to_blocks(grid)})

    return {
        "meetings": [
            {"id": m.id, "room": m.room, "start": _hhmm(m.start),
             "end": _hhmm(m.end), "attendees": list(m.attendees)}
            for m in sorted(meetings, key=lambda m: (m.start, m.id))
        ],
        "agents": out_agents,
    }


def to_json(plan: dict) -> str:
    """Canonical JSON: sorted keys, fixed separators -> byte-stable output."""
    return json.dumps(plan, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    print(to_json(build_day()), end="")
