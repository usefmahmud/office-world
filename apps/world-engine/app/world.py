"""World state: the single mutable projection + the append-only event log.

Every change goes through `World.emit()`, which validates the transition against
the frozen contract and then appends to `events.jsonl`. Replaying the log
reproduces the exact same world (deterministic, no I/O in the rules).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import thought as thought_mod
from .schema import Rejection, default_roster, resolve


@dataclass
class Agent:
    id: str
    name: str
    role: str
    desk_room: str
    state: str = "idle"
    room: str = ""
    ticket: str | None = None
    since_tick: int = 0
    # WORLD-11: the real rows behind the panel's "Thinking:" line — the Atlas title and
    # status of the ticket the agent holds, and the topic of the meeting they joined.
    ticket_title: str | None = None
    ticket_status: str | None = None
    meeting_topic: str | None = None

    def __post_init__(self) -> None:
        if not self.room:
            self.room = self.desk_room


@dataclass
class Meeting:
    id: str
    room: str
    members: list[str]
    opened_tick: int

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "room": self.room,
            "members": sorted(self.members),
            "opened_tick": self.opened_tick,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


#: How many `message` events the snapshot carries back to a fresh client.
RECENT_MESSAGES = 30


class World:
    """Deterministic projection of the real ledger."""

    def __init__(
        self,
        seed: int = 7,
        state_dir: str | None = None,
        clock: Callable[[int], str] | None = None,
        sim_minutes: Callable[[int], int] | None = None,
    ) -> None:
        self.seed = seed
        self.tick = 0
        # DT-2: an event's `ts` is derived from its tick. The engine injects the
        # `t0 + tick * sim_seconds_per_tick` clock; a bare World keeps the wall clock
        # so a caller that has no contract in hand still gets a timestamp.
        self.clock: Callable[[int], str] = clock or (lambda tick: _now())
        # WORLD-11: the minute of the simulated day, derived by the engine from the same
        # scale. Left unset, the dayplan half of `thought` is simply not consulted — a
        # bare World never reads the wall clock for a derived line (DT-2).
        self.sim_minutes: Callable[[int], int] | None = sim_minutes
        self.agents: dict[str, Agent] = {
            sid: Agent(spec.id, spec.name, spec.role, spec.desk_room)
            for sid, spec in default_roster().items()
        }
        self.meeting: Meeting | None = None
        self.rejections: list[Rejection] = []
        self.log_path: Path | None = None
        # The last few `message` events, so a client that connects (or reloads) can
        # render the transcript without waiting for the next delta. Bounded, ordered.
        self.recent_messages: list[dict] = []
        if state_dir:
            d = Path(state_dir)
            d.mkdir(parents=True, exist_ok=True)
            self.log_path = d / "events.jsonl"

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "tick": self.tick,
            "seed": self.seed,
            "ts": self.clock(self.tick),
            "agents": [self._agent_dict(a) for a in self.agents.values()],
            "meeting": self.meeting.as_dict() if self.meeting else None,
            "counts": self.counts(),
        }

    def _agent_dict(self, a: Agent) -> dict:
        now_minute = self.sim_minutes(self.tick) if self.sim_minutes else None
        line, source = thought_mod.compose(
            state=a.state,
            agent_id=a.id,
            ticket=a.ticket,
            title=a.ticket_title,
            meeting_topic=a.meeting_topic,
            now_minute=now_minute,
        )
        return {
            "id": a.id,
            "name": a.name,
            "role": a.role,
            "state": a.state,
            "room": a.room,
            "ticket": a.ticket,
            "since_tick": a.since_tick,
            # WORLD-11 (panel): ticket detail + the derived one-liner, with the row it
            # came from so a reader can tell a ticket thought from a dayplan one.
            "ticket_title": a.ticket_title,
            "ticket_status": a.ticket_status,
            "thought": line,
            "thought_source": source,
        }

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in ("idle", "working", "review", "break", "meeting")}
        for a in self.agents.values():
            out[a.state] = out.get(a.state, 0) + 1
        return out

    # -- mutation -----------------------------------------------------------
    def emit(
        self,
        agent_id: str,
        event_type: str,
        *,
        ticket: str | None = None,
        title: str | None = None,
        note: str | None = None,
        room: str | None = None,
        topic: str | None = None,
        source: str = "engine",
        persist: bool = True,
    ) -> dict | None:
        """Apply one event. Returns the event dict, or None when rejected."""
        agent = self.agents.get(agent_id)
        if agent is None:
            self._reject("unknown_agent", agent_id=agent_id, event_type=event_type)
            return None

        target = resolve(agent.state, event_type)
        if target is None:
            self._reject(
                "invalid_transition",
                agent_id=agent_id,
                event_type=event_type,
                from_state=agent.state,
            )
            return None

        prev = agent.state
        agent.state = target
        if target != prev:
            agent.since_tick = self.tick
        if ticket is not None:
            agent.ticket = ticket if target in ("working", "review") else None
        # WORLD-11: the panel shows the Atlas title/status of whatever key is on the desk.
        self._refresh_ticket(agent, title)
        # A meeting's topic is what its opener said it was; leaving the room forgets it.
        # `join` is the roster marker the multi-member open uses, not a topic, and a
        # `message` inside the meeting must never rewrite the topic.
        if event_type == "meeting_open" and target == "meeting":
            spoken = topic if topic is not None else (note if note not in (None, "", "join") else None)
            if spoken:
                agent.meeting_topic = spoken
        elif target != "meeting":
            agent.meeting_topic = None
        # `message` is a self-loop (contract §3): it never moves anybody, so the
        # post-event room is the room the agent was already in.
        if event_type != "message":
            if target == "meeting":
                agent.room = "meeting-room"
            elif target == "break":
                agent.room = "coffee-corner"
            elif target in ("working", "review"):
                agent.room = agent.desk_room
            elif target == "idle":
                agent.room = agent.desk_room
            if event_type in ("task_start", "meeting_open") and room:
                agent.room = room

        event = {
            "tick": self.tick,
            "ts": self.clock(self.tick),
            "type": event_type,
            "agent": agent_id,
            "from": prev,
            "to": target,
            "ticket": agent.ticket,
            "room": agent.room,
            "note": note,
            "source": source,
        }
        if event_type == "message":
            self.recent_messages.append(event)
            self.recent_messages = self.recent_messages[-RECENT_MESSAGES:]
        if persist:
            self._append(event)
        return event

    def open_meeting(self, members: list[str], room: str = "meeting-room") -> dict | None:
        self.emit(members[0], "meeting_open", room=room, note="meeting_open", source="engine")
        for extra in members[1:]:
            self.emit(extra, "meeting_open", room=room, note="join", source="engine")
        self.meeting = Meeting(
            id=f"m-{self.tick}", room=room, members=list(members), opened_tick=self.tick
        )
        return self.meeting.as_dict()

    def close_meeting(self) -> dict | None:
        if self.meeting is None:
            return None
        for member in self.meeting.members:
            if member in self.agents and self.agents[member].state == "meeting":
                self.emit(member, "meeting_close", note="meeting_close", source="engine")
        closed = self.meeting.as_dict()
        self.meeting = None
        return closed

    # -- persistence --------------------------------------------------------
    def _refresh_ticket(self, agent: Agent, title: str | None = None) -> None:
        """Keep the panel's ticket detail in step with the key the agent actually holds.

        The title/status are the Atlas row's, snapshotted into the engine's ticket
        catalog. An unknown key keeps a null title — never a made-up one.
        """
        if not agent.ticket:
            agent.ticket_title = None
            agent.ticket_status = None
            return
        meta = thought_mod.ticket_meta(agent.ticket) or {}
        agent.ticket_title = title if title is not None else meta.get("title")
        agent.ticket_status = meta.get("status")

    def _append(self, event: dict) -> None:
        if self.log_path is None:
            return
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def replay(self) -> int:
        """Rebuild state from events.jsonl (called once on boot)."""
        if self.log_path is None or not self.log_path.exists():
            return 0
        applied = 0
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.tick = max(self.tick, int(event.get("tick", 0)))
                agent = self.agents.get(event.get("agent", ""))
                if agent is None:
                    continue
                target = resolve(agent.state, event.get("type", ""))
                if target is None:
                    continue
                agent.state = target
                agent.since_tick = int(event.get("tick", 0))
                agent.ticket = event.get("ticket")
                # WORLD-11: replay rebuilds the same panel detail from the same rows.
                self._refresh_ticket(agent)
                if target == "meeting":
                    note = event.get("note")
                    if note not in (None, "", "join"):
                        agent.meeting_topic = note
                else:
                    agent.meeting_topic = None
                if event.get("room"):
                    agent.room = event["room"]
                if event.get("type") == "message":
                    self.recent_messages.append(event)
                    self.recent_messages = self.recent_messages[-RECENT_MESSAGES:]
                applied += 1
        return applied

    def _reject(self, reason: str, **kw) -> None:
        self.rejections.append(Rejection(reason=reason, **kw))
        del self.rejections[:-50]
