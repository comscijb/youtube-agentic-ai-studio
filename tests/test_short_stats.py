"""Phase 4.3-F: per-format Shorts analytics, the weight suggestion and the Shorts settings (cache only)."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import config
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import short_stats
from video import short_formats, shorts_settings

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def short(video_id, fmt, parent="EP1", published="2026-11-10"):
    record = {"video_id": video_id, "parent_video_id": parent, "published_at": published}
    if fmt:
        record["format"] = fmt
    return record


def stats(views, pct, duration=10, subs=0, published="2026-11-10"):
    return {"published_at": published, "totals": {"views": views, "averageViewPercentage": pct, "averageViewDuration": duration,
                                                  "subscribersGained": subs}}


class AggregateTests(unittest.TestCase):
    def test_by_format_with_clicks_shared_by_views(self):
        records = [{"video_id": "EP1"}, short("a", "hook"), short("b", "hook"), short("c", "line"), short("old", None)]
        cache = {"a": stats(100, 60, 20, 2), "b": stats(300, 40, 10, 1), "c": stats(100, 80, 12), "old": stats(50, 30),
                 "EP1": {"traffic_sources": [{"insightTrafficSourceType": "SHORTS", "views": 55}, {"insightTrafficSourceType": "YT_SEARCH", "views": 9}]}}
        rows = {row["format"]: row for row in short_stats.aggregate(records, cache.get, now=NOW)}
        hook = rows["hook"]
        self.assertEqual((hook["shorts"], hook["views"], hook["subscribers"]), (2, 400, 3))
        self.assertAlmostEqual(hook["avg_view_percentage"], (100 * 60 + 300 * 40) / 400, places=1)
        self.assertAlmostEqual(hook["avg_view_duration"], (100 * 20 + 300 * 10) / 400, places=1)
        self.assertAlmostEqual(hook["episode_clicks"], 55 * 400 / 550, places=1)
        self.assertEqual(rows["recorte"]["shorts"], 1)              # the old cut-downs are the baseline
        self.assertAlmostEqual(sum(row["episode_clicks"] for row in rows.values()), 55, places=1)

    def test_only_the_last_30_days(self):
        records = [short("a", "hook", published="2026-09-01"), short("b", "hook")]
        cache = {"a": stats(999, 90, published="2026-09-01"), "b": stats(10, 50)}
        rows = short_stats.aggregate(records, cache.get, now=NOW)
        self.assertEqual(rows[0]["views"], 10)

    def test_ranked_best_first(self):
        records = [short("a", "hook"), short("b", "line")]
        cache = {"a": stats(10, 20), "b": stats(200, 70)}
        self.assertEqual([row["format"] for row in short_stats.aggregate(records, cache.get, now=NOW)], ["line", "hook"])


class SuggestTests(unittest.TestCase):
    current = {name: 1.0 for name in short_formats.ORDER}

    def row(self, fmt, shorts, per_short, pct):
        return {"format": fmt, "shorts": shorts, "views_per_short": per_short, "avg_view_percentage": pct}

    def test_needs_three_shorts(self):
        found = short_stats.suggest([self.row("hook", 2, 500, 80)], self.current, per_episode=4)
        self.assertFalse(found["ready"])
        self.assertEqual(found["weights"], self.current)

    def test_more_of_what_works_never_below_one_per_two_episodes(self):
        rows = [self.row("hook", 3, 500, 80), self.row("line", 3, 5, 10), self.row("recorte", 9, 1000, 90)]
        found = short_stats.suggest(rows, self.current, per_episode=4)
        weights = found["weights"]
        self.assertTrue(found["ready"])
        self.assertGreater(weights["hook"], weights["line"])
        total = sum(weights.values())
        for name in short_formats.ORDER:
            self.assertGreaterEqual(weights[name] / total, 0.5 / 4 - 1e-3, name)
        self.assertNotIn("recorte", weights)
        self.assertEqual(weights["pov"], 1.0)                       # untried formats keep their weight


class SettingsTests(unittest.TestCase):
    def test_save_and_apply(self):
        with patch.object(config, "SHORTS_FORMATS", dict(short_formats.DEFAULT_WEIGHTS)), \
                patch.object(config, "SHORTS_PER_EPISODE", 4), patch.object(config, "SHORTS_MODE", "formats"), \
                patch.object(config, "SHORTS_TTS_SPEED", 0.95):
            found = shorts_settings.save({"formats": {"line": 0, "hook": 2.5}, "per_episode": 5, "mode": "cuts", "tts_speed": 1.0})
            self.assertEqual(found["formats"]["line"], 0.0)
            self.assertEqual(found["formats"]["hook"], 2.5)
            self.assertEqual((found["per_episode"], found["mode"], found["tts_speed"]), (5, "cuts", 1.0))
            self.assertEqual(short_formats.allocate(), ["hook", "hook", "dilemma", "countdown", "pov"])
            with self.assertRaises(ValueError):
                shorts_settings.save({"per_episode": 9})
            with self.assertRaises(ValueError):
                shorts_settings.save({"formats": {name: 0 for name in short_formats.ORDER}})


if __name__ == "__main__":
    unittest.main()
