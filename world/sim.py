"""The deterministic simulation core.

The whole world is one pure function::

    state(t+1) = step(state(t), external_events)

* no wall clock, no filesystem, no network, no LLM, no global RNG;
* the only randomness is a :class:`world.rng.Stream` addressed by
  ``(seed, label, agent, tick)``, so the same seed replays byte-identically;
* integer geometry only — no float ever reaches a snapshot;
* the state is immutable: ``step`` never mutates what it is given.

Run the golden test with ``pytest tests/test_sim.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Iterable, Sequence

from .entities import (
    FEED_LEN,
    ROOM_INDEX,
    Event,
    Agent,
    WorldState,
    next_state,
    target_for,
)
from .rng import Stream
from .schedules import (
    DAY_LEN,
    MESSAGE_MIN_GAP,
    PROFILE_INDEX,
    PROFILES,
    ambient_line,
    events_at,
)

#: Baseline counters kept in every snapshot (all start at zero).
STAT_KEYS: tuple[str, ...] = (
    "applied",
    "rejected",
    "messages",
    "task_starts",
    "task_dones",
    "meetings",
    "breaks",
    "handoffs",
    "idles",
)

_AMBIENT_STATES: tuple[str, ...] = ("break", "meeting", "idle")

_STAT_FOR_KIND: dict[str, str] = {
    "task_start": "task_starts",
    "task_done": "task_dones",
    "message": "messages",
    "meeting_open": "meetings",
    "break": "breaks",
    "handoff": "handoffs",
    "idle": "idles",
}


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def initial_state(seed: int = 0) -> WorldState:
    """Build tick 0 of the world: six agents, idle, standing in the lobby."""
    lobby = ROOM_INDEX["lobby"]
    ax, ay = lobby.anchor
    agents = tuple(
        Agent(
            agent_id=profile.agent_id,
            name=profile.name,
            desk_x=profile.desk_x,
            desk_y=profile.desk_y,
            state="idle",
            x=ax + (index % 3 - 1) * 300,
            y=ay + (index // 3) * 300,
            room="lobby",
            since_tick=0,
        )
        for index, profile in enumerate(PROFILES)
    )
    digest = hashlib.sha256(f"office-world:{seed}".encode("utf-8")).hexdigest()
    return WorldState(
        tick=0,
        seed=seed,
        agents=agents,
        digest=digest,
        feed=(),
        stats=tuple((key, 0) for key in STAT_KEYS),
    )


# --------------------------------------------------------------------------
# Geometry helpers (integer only)
# --------------------------------------------------------------------------


def _round_div(num: int, den: int) -> int:
    """Round ``num/den`` to the nearest integer, halves away from zero."""
    if den == 0:
        raise ZeroDivisionError("den must be non-zero")
    if num >= 0:
        return (2 * num + den) // (2 * den)
    return -((2 * -num + den) // (2 * den))


def _start_leg(agent: Agent, target_x: int, target_y: int, room_id: str, speed: int) -> Agent:
    """Point ``agent`` at a target, straight line, integer interpolation."""
    dx = target_x - agent.x
    dy = target_y - agent.y
    dist = max(abs(dx), abs(dy))
    if dist == 0:
        return replace(
            agent,
            room=room_id,
            dest_room="",
            target_x=target_x,
            target_y=target_y,
            leg_x0=agent.x,
            leg_y0=agent.y,
            leg_k=0,
            leg_n=0,
        )
    steps = max(1, (dist + speed - 1) // speed)
    return replace(
        agent,
        dest_room=room_id,
        target_x=target_x,
        target_y=target_y,
        leg_x0=agent.x,
        leg_y0=agent.y,
        leg_k=0,
        leg_n=steps,
    )


def _advance_leg(agent: Agent) -> Agent:
    """Move one tick along the current straight line."""
    if agent.leg_n <= 0 or agent.leg_k >= agent.leg_n:
        return agent
    nxt_k = agent.leg_k + 1
    dx = agent.target_x - agent.leg_x0
    dy = agent.target_y - agent.leg_y0
    x = agent.leg_x0 + _round_div(dx * nxt_k, agent.leg_n)
    y = agent.leg_y0 + _round_div(dy * nxt_k, agent.leg_n)
    arrived = nxt_k >= agent.leg_n
    return replace(
        agent,
        x=x,
        y=y,
        leg_k=0 if arrived else nxt_k,
        leg_n=0 if arrived else agent.leg_n,
        room=agent.dest_room if arrived else agent.room,
        dest_room="" if arrived else agent.dest_room,
    )


# --------------------------------------------------------------------------
# Event application
# --------------------------------------------------------------------------


def _participants(event: Event) -> tuple[str, ...]:
    if event.kind in ("meeting_open", "meeting_close") and event.participants:
        return event.participants
    return (event.agent_id,)


def _apply_event(
    agent: Agent, event: Event, tick: int, slot: int
) -> tuple[Agent | None, str | None]:
    """Apply one event to one agent.

    Returns ``(new_agent, None)`` when it landed, ``(None, reason)`` when the
    frozen state machine rejects it.
    """
    profile = PROFILE_INDEX[agent.agent_id]

    if event.kind == "message":
        return (
            replace(
                agent,
                messages=agent.messages + 1,
                last_message_tick=tick,
            ),
            None,
        )

    if event.kind == "task_start" and not event.task_id:
        # R2: a `working` desk must always be attributable to a real task.
        return None, "task_start without task_id"

    state = next_state(agent.state, event.kind)
    if state is None:
        return None, f"{event.kind} illegal from {agent.state}"

    task_id = agent.task_id
    if event.kind == "task_start":
        task_id = event.task_id
    elif state in ("break", "idle", "meeting"):
        task_id = None

    if state == agent.state:
        # Already there: refresh the clock, do not restart the walk.
        return replace(agent, since_tick=tick, task_id=task_id), None

    target_x, target_y, room_id = target_for(agent, state, event.room, slot)
    moved = _start_leg(agent, target_x, target_y, room_id, profile.speed)
    return (
        replace(moved, state=state, since_tick=tick, task_id=task_id),
        None,
    )


# --------------------------------------------------------------------------
# The step function
# --------------------------------------------------------------------------


def step(state: WorldState, external_events: Sequence[Event] = ()) -> WorldState:
    """Advance the world exactly one tick.  Pure: ``state`` is untouched."""
    tick = state.tick + 1
    tick_of_day = tick % DAY_LEN

    events: list[Event] = list(events_at(state.seed, tick_of_day))
    events.extend(external_events)
    # Total order on the event tuple: identical inputs ⇒ identical application.
    events.sort(key=Event.sort_key)

    agents: list[Agent] = list(state.agents)
    position = {agent.agent_id: index for index, agent in enumerate(agents)}
    stats = dict(state.stats)
    applied: list[Event] = []

    for event in events:
        for agent_id in _participants(event):
            index = position.get(agent_id)
            if index is None:
                stats["rejected"] = stats.get("rejected", 0) + 1
                continue
            updated, reason = _apply_event(agents[index], event, tick, index)
            if updated is None:
                stats["rejected"] = stats.get("rejected", 0) + 1
                continue
            agents[index] = updated
            stats["applied"] = stats.get("applied", 0) + 1
            counter = _STAT_FOR_KIND.get(event.kind)
            if counter:
                stats[counter] = stats.get(counter, 0) + 1
        applied.append(event)

    # Straight-line movement, one tick per step.
    for index, agent in enumerate(agents):
        if agent.walking:
            agents[index] = _advance_leg(agent)

    # Ambient rule-based chatter (no LLM): rate limited per agent.
    for index, agent in enumerate(agents):
        if agent.walking or agent.state not in _AMBIENT_STATES:
            continue
        if tick - agent.last_message_tick < MESSAGE_MIN_GAP:
            continue
        profile = PROFILE_INDEX[agent.agent_id]
        roller = Stream(state.seed, "ambient-chance", agent.agent_id, str(tick))
        if not roller.chance(profile.social, 1000):
            continue
        speaking = Event(
            tick=tick,
            kind="message",
            agent_id=agent.agent_id,
            room=agent.room,
            text_id=ambient_line(state.seed, agent.agent_id, tick),
            source="ambient",
        )
        agents[index] = replace(
            agent, messages=agent.messages + 1, last_message_tick=tick
        )
        stats["messages"] = stats.get("messages", 0) + 1
        stats["applied"] = stats.get("applied", 0) + 1
        applied.append(speaking)

    digest = _chain(state.digest, applied)
    feed = (state.feed + tuple(event.feed_tuple() for event in applied))[-FEED_LEN:]

    return WorldState(
        tick=tick,
        seed=state.seed,
        agents=tuple(agents),
        digest=digest,
        feed=feed,
        stats=tuple(sorted(stats.items())),
    )


def _chain(previous: str, events: Iterable[Event]) -> str:
    """Running SHA-256 chain over every event ever applied, in order."""
    h = hashlib.sha256(previous.encode("utf-8"))
    for event in events:
        h.update(b"|")
        h.update(
            f"{event.tick}:{event.kind}:{event.agent_id}:"
            f"{event.task_id or ''}:{event.text_id or ''}:{event.source}".encode("utf-8")
        )
    return h.hexdigest()


def run(state: WorldState, ticks: int, external_events: Sequence[Event] = ()) -> WorldState:
    """Advance ``ticks`` ticks and return the final snapshot."""
    if ticks < 0:
        raise ValueError("ticks must be >= 0")
    for _ in range(ticks):
        state = step(state, external_events)
        external_events = ()      # host-supplied events belong to the first tick
    return state


def state_sha256(state: WorldState) -> str:
    """SHA-256 of the canonical snapshot — the value frozen in the golden file."""
    return hashlib.sha256(state.canonical_json().encode("utf-8")).hexdigest()
