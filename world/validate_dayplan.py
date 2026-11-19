#!/usr/bin/env python3
"""Contract lint for world/dayplan.yaml — W0 gate (WORLD-1).

This is not simulation code: it is the executable half of docs/AC.md. It asserts that the
frozen contract is internally consistent, so no wave can start building on a YAML that
contradicts itself (code W-CFG-01).

Usage:
    python3 world/validate_dayplan.py [path/to/dayplan.yaml]

Requires PyYAML. Exit 0 = contract OK, exit 1 = at least one violation printed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    sys.exit(
        "PyYAML is required to lint the contract.\n"
        "  /usr/bin/python3 has it on this host; W1 must add pyyaml to dev requirements."
    )

DEFAULT = Path(__file__).resolve().parent / "dayplan.yaml"

AGENT_IDS = {"hakim", "layla", "maryam", "omar", "salim", "yassin"}
ROOM_IDS = {"bullpen", "meeting_room", "review_corner", "kitchen", "reading_room"}
STATES = {"idle", "working", "review", "break", "meeting"}
TYPES = {
    "task_start",
    "task_done",
    "message",
    "meeting_open",
    "meeting_close",
    "break",
    "handoff",
    "idle",
}
ENVELOPE_REQUIRED = {"seq", "tick", "ts", "agent", "type", "room", "state", "task_id", "target", "payload"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# schedule `type` is the kind of calendar entry, not an event type: map it to the event it emits.
SCHEDULE_TO_EVENT = {
    "day_open": "task_start",
    "meeting": "meeting_open",
    "break": "break",
    "resume": "task_start",
    "review_sweep": "task_done",
    "day_close": "idle",
}

failures: list[str] = []
checks = 0


def check(cond: bool, ac: str, msg: str) -> bool:
    global checks
    checks += 1
    if not cond:
        failures.append(f"[{ac}] {msg}")
    return bool(cond)


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def main(path: Path) -> int:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))

    # ---- 0. shape ---------------------------------------------------------
    for key in ("version", "sim", "agents", "rooms", "stations", "movement",
                "state_machine", "events", "schedule", "guards", "codes", "wire"):
        check(key in doc, "AC-W0-01", f"top-level key missing: {key}")
    if failures:
        return report()
    check(re.fullmatch(r"\d+\.\d+\.\d+", str(doc["version"])) is not None,
          "AC-W0-01", f"version is not semver: {doc['version']!r}")

    sim, agents, rooms = doc["sim"], doc["agents"], doc["rooms"]
    stations, mv, sm = doc["stations"], doc["movement"], doc["state_machine"]
    events, schedule, codes = doc["events"], doc["schedule"], doc["codes"]

    # ---- 0b. sim clock (DT-2: ts = t0 + tick * tick_seconds) ---------------
    tick_seconds = sim.get("tick_seconds")
    day_seconds = (minutes(sim["day_end"]) - minutes(sim["day_start"])) * 60
    check(isinstance(tick_seconds, int) and tick_seconds >= 1, "AC-W0-10",
          f"sim.tick_seconds must be a positive int, got {tick_seconds!r}")
    check(doc["sim"]["day_length_ticks"] * tick_seconds == day_seconds, "AC-W0-10",
          f"day_length_ticks {doc['sim']['day_length_ticks']} x tick_seconds {tick_seconds} "
          f"!= the {day_seconds}s simulated day between {sim['day_start']} and {sim['day_end']}")

    # ---- 1. agents --------------------------------------------------------
    a_ids = [a["id"] for a in agents]
    check(len(agents) == 6, "AC-W0-01", f"agents: expected 6, got {len(agents)}")
    check(len(set(a_ids)) == len(a_ids), "AC-W0-01", f"duplicate agent ids: {a_ids}")
    check(set(a_ids) == AGENT_IDS, "AC-W0-01", f"agent ids {sorted(set(a_ids) ^ AGENT_IDS)} differ from contract")
    for a in agents:
        check(a["home_room"] in ROOM_IDS, "AC-W0-01", f"{a['id']}: unknown home_room {a['home_room']}")
        check(a.get("secondary_room") in ROOM_IDS, "AC-W0-01", f"{a['id']}: unknown secondary_room")
        check(minutes(a["shift"][0]) < minutes(a["shift"][1]), "AC-W0-01", f"{a['id']}: shift not ordered")

    # ---- 2. rooms ---------------------------------------------------------
    r_ids = [r["id"] for r in rooms]
    canvas = mv["canvas"]
    check(len(rooms) == 5, "AC-W0-01", f"rooms: expected 5, got {len(rooms)}")
    check(set(r_ids) == ROOM_IDS, "AC-W0-01", f"room ids {sorted(set(r_ids) ^ ROOM_IDS)} differ from contract")
    for r in rooms:
        x, y, w, h = (r["rect"][k] for k in "xywh")
        check(all(isinstance(v, int) for v in (x, y, w, h)), "AC-W0-01", f"{r['id']}: rect must be ints")
        check(x >= 0 and y >= 0 and x + w <= canvas["w"] and y + h <= canvas["h"],
              "AC-W0-01", f"{r['id']}: rect outside canvas")
        ax, ay = r["anchor"]["x"], r["anchor"]["y"]
        check(x <= ax <= x + w and y <= ay <= y + h, "AC-W0-01", f"{r['id']}: anchor outside its rect")
        check(1 <= r["capacity"] <= 6, "AC-W0-01", f"{r['id']}: capacity {r['capacity']} out of range")

    # ---- 3. stations inside the bullpen ----------------------------------
    bull = next(r for r in rooms if r["id"] == "bullpen")["rect"]
    check(len(stations) == 6, "AC-W0-07", f"stations: expected 6, got {len(stations)}")
    check(sorted(s["agent"] for s in stations) == sorted(a_ids), "AC-W0-07", "every agent needs exactly one desk")
    for s in stations:
        check(s["room"] in ROOM_IDS, "AC-W0-07", f"{s['id']}: unknown room")
        inside = (bull["x"] <= s["x"] <= bull["x"] + bull["w"]
                  and bull["y"] <= s["y"] <= bull["y"] + bull["h"])
        check(inside, "AC-W0-07", f"{s['id']} at ({s['x']},{s['y']}) is outside the bullpen")
    seats = [(s["x"], s["y"]) for s in stations]
    check(len(set(seats)) == len(seats), "AC-W0-07", "two desks share a seat point")

    # ---- 4. movement ------------------------------------------------------
    check(mv["mode"] == "straight_line", "AC-W0-01", "v1 movement must be straight_line")
    check(mv["pathfinding"] is False, "AC-W0-01", "pathfinding is out of scope for v1")
    check(mv["collision"] is False, "AC-W0-01", "collision is out of scope for v1")
    check(doc["sim"]["determinism"]["io_in_step"] is False, "AC-W1-03", "IO in step() violates DT-1")
    check(doc["sim"]["determinism"]["wall_clock_in_step"] is False, "AC-W1-03", "wall clock in step() violates DT-2")

    # ---- 5. state machine ------------------------------------------------
    check(sm["states"] == ["idle", "working", "review", "break", "meeting"],
          "AC-W0-01", f"states differ from the frozen order: {sm['states']}")
    check(set(sm["states"]) == STATES, "AC-W0-01", "state set differs from contract")
    check(sm["initial"] == "idle", "AC-W0-01", f"initial state must be idle, got {sm['initial']}")
    cyc = sm["canonical_cycle"]
    check(cyc == ["idle", "working", "review", "break", "meeting", "idle"],
          "AC-W0-01", f"canonical cycle is frozen, got {cyc}")
    check(sm["closed"] is True, "AC-W0-01", "transition table must be closed")

    transitions = sm["transitions"]
    seen_types = [t["type"] for t in transitions]
    check(len(seen_types) == len(set(seen_types)) == 8, "AC-W0-03", f"one transition row per type: {seen_types}")
    check(set(seen_types) == TYPES, "AC-W0-03", f"transition types {sorted(set(seen_types) ^ TYPES)} differ from contract")
    for t in transitions:
        for f in t["from"]:
            check(f in STATES, "AC-W0-03", f"{t['type']}: unknown from_state {f}")
        check(t["to"] is None or t["to"] in STATES, "AC-W0-03", f"{t['type']}: unknown to_state {t['to']}")

    # the frozen cycle must be reachable through the table, edge by edge
    edges = {(f, t["to"]) for t in transitions for f in t["from"] if t["to"] is not None}
    for a, b in zip(cyc, cyc[1:]):
        check((a, b) in edges, "AC-W0-01", f"canonical edge {a}->{b} has no transition")

    check(set(sm["timeouts"]) == STATES, "AC-W0-04", f"timeouts keys {sorted(sm['timeouts'])} != states")
    for st, tout in sm["timeouts"].items():
        check(tout["max_ticks"] > 0, "AC-W0-04", f"{st}: max_ticks must be > 0")

    tid = sm["task_id"]
    check(tid["required_on"] == ["task_start", "task_done", "handoff"],
          "AC-W0-04", f"required_on changed: {tid['required_on']}")
    check(not set(tid["required_on"]) & set(tid["forbidden_on"]),
          "AC-W0-04", "a type is both required and forbidden for task_id")
    check(set(tid["required_on"]) | set(tid["forbidden_on"]) | set(tid["optional_on"]) == TYPES,
          "AC-W0-04", "task_id rules do not cover all eight types")
    check(all(re.fullmatch(tid["pattern"], k) for k in ("WORLD-1", "PB-12")),
          "AC-W0-04", "task_id pattern rejects a valid key")
    check(not re.fullmatch(tid["pattern"], "world-1"), "AC-W0-04", "task_id pattern accepts a lowercase key")

    # ---- 6. event schema --------------------------------------------------
    check(events["count"] == 8 == len(events["types"]), "AC-W0-01",
          "event count must be 8 (decision D-01)")
    check(set(events["types"]) == TYPES, "AC-W0-01", "event types differ from the closed vocabulary")
    env = events["envelope"]
    check(ENVELOPE_REQUIRED <= set(env), "AC-W0-02", f"envelope missing {sorted(ENVELOPE_REQUIRED - set(env))}")
    for f in ("task_id", "target"):
        check(env[f]["key_always_present"] is True, "AC-W0-02", f"{f} must always be present as a key")
    for name, t in events["types"].items():
        row = next((x for x in transitions if x["type"] == name), None)
        if row is None:
            check(False, "AC-W0-03", f"{name}: no transition row in the state machine")
            continue
        check(t["to_state"] == row["to"], "AC-W0-03",
              f"{name}: to_state {t['to_state']} contradicts the transition table {row['to']}")
        for fname, spec in t["payload"].items():
            check("type" in spec and "required" in spec, "AC-W0-03", f"{name}.{fname}: needs type + required")
        if name == "message":
            check("llm" not in t["payload"]["text_source"]["enum"], "AC-W0-06",
                  "text_source must not allow llm inside the tick loop")

    # ---- 7. schedule ------------------------------------------------------
    day_start, day_end = minutes(sim["day_start"]), minutes(sim["day_end"])
    last = day_start
    for s in schedule:
        m = minutes(s["at"])
        check(TIME_RE.fullmatch(s["at"]) is not None, "AC-W0-07", f"{s['id']}: bad time {s['at']}")
        check(day_start <= m <= day_end, "AC-W0-07", f"{s['id']}: {s['at']} outside the day window")
        check(m >= last, "AC-W0-07", f"{s['id']}: {s['at']} is not chronologically ordered")
        last = m
        if "room" in s:
            check(s["room"] in ROOM_IDS, "AC-W0-07", f"{s['id']}: unknown room {s['room']}")
        parts = s.get("participants", "all")
        if parts != "all":
            check(set(parts) <= AGENT_IDS, "AC-W0-07", f"{s['id']}: unknown participants {parts}")
            check(len(parts) <= next(r for r in rooms if r["id"] == s["room"])["capacity"],
                  "AC-W0-07", f"{s['id']}: participants exceed room capacity")
        if "planned_ticks" in s:
            event_type = SCHEDULE_TO_EVENT[s["type"]]
            spec = events["types"][event_type]["payload"]["planned_ticks"]
            check(spec["min"] <= s["planned_ticks"] <= spec["max"], "AC-W0-07",
                  f"{s['id']}: planned_ticks {s['planned_ticks']} outside {spec['min']}..{spec['max']}")
        check(m + 0 <= day_end, "AC-W0-07", f"{s['id']}: starts after day_end")

    # the day must open and close the state machine
    check(schedule[0]["type"] == "day_open" and schedule[0]["emit"] == "task_start",
          "AC-W0-07", "the day must open with task_start")
    check(schedule[-1]["type"] == "day_close" and schedule[-1]["emit"] == "idle",
          "AC-W0-07", "the day must close with an idle event")

    # ---- 8. codes ---------------------------------------------------------
    code_names = [c["code"] for c in codes]
    check(len(code_names) == len(set(code_names)), "AC-W0-02", "duplicate error codes")
    for c in codes:
        check({"code", "http", "meaning"} <= set(c), "AC-W0-02", f"code row incomplete: {c}")
    for required_code in ("W-STATE-01", "W-EVT-05", "W-DRIFT-01", "W-DET-01", "W-CFG-01"):
        check(required_code in code_names, "AC-W0-02", f"missing code {required_code}")

    # ---- 9. wire ----------------------------------------------------------
    check(doc["wire"]["patches"]["rate_hz"] == 1, "AC-W2-03", "WS patch rate must be 1 Hz")
    check(doc["wire"]["patches"]["coalesce"] == "per_agent_per_tick", "AC-W2-03", "patches must coalesce")
    check(doc["wire"]["snapshot"]["http"] == "GET /api/world/state", "AC-W2-02", "snapshot route changed")

    # ---- 10. scope guard --------------------------------------------------
    oos = doc["out_of_scope"]
    check("llm_in_tick_loop" in oos, "AC-W0-06", "llm_in_tick_loop must stay out of scope")
    check("docker_deploy_without_owner_approval" in oos, "AC-W5-01",
          "docker deploy must stay behind the owner's approval")

    return report(len(agents), len(rooms), len(TYPES), len(STATES), len(schedule))


def report(n_agents=None, n_rooms=None, n_types=None, n_states=None, n_events=None) -> int:
    if n_agents:
        print(f"agents={n_agents} rooms={n_rooms} event_types={n_types} states={n_states} "
              f"schedule_entries={n_events}")
    if failures:
        print(f"FAIL — {len(failures)} violation(s) / {checks} checks")
        for f in failures:
            print("  " + f)
        return 1
    print(f"OK — {checks} contract checks passed (W-CFG-01 clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT))
