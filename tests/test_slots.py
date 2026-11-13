"""Phase 4.0-F: fixed publishing slots and Shorts spacing."""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import shorts_upload, slots

NY = "America/New_York"
SLOTS = {"realistic_psychological_fiction": "TUE 18:00", "philosophical_thought_experiment": "THU 18:00",
         "dark_human_parable": "SAT 18:00"}


def at(*args, zone=NY):
    return datetime(*args, tzinfo=ZoneInfo(zone))


class NextSlotTests(unittest.TestCase):
    def test_same_week_and_across_the_week_boundary(self):
        wednesday = at(2026, 11, 11, 9, 0)
        self.assertEqual(slots.next_slot("philosophical_thought_experiment", now=wednesday, slots=SLOTS, zone=NY), at(2026, 11, 12, 18, 0))
        self.assertEqual(slots.next_slot("realistic_psychological_fiction", now=wednesday, slots=SLOTS, zone=NY), at(2026, 11, 17, 18, 0))
        sunday_night = at(2026, 11, 15, 23, 30)
        self.assertEqual(slots.next_slot("dark_human_parable", now=sunday_night, slots=SLOTS, zone=NY), at(2026, 11, 21, 18, 0))

    def test_too_close_to_the_slot_takes_the_next_week(self):
        thursday = at(2026, 11, 12, 17, 30)                 # 30 min before: no time to upload and process
        self.assertEqual(slots.next_slot("philosophical_thought_experiment", now=thursday, slots=SLOTS, zone=NY), at(2026, 11, 19, 18, 0))

    def test_daylight_saving_keeps_the_wall_clock(self):
        before = at(2026, 10, 28, 12, 0)                    # EDT (UTC-4)
        first = slots.next_slot("philosophical_thought_experiment", now=before, slots=SLOTS, zone=NY)
        after_change = slots.next_slot("philosophical_thought_experiment", now=first + timedelta(hours=1), slots=SLOTS, zone=NY)
        self.assertEqual((first.hour, after_change.hour), (18, 18))
        self.assertEqual(slots.rfc3339(first), "2026-10-29T22:00:00Z")       # EDT
        self.assertEqual(slots.rfc3339(after_change), "2026-11-05T23:00:00Z")  # EST after 1 November
        self.assertEqual(after_change.astimezone(timezone.utc) - first.astimezone(timezone.utc), timedelta(days=7, hours=1))

    def test_unknown_type_has_no_slot(self):
        self.assertIsNone(slots.next_slot("other", now=at(2026, 11, 11, 9), slots=SLOTS, zone=NY))

    def test_settings_defaults(self):
        found = slots.settings()
        self.assertEqual(found["timezone"], NY)
        self.assertEqual(found["slots"]["philosophical_thought_experiment"], "THU 18:00")


class ShortsSpacingTests(unittest.TestCase):
    def test_never_two_shorts_within_three_hours(self):
        base = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)
        found = slots.spread([base, base + timedelta(hours=1), base + timedelta(hours=2), base + timedelta(days=1)], 3)
        self.assertEqual(found, [base, base + timedelta(hours=3), base + timedelta(hours=6), base + timedelta(days=1)])

    def test_several_per_day_with_the_episode_slot_first(self):
        episode = "2026-11-12T23:00:00Z"
        times = shorts_upload.schedule(3, now=datetime(2026, 11, 11, 12, tzinfo=timezone.utc), episode_publish_at=episode,
                                       settings={"first": "with_episode", "spacing_hours": 2, "time": "18:00"})
        self.assertEqual(times[0], datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc))
        gaps = [later - earlier for earlier, later in zip(times, times[1:])]
        self.assertTrue(all(gap >= timedelta(hours=3) for gap in gaps))


if __name__ == "__main__":
    unittest.main()
