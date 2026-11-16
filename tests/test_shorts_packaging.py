"""Phase 4.3-E: format Shorts' description, pinned comment, alternating order and the week spread (no YouTube)."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import after_upload, shorts_upload

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=timezone(timedelta(hours=-5)))
WEEK = {"first": "with_episode", "spread_days": 7, "times": ["12:00", "18:00", "21:00"], "spacing_hours": 24, "time": "18:00"}


class OrderTests(unittest.TestCase):
    def test_formats_alternate(self):
        items = [{"id": f"s{n}", "format": fmt} for n, fmt in enumerate(["hook", "hook", "line", "pov", "dilemma", "countdown"])]
        self.assertEqual([item["format"] for item in shorts_upload.alternate(items)],
                         ["hook", "dilemma", "line", "countdown", "pov", "hook"])

    def test_included_alternates_format_plans_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "shorts").mkdir()
            segments = []
            for number, fmt in enumerate(["line", "hook", "hook"], 1):
                (folder / "shorts" / f"short_0{number}.mp4").write_bytes(b"x")
                segments.append({"id": f"short_0{number}", "format": fmt, "include": True})
            atomic_write_json(folder / "shorts" / "shorts_plan.json", {"mode": "formats", "segments": segments})
            self.assertEqual([item["id"] for item in shorts_upload.included(folder)], ["short_02", "short_01", "short_03"])


class WeekTests(unittest.TestCase):
    def test_first_with_episode_then_across_the_week(self):
        with patch.object(config, "SHORTS_SCHEDULE", WEEK):
            times = shorts_upload.schedule(4, now=NOW)
        self.assertIsNone(times[0])
        later = times[1:]
        self.assertGreaterEqual(later[0] - NOW, timedelta(hours=3))
        self.assertGreaterEqual((later[-1] - NOW).days, 6)
        self.assertTrue(all(later[index + 1] - later[index] >= timedelta(hours=3) for index in range(len(later) - 1)))
        self.assertTrue(all(when.strftime("%H:%M") in WEEK["times"] for when in later))

    def test_several_per_day_never_within_three_hours(self):
        settings = {**WEEK, "spread_days": 1}
        times = shorts_upload.schedule(6, now=NOW, settings=settings)
        days = [when.astimezone(NOW.tzinfo).date() for when in times[1:]]
        self.assertGreater(max(days.count(day) for day in set(days)), 1)
        moments = [NOW] + times[1:]
        self.assertTrue(all(b - a >= timedelta(hours=3) for a, b in zip(moments, moments[1:])))

    def test_scheduled_episode_starts_the_week(self):
        times = shorts_upload.schedule(3, now=NOW, episode_publish_at="2026-11-18T23:00:00Z", settings=WEEK)
        self.assertEqual(times[0], datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc))
        self.assertGreaterEqual(times[1] - times[0], timedelta(hours=3))


class PackagingTests(unittest.TestCase):
    def test_description_and_pinned_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp) / "script.json", {"narrative_type": "dark_human_parable"})
            atomic_write_json(Path(temp) / "story_brief.json", {"topic_words": ["mind reading"]})
            segment = {"format": "dilemma", "hook_line": "Everyone can hear your mind.", "description_line": "A baker's choice.",
                       "question": "What would you do?"}
            text = shorts_upload.description(segment, "https://www.youtube.com/watch?v=EP", temp)
        lines = text.strip().split("\n\n")
        self.assertEqual(lines[0], "Everyone can hear your mind.")
        self.assertIn("Full story: https://www.youtube.com/watch?v=EP", lines)
        self.assertIn("#parable", lines[-1])
        self.assertIn("#MindReading", lines[-1])
        self.assertTrue(lines[-1].endswith("#Shorts"))
        self.assertEqual(shorts_upload.pinned_comment(segment), "What would you do?")
        self.assertEqual(shorts_upload.pinned_comment({"format": "hook"}), "The full story is linked above.")

    def test_related_video_is_the_first_checklist_item(self):
        self.assertEqual(after_upload.ITEMS[0], "related_video")
        ledger = {"publications": [
            {"video_id": "EP", "title": "Episode", "project": "", "state": "published"},
            {"video_id": "S1", "title": "Short", "parent_video_id": "EP", "format": "dilemma", "pinned_comment": "What would you do?"}]}
        with patch.object(after_upload, "community_drafts", return_value={"poll": {"question": "q", "options": ["a"]},
                                                                           "teaser": {"text": "t"}}):
            found = after_upload.checklist("EP", ledger=ledger)
        first = found["items"][0]
        self.assertEqual(first["id"], "related_video")
        self.assertIn("dilemma", first["value"])
        self.assertIn("What would you do?", first["value"])


if __name__ == "__main__":
    unittest.main()
