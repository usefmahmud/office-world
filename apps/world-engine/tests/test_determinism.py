"""Determinism: same seed -> byte-identical world; replay -> same world."""

import hashlib
import json

from app import sim
from app.world import World


def run(seed: int, ticks: int) -> tuple[str, list[dict]]:
    world = World(seed=seed)
    plan = sim.initial_durations(world)
    log: list[dict] = []
    for _ in range(ticks):
        log.extend(sim.step(world, plan))
    snap = world.snapshot()
    snap.pop("ts")  # wall-clock only, never part of the projection
    digest = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    return digest, log


def test_same_seed_same_world():
    a, log_a = run(7, 300)
    b, log_b = run(7, 300)
    assert a == b
    assert log_a == log_b


def test_different_seed_different_day():
    a, _ = run(7, 300)
    c, _ = run(8, 300)
    assert a != c


def test_ten_thousand_ticks_are_stable():
    digest_10k, log = run(7, 10_000)
    digest_10k_again, _ = run(7, 10_000)
    assert digest_10k == digest_10k_again
    # the day must actually contain life, not a frozen office
    types = {e["type"] for e in log}
    assert {"task_start", "task_done", "meeting_open", "meeting_close"} <= types
    assert len(log) > 5_000


def test_no_agent_ever_enters_an_unknown_state():
    world = World(seed=11)
    plan = sim.initial_durations(world)
    for _ in range(500):
        sim.step(world, plan)
        for agent in world.agents.values():
            assert agent.state in ("idle", "working", "review", "break", "meeting")


def test_working_agents_always_carry_a_ticket():
    world = World(seed=3)
    plan = sim.initial_durations(world)
    for _ in range(400):
        sim.step(world, plan)
        for agent in world.agents.values():
            if agent.state == "working":
                assert agent.ticket, f"{agent.id} works anonymously"


def test_replay_restores_the_same_projection(tmp_path):
    world = World(seed=5, state_dir=str(tmp_path))
    plan = sim.initial_durations(world)
    for _ in range(120):
        sim.step(world, plan)
    states = {a.id: (a.state, a.ticket, a.room) for a in world.agents.values()}

    revived = World(seed=5, state_dir=str(tmp_path))
    applied = revived.replay()
    assert applied > 0
    assert revived.tick == world.tick
    assert {a.id: (a.state, a.ticket, a.room) for a in revived.agents.values()} == states
