"""Phase 4.2-G: retention at 0:30 and 1:00 for the new cut against the old one, and the preview workspace."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.narrative import atomic_write_json
from uploader import retention


def curve(drop_at):
    return [{"ratio": step / 100, "watch": 1.0 if step / 100 < drop_at else 0.4} for step in range(101)]


class CompareTests(unittest.TestCase):
    def test_watch_at_seconds(self):
        self.assertEqual(retention.watch_at(curve(0.2), 300, 30), 1.0)       # 30 s of 300 = 10 %
        self.assertEqual(retention.watch_at(curve(0.2), 300, 90), 0.4)
        self.assertIsNone(retention.watch_at(curve(0.2), 40, 60))

    def test_new_and_old_episodes(self):
        records = [{"video_id": "a"}, {"video_id": "b", "cut_version": "4.2"}, {"video_id": "c", "cut_version": "4.2"}]
        stats = {"a": {"retention": curve(0.05)}, "b": {"retention": curve(0.5)}, "c": {"retention": curve(0.15)}}
        found = retention.compare_cuts(records, stats.get, lambda record: 300.0)
        self.assertEqual(found["old"], {"videos": 1, "at_30": 0.4, "at_60": 0.4})
        self.assertEqual(found["new"]["videos"], 2)
        self.assertEqual(found["new"]["at_30"], 1.0)
        self.assertEqual(found["new"]["at_60"], 0.7)


class WorkspaceTests(unittest.TestCase):
    def test_the_preview_copies_what_it_needs_from_a_live_project(self):
        from scripts import preview_cut
        with tempfile.TemporaryDirectory() as temp:
            live = Path(temp, "projects", "slug")
            (live / "images" / "shots" / "history").mkdir(parents=True)
            atomic_write_json(live / "script.json", {"sections": []})
            (live / "narration.mp3").write_bytes(b"mp3")
            (live / "images" / "shots" / "s01_01.jpg").write_bytes(b"jpg")
            (live / "images" / "shots" / "history" / "s01_01_v1.jpg").write_bytes(b"old")
            with patch("studio.projects.root", return_value=Path(temp, "projects")), \
                    patch.object(preview_cut, "_archive_files", return_value=(None, {})):
                work = preview_cut.workspace("slug", Path(temp, "work"))
            self.assertTrue((work / "script.json").is_file())
            self.assertTrue((work / "images" / "shots" / "s01_01.jpg").is_file())
            self.assertFalse((work / "images" / "shots" / "history").exists())


if __name__ == "__main__":
    unittest.main()
