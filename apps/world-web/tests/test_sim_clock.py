"""WORLD-9 — the page clock follows the sim scale, not a hardcoded 1 Hz.

The display clock is derived (DT-2): `t0 + tick * sim_seconds_per_tick`. The scale is the
contract default (60 s/tick) and is overridden by the engine's `sim_seconds_per_tick`
when the wire carries it, so `WORLD_SIM_SECONDS_PER_TICK` really does reach the screen.
"""

from __future__ import annotations

from contract_projection import INDEX_HTML, embedded, project, DAYPLAN

HTML = INDEX_HTML.read_text(encoding="utf-8")
CONTRACT = embedded()


def test_contract_scale_is_one_simulated_minute_per_tick() -> None:
    sim = CONTRACT["sim"]
    assert sim["tick_seconds"] == 60
    assert sim["day_length_ticks"] == 540
    hours = (int(sim["day_end"][:2]) - int(sim["day_start"][:2]))
    assert sim["day_length_ticks"] * sim["tick_seconds"] == hours * 3600


def test_the_projection_carries_the_scale_from_the_yaml() -> None:
    assert project(DAYPLAN)["sim"] == CONTRACT["sim"]


def test_the_clock_is_derived_from_t0_and_the_scale() -> None:
    # one derivation, no wall clock read
    assert "Date.parse(SIM.t0) + tick * secondsPerTick * 1000" in HTML
    assert "Date.now()" not in HTML
    assert "new Date()" not in HTML


def test_the_page_accepts_the_engine_scale_on_every_frame_shape() -> None:
    assert "sim_seconds_per_tick" in HTML
    for handler in ("case 'snapshot'", "case 'patch'", "case 'delta'"):
        assert handler in HTML
    assert "setSimScale(snap.sim_seconds_per_tick)" in HTML
    assert HTML.count("setSimScale(msg.sim_seconds_per_tick)") == 2   # patch + delta


def test_the_rate_is_visible() -> None:
    assert 'id="rate"' in HTML
    assert "secondsPerTick + ' s/tick'" in HTML


def test_hour_minute_labels_stay_inside_the_day() -> None:
    """With 60 s/tick the day plan must land inside 09:00-18:00 of sim time."""
    sim = CONTRACT["sim"]

    def minutes(hhmm: str) -> int:
        hours, mins = hhmm.split(":")
        return int(hours) * 60 + int(mins)

    start_min, end_min = minutes(sim["day_start"]), minutes(sim["day_end"])
    for entry in CONTRACT["schedule"]:
        assert start_min <= minutes(entry["at"]) <= end_min, entry
