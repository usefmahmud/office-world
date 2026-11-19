"""WORLD-9 — the sim clock is adjustable and derived.

One rule under test: `sim_seconds_per_tick` comes from `WORLD_SIM_SECONDS_PER_TICK`
(default 60, the contract value in `world/dayplan.yaml`), the engine reports it on the
wire, and every simulated timestamp is `t0 + tick * sim_seconds_per_tick` (DT-2) —
so one tick moves the displayed clock by exactly that many seconds.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import main

REPO_ROOT = Path(__file__).resolve().parents[3]
DAYPLAN = REPO_ROOT / "world" / "dayplan.yaml"


def make_client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def reload_main(monkeypatch):
    """Re-import the engine with a given WORLD_SIM_SECONDS_PER_TICK, then put it back."""

    def _reload(value: int | str | None = None):
        if value is None:
            monkeypatch.delenv("WORLD_SIM_SECONDS_PER_TICK", raising=False)
        else:
            monkeypatch.setenv("WORLD_SIM_SECONDS_PER_TICK", str(value))
        return importlib.reload(main)

    yield _reload
    monkeypatch.delenv("WORLD_SIM_SECONDS_PER_TICK", raising=False)
    importlib.reload(main)


def test_default_is_one_simulated_minute_per_tick() -> None:
    assert main.SIM_SECONDS_PER_TICK == 60


def test_engine_default_matches_the_contract() -> None:
    """A page built from the YAML and an engine built from the env must agree."""
    contract = yaml.safe_load(DAYPLAN.read_text(encoding="utf-8"))["sim"]
    assert main.SIM_SECONDS_PER_TICK == contract["tick_seconds"]
    assert main.SIM_T0 == contract["t0"]
    # a 09:00-18:00 day in ticks, at the engine's scale
    assert contract["day_length_ticks"] * main.SIM_SECONDS_PER_TICK == 9 * 3600


def test_sim_time_is_derived_from_tick_and_scale() -> None:
    assert main.sim_time(0) == "2026-01-05T09:00:00Z"
    assert main.sim_time(1) == "2026-01-05T09:01:00Z"     # +60 s
    assert main.sim_time(60) == "2026-01-05T10:00:00Z"    # +1 h after 60 ticks
    assert main.sim_time(540) == "2026-01-05T18:00:00Z"   # the whole day, 540 ticks


def test_snapshot_carries_the_scale_and_a_derived_clock() -> None:
    with make_client() as client:
        state = client.get("/api/world/state").json()
    assert state["sim_seconds_per_tick"] == 60
    assert state["sim_time"] == main.sim_time(state["tick"])
    assert state["ts"] == state["sim_time"], "ts must be derived (DT-2), never the wall clock"


def test_one_tick_advances_the_clock_by_sixty_seconds() -> None:
    with make_client() as client:
        before = client.get("/api/world/state").json()
        client.post("/api/world/tick")
        after = client.get("/api/world/state").json()

    assert after["tick"] == before["tick"] + 1
    assert main.sim_time(after["tick"]) != main.sim_time(before["tick"])
    gap_seconds = (after["tick"] - before["tick"]) * after["sim_seconds_per_tick"]
    assert gap_seconds == 60


def test_delta_frames_advertise_the_scale() -> None:
    with make_client() as client:
        with client.websocket_connect("/ws/world") as ws:
            snapshot = ws.receive_json()
            assert snapshot["world"]["sim_seconds_per_tick"] == 60
            client.post("/api/world/tick")
            delta = ws.receive_json()
            assert delta["type"] == "delta"
            assert delta["sim_seconds_per_tick"] == 60
            assert delta["sim_time"] == main.sim_time(delta["tick"])


def test_scale_is_read_from_the_environment(reload_main) -> None:
    engine = reload_main(900)                       # 15 simulated minutes per tick
    assert engine.SIM_SECONDS_PER_TICK == 900
    assert engine.sim_time(0) == "2026-01-05T09:00:00Z"
    assert engine.sim_time(1) == "2026-01-05T09:15:00Z"
    with TestClient(engine.app) as client:
        assert client.get("/api/world/state").json()["sim_seconds_per_tick"] == 900
        contract = client.get("/api/world/contract").json()
        assert contract["sim_seconds_per_tick"] == 900
        assert contract["day_length_ticks"] == 36     # 9 h / 15 min


def test_a_slow_scale_shrinks_the_day(reload_main) -> None:
    engine = reload_main(1)                         # the old v1.0.0 behaviour
    contract = TestClient(engine.app).get("/api/world/contract").json()
    assert contract["sim_seconds_per_tick"] == 1
    assert contract["day_length_ticks"] == 32400


@pytest.mark.parametrize("bad", ["0", "-5", "sixty"])
def test_a_bad_scale_refuses_to_boot(reload_main, bad: str) -> None:
    with pytest.raises(ValueError):
        reload_main(bad)


def test_message_events_reach_the_wire_and_do_not_move_anybody() -> None:
    """WORLD-10 relies on this: a `message` is a self-loop carrying its room and text."""
    with make_client() as client:
        before = client.get("/api/world/state").json()
        member = next(a for a in before["agents"] if a["state"] != "meeting")
        posted = client.post(
            "/api/world/events",
            json={
                "agent": member["id"],
                "type": "message",
                "room": member["room"],
                "note": "contract review in ten",
            },
        )
        assert posted.status_code == 200
        event = posted.json()["event"]
        assert event["type"] == "message"
        assert event["note"] == "contract review in ten"
        assert event["room"] == member["room"]
        assert event["to"] == event["from"]

        after = client.get("/api/world/state").json()
        same = next(a for a in after["agents"] if a["id"] == member["id"])
        assert same["room"] == member["room"], "a message must not teleport the speaker"
        assert same["state"] == member["state"], "a message is a self-loop (§4)"
