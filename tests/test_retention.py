"""Phase 4.0-D: the retention learning loop on synthetic curves (no API call)."""
import unittest
from unittest.mock import MagicMock

from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import retention

TIMELINE = {"total_duration": 100.0, "scenes": [
    {"scene_id": 1, "title": "The First Stone", "start": 0.0, "end": 40.0},
    {"scene_id": 2, "title": "The Fare", "start": 40.0, "end": 100.0}]}
SCRIPT = {"sections": [{"section_type": "opening", "narration": "a"}, {"section_type": "setup", "narration": "b"}]}
WORDS = [{"text": "One.", "start": 30.0, "end": 31.0, "sentence_end": True, "scene_id": 1},
         {"text": "Two", "start": 33.0, "end": 34.0, "sentence_end": False, "scene_id": 1},
         {"text": "more.", "start": 34.0, "end": 39.5, "sentence_end": True, "scene_id": 1},
         {"text": "Three.", "start": 41.0, "end": 42.0, "sentence_end": True, "scene_id": 2}]


def curve(fall_at=0.35, fall=0.18):
    points, watch = [], 1.0
    for step in range(101):
        ratio = step / 100
        if ratio < 0.03:
            watch -= 0.1                                            # the start always falls: ignored
        elif fall_at <= ratio < fall_at + 0.03:
            watch -= fall / 3
        else:
            watch -= 0.002
        points.append({"ratio": ratio, "watch": round(watch, 4), "relative": 0.5})
    return points


class DropTests(unittest.TestCase):
    def test_the_big_drop_is_found_and_the_start_is_ignored(self):
        found = retention.drops(curve(), 100.0)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["ratio"], 0.35, places=2)
        self.assertAlmostEqual(found[0]["seconds"], 35.0, places=1)
        self.assertGreaterEqual(found[0]["drop"], 0.17)

    def test_a_flat_curve_has_no_lesson(self):
        flat = [{"ratio": step / 100, "watch": 1 - step / 500, "relative": 0.5} for step in range(101)]
        self.assertEqual(retention.drops(flat, 100.0), [])


class LocateTests(unittest.TestCase):
    def test_time_maps_to_scene_section_and_sentence(self):
        place = retention.locate(35.0, TIMELINE, SCRIPT, WORDS)
        self.assertEqual((place["scene"], place["section_type"], place["where"]), (1, "opening", "end"))
        self.assertEqual(place["sentence"], "Two more.")
        self.assertEqual(place["sentences_before"], 1)
        self.assertEqual(retention.locate(41.5, TIMELINE, SCRIPT, WORDS)["section_type"], "setup")

    def test_lesson_text(self):
        lessons = retention.lessons_for({"retention": curve()}, TIMELINE, SCRIPT, WORDS)
        self.assertEqual(lessons[0]["key"], "opening:end")
        self.assertIn("queda de 18 % aos 0:35, no fim da abertura", lessons[0]["text"])
        self.assertIn("«Two more.»", lessons[0]["text"])


class DigestTests(unittest.TestCase):
    def test_consistent_lessons_aggregate_and_only_three_reach_the_prompt(self):
        lesson = lambda key, drop=0.1: {"key": key, "text": key, "drop": drop, "seconds": 1, "section_type": key.split(":")[0],
                                        "where": key.split(":")[1]}
        keys = ["opening:end", "setup:middle", "reversal:start", "climax:end"]
        for number in range(4):
            retention.update_digest(f"v{number}", "t", [lesson(key) for key in keys], f"2026-11-0{number + 1}")
        retention.update_digest("v9", "t", [lesson("aftermath:end", 0.5)], "2026-11-09")
        data = retention.update_digest("v0", "t", [lesson("opening:end", 0.3), *[lesson(key) for key in keys[1:]]], "2026-11-01")
        self.assertEqual(data["consistent"][0]["key"], "opening:end")
        self.assertEqual(len(retention.guidance()), 3)
        self.assertNotIn("aftermath", " ".join(retention.guidance()))                  # one video only
        rule = retention.prompt_rule()
        self.assertEqual(rule.count("\n- "), 3)
        self.assertIn("soft guidance", rule)
        self.assertIn("never a rule", rule)

    def test_at_most_ten_videos_are_kept(self):
        for number in range(12):
            retention.update_digest(f"w{number}", "t", [], f"2026-10-{number + 10}")
        self.assertLessEqual(len(retention.update_digest("w0", "t", [], "2026-10-10")["videos"]), 10)


class FetchTests(unittest.TestCase):
    def test_the_curve_query(self):
        analytics = MagicMock()
        analytics.reports().query().execute.return_value = {
            "columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}, {"name": "relativeRetentionPerformance"}],
            "rows": [[0.02, 0.9, 0.5], [0.01, 1.0, 0.5]]}
        found = retention.fetch_curve(analytics, "v1", "2026-11-01", "2026-11-12")
        self.assertEqual([point["ratio"] for point in found], [0.01, 0.02])
        kwargs = analytics.reports().query.call_args.kwargs
        self.assertEqual(kwargs["dimensions"], "elapsedVideoTimeRatio")
        self.assertEqual(kwargs["metrics"], "audienceWatchRatio,relativeRetentionPerformance")
        self.assertEqual(kwargs["filters"], "video==v1")


if __name__ == "__main__":
    unittest.main()
