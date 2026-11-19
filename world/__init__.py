"""The office world: a deterministic, LLM-free simulation core.

Public surface::

    from world import initial_state, step, run, state_sha256

    world = initial_state(seed=1)
    world = run(world, 10_000)
    state_sha256(world)   # byte-stable across runs and machines
"""

from __future__ import annotations

from .entities import (
    CYCLE,
    EVENT_KINDS,
    FEED_LEN,
    ROOM_INDEX,
    ROOMS,
    STATES,
    TRANSITIONS,
    Agent,
    Event,
    Room,
    WorldState,
    next_state,
)
from .rng import Stream, stream_seed
from .schedules import AGENT_IDS, DAY_LEN, PROFILES, day_plan, events_at
from .sim import initial_state, run, state_sha256, step

__all__ = [
    "AGENT_IDS",
    "Agent",
    "CYCLE",
    "DAY_LEN",
    "EVENT_KINDS",
    "Event",
    "FEED_LEN",
    "PROFILES",
    "ROOM_INDEX",
    "ROOMS",
    "Room",
    "STATES",
    "Stream",
    "TRANSITIONS",
    "WorldState",
    "day_plan",
    "events_at",
    "initial_state",
    "next_state",
    "run",
    "state_sha256",
    "step",
    "stream_seed",
]
