"""Provisional deterministic life loop for v1 (rules only — no LLM, no I/O).

`step(world)` is a pure-ish function of (world state, seed, tick): same inputs,
same events. It is intentionally small and will be replaced by the W1 core
(`world/sim.py`) without touching the transport layer.

Cycle per agent: idle -> working -> review -> break -> idle
Global meeting every MEETING_EVERY ticks, closing after MEETING_LEN ticks.
"""

from __future__ import annotations

from .rng import SubStream
from .world import World

WORK_MIN, WORK_MAX = 6, 14  # ticks (TICK=2s -> 12..28s of "work")
IDLE_MIN, IDLE_MAX = 2, 5
REVIEW_LEN = 3
BREAK_LEN = 4
MEETING_EVERY = 45
MEETING_LEN = 8
MEETING_SIZE = 3

_TICKETS = {
    "hakim": ["WORLD-1", "WORLD-8", "CHAT-11"],
    "layla": ["WORLD-3", "WORLD-6"],
    "maryam": ["WORLD-1", "WORLD-5"],
    "omar": ["WORLD-2", "WORLD-7"],
    "salim": ["WORLD-7", "WORLD-9"],
    "yassin": ["WORLD-4", "WORLD-8"],
}

_MEETINGS = [
    ("standup", ["hakim", "layla", "maryam"]),
    ("infra-sync", ["yassin", "omar"]),
    ("contract-review", ["maryam", "omar", "yassin"]),
    ("research-brief", ["salim", "layla", "hakim"]),
    ("retro", ["hakim", "omar", "yassin", "salim"]),
]


def _stream(world: World, name: str) -> SubStream:
    return SubStream(world.seed, name)


def initial_durations(world: World) -> dict[str, dict]:
    """Deterministic per-agent timing windows, derived from the world seed."""
    plan: dict[str, dict] = {}
    for agent_id in world.agents:
        s = _stream(world, f"plan:{agent_id}")
        plan[agent_id] = {
            "work": s.between(WORK_MIN, WORK_MAX),
            "idle": s.between(IDLE_MIN, IDLE_MAX),
            "ticket": s.pick(_TICKETS.get(agent_id, ["WORLD-?"])),
        }
    return plan


def step(world: World, plan: dict[str, dict] | None = None) -> list[dict]:
    """Advance the world by one tick. Returns the events emitted this tick."""
    world.tick += 1
    plan = plan if plan is not None else initial_durations(world)
    events: list[dict] = []
    tick = world.tick

    # 1) global meeting cadence (deterministic rotation, no randomness at runtime)
    if world.meeting is not None:
        if tick - world.meeting.opened_tick >= MEETING_LEN:
            for member in list(world.meeting.members):
                ev = world.emit(member, "meeting_close", note="meeting_close")
                if ev:
                    events.append(ev)
            world.meeting = None
    elif tick > 0 and tick % MEETING_EVERY == 0:
        name, members = _MEETINGS[(tick // MEETING_EVERY - 1) % len(_MEETINGS)]
        present = [m for m in members if m in world.agents]
        if present:
            for member in present:
                ev = world.emit(member, "meeting_open", room="meeting-room", note=name)
                if ev:
                    events.append(ev)
            from .world import Meeting

            world.meeting = Meeting(
                id=f"m-{tick}", room="meeting-room", members=present, opened_tick=tick
            )

    # 2) per-agent life cycle
    for agent_id, agent in world.agents.items():
        if agent.state == "meeting":
            continue
        p = plan[agent_id]
        age = tick - agent.since_tick

        if agent.state == "idle":
            if age >= p["idle"]:
                ev = world.emit(agent_id, "task_start", ticket=p["ticket"], note="day-plan")
                if ev:
                    events.append(ev)
                    agent.since_tick = tick
                    # a fresh task gets a fresh window
                    s = _stream(world, f"work:{agent_id}:{tick}")
                    p["work"] = s.between(WORK_MIN, WORK_MAX)
                    p["ticket"] = s.pick(_TICKETS.get(agent_id, ["WORLD-?"]))
        elif agent.state == "working":
            if age >= p["work"]:
                ev = world.emit(agent_id, "task_done", ticket=agent.ticket, note="handoff-to-review")
                if ev:
                    events.append(ev)
        elif agent.state == "review":
            if age >= REVIEW_LEN:
                ev = world.emit(agent_id, "handoff", ticket=None, note="review-complete")
                if ev:
                    events.append(ev)
        elif agent.state == "break":
            if age >= BREAK_LEN:
                ev = world.emit(agent_id, "handoff", ticket=None, note="coffee-done")
                if ev:
                    events.append(ev)

    return events
