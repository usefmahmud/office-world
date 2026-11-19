"""WORLD-6 tests: meeting conflicts, meeting events, reminders, nudges, determinism. stdlib only."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from office.meetings import (  # noqa: E402
    Meeting, MeetingConflict, book, find_conflicts, meeting_events,
)
from office.reminders import (  # noqa: E402
    Commitment, add_commitment, due_nudges, mark_done, parse_commitment, snapshot,
)


def m(id_, room, start, end, attendees, organizer=None):
    """Meeting fixture. Times are minutes of day; contract caps duration at 60 min."""
    return Meeting(id_, organizer or attendees[0], tuple(attendees), room, start, end)


def must(c):
    assert c is not None, "expected a commitment"
    return c


class MeetingConflictTests(unittest.TestCase):

    def test_same_room_overlap_is_rejected(self):
        base = [m("a", "room-1", 600, 630, ["hakim", "layla"])]
        with self.assertRaises(MeetingConflict) as ctx:
            book(base, m("b", "room-1", 615, 645, ["maryam", "omar"]))
        self.assertIn("room", str(ctx.exception))

    def test_shared_attendee_overlap_is_rejected_even_in_other_room(self):
        base = [m("a", "room-1", 600, 630, ["hakim", "layla"])]
        with self.assertRaises(MeetingConflict) as ctx:
            book(base, m("b", "room-2", 615, 645, ["layla", "omar"]))
        self.assertIn("attendee:layla", str(ctx.exception))

    def test_back_to_back_is_allowed(self):
        base = [m("a", "room-1", 600, 630, ["hakim", "layla"])]
        out = book(base, m("b", "room-1", 630, 660, ["hakim", "layla"]))
        self.assertEqual([x.id for x in out], ["a", "b"])

    def test_disjoint_rooms_and_people_are_allowed(self):
        base = [m("a", "room-1", 600, 630, ["hakim", "layla"])]
        out = book(base, m("b", "room-2", 600, 630, ["maryam", "omar"]))
        self.assertEqual(len(out), 2)

    def test_duplicate_id_is_rejected(self):
        base = [m("a", "room-1", 600, 630, ["hakim", "layla"])]
        with self.assertRaises(MeetingConflict):
            book(base, m("a", "room-2", 900, 930, ["maryam", "omar"]))

    def test_daily_cap_is_enforced(self):
        sched: tuple[Meeting, ...] = ()
        for i, start in enumerate((540, 600, 660)):
            sched = book(sched, m(f"x{i}", "room-1", start, start + 30, ["hakim", "layla"]))
        with self.assertRaises(MeetingConflict):
            book(sched, m("x3", "room-2", 900, 930, ["maryam", "omar"]))

    def test_invalid_shapes_are_rejected(self):
        bad = [
            lambda: m("a", "nowhere", 600, 630, ["hakim", "layla"]),        # unknown room
            lambda: m("a", "room-1", 600, 600, ["hakim", "layla"]),         # zero length
            lambda: m("a", "room-1", 600, 662, ["hakim", "layla"]),         # 62 min > 3600 ticks
            lambda: m("a", "room-1", 600, 630, ["hakim"]),                  # one attendee
            lambda: m("a", "room-1", 600, 630, ["hakim", "ghost"]),         # unknown agent
            lambda: m("a", "room-1", 600, 630, ["layla", "maryam"], organizer="hakim"),
        ]
        for make in bad:
            with self.subTest(make=make):
                with self.assertRaises(MeetingConflict):
                    make()

    def test_find_conflicts_reports_pairs_in_stable_order(self):
        meetings = [
            m("c", "room-1", 615, 645, ["hakim", "layla"]),
            m("a", "room-1", 600, 630, ["hakim", "maryam"]),
        ]
        self.assertEqual(find_conflicts(meetings), [("a", "c", "room")])


class MeetingEventTests(unittest.TestCase):

    def setUp(self):
        self.meetings = [
            m("standup", "room-1", 600, 615, ["hakim", "layla", "maryam"]),
            m("handoff", "room-2", 930, 960, ["layla", "omar"]),
        ]

    def test_open_and_close_per_attendee_with_post_state(self):
        ev = meeting_events(self.meetings)
        self.assertEqual(len(ev), 2 * 3 + 2 * 2)
        opens = [e for e in ev if e["type"] == "meeting_open"]
        closes = [e for e in ev if e["type"] == "meeting_close"]
        self.assertTrue(all(e["state"] == "meeting" for e in opens))
        self.assertTrue(all(e["state"] == "idle" for e in closes))
        self.assertTrue(all(e["task_id"] is None and e["target"] is None for e in ev))

    def test_ticks_are_seconds_from_midnight(self):
        first = min(meeting_events(self.meetings), key=lambda e: e["tick"])
        self.assertEqual(first["tick"], 600 * 60)
        self.assertEqual(first["type"], "meeting_open")
        self.assertEqual(first["payload"]["planned_ticks"], 15 * 60)

    def test_same_tick_order_is_close_before_open_then_roster(self):
        back = [m("x", "room-1", 600, 630, ["hakim", "layla"]),
                m("y", "room-2", 630, 660, ["hakim", "maryam"])]
        same_tick = [e for e in meeting_events(back) if e["tick"] == 630 * 60]
        self.assertEqual([e["type"] for e in same_tick],
                         ["meeting_close", "meeting_close", "meeting_open", "meeting_open"])
        self.assertEqual([e["agent"] for e in same_tick[2:]], ["hakim", "maryam"])

    def test_output_is_independent_of_input_order(self):
        self.assertEqual(meeting_events(self.meetings),
                         meeting_events(list(reversed(self.meetings))))


class CommitmentParsingTests(unittest.TestCase):

    def test_egyptian_promise_with_pm_word(self):
        c = must(parse_commitment("layla", "تمام، هبعت المسودة الساعة 3 بالليل"))
        self.assertEqual(c.due, 15 * 60)
        self.assertFalse(c.ambiguous)

    def test_arabic_indic_digits_and_am_word(self):
        c = must(parse_commitment("omar", "هخلص الـPR الساعة ٩ الصبح"))
        self.assertEqual(c.due, 9 * 60)
        self.assertFalse(c.ambiguous)

    def test_bare_hour_is_flagged_ambiguous(self):
        c = must(parse_commitment("layla", "هبعت المسودة الساعة 3"))
        self.assertEqual(c.due, 15 * 60)   # default PM for 1..6
        self.assertTrue(c.ambiguous)

    def test_english_at_time(self):
        c = must(parse_commitment("maryam", "I'll send the notes at 4:30pm"))
        self.assertEqual(c.due, 16 * 60 + 30)

    def test_no_future_marker_is_not_a_commitment(self):
        self.assertIsNone(parse_commitment("layla", "الاجتماع كان الساعة 3"))

    def test_future_marker_without_time_is_not_a_commitment(self):
        self.assertIsNone(parse_commitment("layla", "هبعت المسودة بعدين"))

    def test_unknown_owner_is_refused(self):
        with self.assertRaises(ValueError):
            parse_commitment("intruder", "هبعت الساعة 3")


class ReminderNudgeTests(unittest.TestCase):

    def setUp(self):
        c: Commitment = must(parse_commitment("layla", "هبعت المسودة الساعة 3 بالليل"))
        self.store = add_commitment((), c)   # due 15:00

    def test_reminder_is_global_and_pending(self):
        snap = snapshot(self.store)
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["scope"], "world")
        self.assertEqual(snap[0]["status"], "pending")

    def test_no_nudge_inside_grace(self):
        store, nudges = due_nudges(self.store, now=15 * 60 + 4)
        self.assertEqual(nudges, [])
        self.assertEqual(store[0].status, "pending")

    def test_late_reminder_produces_one_nudge(self):
        store, nudges = due_nudges(self.store, now=15 * 60 + 30)
        self.assertEqual(len(nudges), 1)
        self.assertEqual(nudges[0].agent, "layla")
        self.assertEqual(nudges[0].late_min, 30)
        self.assertEqual(store[0].status, "nudged")

    def test_nudge_is_emitted_once(self):
        store, _ = due_nudges(self.store, now=15 * 60 + 30)
        store2, again = due_nudges(store, now=15 * 60 + 40)
        self.assertEqual(again, [])
        self.assertEqual(store2, store)

    def test_done_reminder_never_nudges(self):
        done = mark_done(self.store, self.store[0].id)
        _, nudges = due_nudges(done, now=23 * 60)
        self.assertEqual(nudges, [])

    def test_adding_same_commitment_twice_is_idempotent(self):
        c = must(parse_commitment("layla", "هبعت المسودة الساعة 3 بالليل"))
        self.assertEqual(len(add_commitment(self.store, c)), 1)


class DeterminismTests(unittest.TestCase):

    def test_same_inputs_same_outputs(self):
        def run():
            sched = book((), m("a", "room-1", 600, 630, ["hakim", "layla"]))
            sched = book(sched, m("b", "room-2", 720, 750, ["maryam", "omar"]))
            store: tuple = ()
            for msg in ("هبعت المسودة الساعة 3 بالليل", "هراجع الساعة 10 الصبح"):
                c = parse_commitment("layla", msg)
                if c is not None:
                    store = add_commitment(store, c)
            store, nudges = due_nudges(store, now=23 * 60)
            return meeting_events(sched), snapshot(store), [n.__dict__ for n in nudges]

        self.assertEqual(run(), run())

    def test_store_order_does_not_change_snapshot(self):
        a = must(parse_commitment("layla", "هبعت الساعة 5 بالليل"))
        b = must(parse_commitment("omar", "هخلص الساعة 9 الصبح"))
        one = add_commitment(add_commitment((), a), b)
        two = add_commitment(add_commitment((), b), a)
        self.assertEqual(snapshot(one), snapshot(two))


if __name__ == "__main__":
    unittest.main()
