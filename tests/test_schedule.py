"""WORLD-3 tests: room conflicts, mandatory break, determinism. stdlib only."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from office.schedule import (  # noqa: E402
    AGENTS, MINUTES_PER_DAY, Meeting, build_day, default_meetings, to_json,
    validate_meetings,
)


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class ScheduleTests(unittest.TestCase):

    def setUp(self):
        self.plan = build_day()

    def test_no_two_meetings_share_a_room_at_the_same_time(self):
        meetings = self.plan["meetings"]
        for i, a in enumerate(meetings):
            for b in meetings[i + 1:]:
                if a["room"] != b["room"]:
                    continue
                overlap = _minutes(a["start"]) < _minutes(b["end"]) and \
                    _minutes(b["start"]) < _minutes(a["end"])
                self.assertFalse(overlap, f"{a['id']} overlaps {b['id']} in {a['room']}")

    def test_room_conflict_is_rejected_by_validator(self):
        bad = [Meeting("x", "room-1", 600, 700, ("hakim",)),
               Meeting("y", "room-1", 650, 750, ("layla",))]
        with self.assertRaises(ValueError):
            validate_meetings(bad)

    def test_attendee_double_booking_is_rejected(self):
        bad = [Meeting("x", "room-1", 600, 700, ("hakim",)),
               Meeting("y", "room-2", 650, 750, ("hakim",))]
        with self.assertRaises(ValueError):
            validate_meetings(bad)

    def test_back_to_back_meetings_are_allowed(self):
        ok = [Meeting("x", "room-1", 600, 700, ("hakim",)),
              Meeting("y", "room-1", 700, 800, ("hakim",))]
        validate_meetings(ok)  # must not raise

    def test_every_agent_has_at_least_one_break(self):
        for entry in self.plan["agents"]:
            states = {b["state"] for b in entry["blocks"]}
            self.assertIn("break", states, entry["agent"])

    def test_fully_booked_agent_is_rejected_instead_of_breaking_the_break_rule(self):
        # Meetings cover every work minute -> no room left for the mandatory break.
        full = [Meeting("all", "room-1", 9 * 60, 17 * 60, ("hakim",))]
        with self.assertRaises(ValueError):
            build_day(agents=("hakim",), meetings=full)

    def test_blocks_cover_full_day_without_gaps(self):
        for entry in self.plan["agents"]:
            blocks = entry["blocks"]
            self.assertEqual(blocks[0]["start"], "00:00", entry["agent"])
            self.assertEqual(blocks[-1]["end"], "24:00", entry["agent"])
            for prev, nxt in zip(blocks, blocks[1:]):
                self.assertEqual(prev["end"], nxt["start"], entry["agent"])
                self.assertNotEqual(prev["state"], nxt["state"])
            for b in blocks:
                self.assertIn(b["state"], {"work", "break", "meeting", "idle"})

    def test_meeting_attendees_are_in_meeting_state_during_meeting(self):
        for m in self.plan["meetings"]:
            for agent in m["attendees"]:
                entry = next(e for e in self.plan["agents"] if e["agent"] == agent)
                for b in entry["blocks"]:
                    if _minutes(b["start"]) >= _minutes(m["start"]) and \
                            _minutes(b["end"]) <= _minutes(m["end"]):
                        self.assertEqual(b["state"], "meeting", (agent, m["id"]))

    def test_output_is_deterministic(self):
        self.assertEqual(to_json(build_day()), to_json(build_day()))
        self.assertEqual(json.loads(to_json(self.plan)), self.plan)

    def test_cli_output_matches_library_output(self):
        out = subprocess.run([sys.executable, "-m", "office.schedule"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, to_json(build_day()))

    def test_all_six_agents_present(self):
        self.assertEqual([e["agent"] for e in self.plan["agents"]], list(AGENTS))
        self.assertEqual(len(AGENTS), 6)


if __name__ == "__main__":
    unittest.main()
