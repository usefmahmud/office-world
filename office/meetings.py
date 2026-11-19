"""Meetings as first-class objects (WORLD-6 / W4).

Pure Python: no I/O, no clock, no randomness, no LLM. Same input -> same output.

A meeting is ``organizer + attendees + room + [start, end)``. Two meetings
conflict when they overlap in time AND either share a room or share an
attendee. Back-to-back meetings (one ends exactly when the next starts) are
allowed.

Events follow the frozen envelope in ``docs/CONTRACT.md`` §3/§5.4-§5.5:
``meeting_open`` at ``start`` and ``meeting_close`` at ``end``, one event per
attendee. ``tick`` is seconds from 00:00 (``tick_seconds = 1``), so
``tick = minute * 60``. ``seq``/``ts`` are assigned by the single writer, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .schedule import AGENTS, MINUTES_PER_DAY, ROOMS

#: Contract §5.4 payload bounds (planned_ticks, participants).
MIN_PARTICIPANTS = 2
MAX_PARTICIPANTS = 6
MIN_PLANNED_TICKS = 60
MAX_PLANNED_TICKS = 3600
#: Contract §6.3: at most 3 meeting_open per day.
MAX_MEETINGS_PER_DAY = 3
TICKS_PER_MINUTE = 60

#: Same-tick order: close before open (back-to-back meetings), then agent roster order (DT-6).
_TYPE_RANK = {"meeting_close": 0, "meeting_open": 1}
_AGENT_RANK = {agent: i for i, agent in enumerate(AGENTS)}


class MeetingConflict(ValueError):
    """Raised when a meeting cannot be booked (room, attendee, cap or shape)."""


@dataclass(frozen=True)
class Meeting:
    id: str
    organizer: str
    attendees: tuple[str, ...]
    room: str
    start: int  # minute of day, [0, 1440)
    end: int    # minute of day, exclusive
    topic: str = "meeting"

    def __post_init__(self) -> None:
        if not self.id:
            raise MeetingConflict("meeting id is empty")
        if self.room not in ROOMS:
            raise MeetingConflict(f"{self.id}: unknown room {self.room!r}")
        if not (0 <= self.start < self.end <= MINUTES_PER_DAY):
            raise MeetingConflict(f"{self.id}: bad interval {self.start}-{self.end}")
        ticks = (self.end - self.start) * TICKS_PER_MINUTE
        if not (MIN_PLANNED_TICKS <= ticks <= MAX_PLANNED_TICKS):
            raise MeetingConflict(f"{self.id}: duration {ticks} ticks outside 60..3600")
        if len(set(self.attendees)) != len(self.attendees):
            raise MeetingConflict(f"{self.id}: duplicate attendees")
        if not (MIN_PARTICIPANTS <= len(self.attendees) <= MAX_PARTICIPANTS):
            raise MeetingConflict(f"{self.id}: needs 2..6 attendees")
        unknown = [a for a in self.attendees if a not in _AGENT_RANK]
        if unknown:
            raise MeetingConflict(f"{self.id}: unknown attendees {unknown}")
        if self.organizer not in self.attendees:
            raise MeetingConflict(f"{self.id}: organizer must be an attendee")

    def overlaps(self, other: "Meeting") -> bool:
        return self.start < other.end and other.start < self.end

    def conflict_reason(self, other: "Meeting") -> str | None:
        """Why ``self`` cannot coexist with ``other``, or ``None`` if it can."""
        if not self.overlaps(other):
            return None
        if self.room == other.room:
            return "room"
        shared = sorted(set(self.attendees) & set(other.attendees), key=_AGENT_RANK.__getitem__)
        if shared:
            return "attendee:" + ",".join(shared)
        return None


def _sorted(meetings: Iterable[Meeting]) -> tuple[Meeting, ...]:
    return tuple(sorted(meetings, key=lambda m: (m.start, m.end, m.id)))


def find_conflicts(meetings: Sequence[Meeting]) -> list[tuple[str, str, str]]:
    """All conflicting pairs as ``(first_id, second_id, reason)``, deterministic order."""
    items = _sorted(meetings)
    out: list[tuple[str, str, str]] = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if b.start >= a.end:
                break  # sorted by start: nothing later can overlap a
            reason = a.conflict_reason(b)
            if reason is not None:
                out.append((a.id, b.id, reason))
    return out


def book(existing: Sequence[Meeting], candidate: Meeting) -> tuple[Meeting, ...]:
    """Return the schedule with ``candidate`` added, or raise :class:`MeetingConflict`.

    Rejects duplicate ids, the per-day meeting cap, and any room/attendee clash.
    The input sequence is never mutated.
    """
    if any(m.id == candidate.id for m in existing):
        raise MeetingConflict(f"duplicate meeting id {candidate.id}")
    if len(existing) + 1 > MAX_MEETINGS_PER_DAY:
        raise MeetingConflict(f"{candidate.id}: more than {MAX_MEETINGS_PER_DAY} meetings a day")
    for other in _sorted(existing):
        reason = candidate.conflict_reason(other)
        if reason is not None:
            raise MeetingConflict(f"{candidate.id} conflicts with {other.id} ({reason})")
    return _sorted([*existing, candidate])


def meeting_events(meetings: Sequence[Meeting]) -> list[dict[str, Any]]:
    """Envelope dicts for ``meeting_open`` / ``meeting_close``, one per attendee.

    Output is sorted by (tick, type rank, agent roster rank) so the same meetings
    always yield the same list, regardless of input order.
    """
    events: list[dict[str, Any]] = []
    for m in _sorted(meetings):
        planned = (m.end - m.start) * TICKS_PER_MINUTE
        for agent in m.attendees:
            base = {"agent": agent, "room": m.room, "task_id": None, "target": None}
            events.append({
                **base,
                "tick": m.start * TICKS_PER_MINUTE,
                "type": "meeting_open",
                "state": "meeting",
                "payload": {
                    "topic": m.topic,
                    "participants": list(m.attendees),
                    "planned_ticks": planned,
                    "meeting_id": m.id,
                },
            })
            events.append({
                **base,
                "tick": m.end * TICKS_PER_MINUTE,
                "type": "meeting_close",
                "state": "idle",
                "room": "bullpen",  # agents walk back to the floor after close (§5.5)
                "payload": {"actual_ticks": planned, "decisions": [], "meeting_id": m.id},
            })
    events.sort(key=lambda e: (e["tick"], _TYPE_RANK[e["type"]], _AGENT_RANK[e["agent"]]))
    return events
