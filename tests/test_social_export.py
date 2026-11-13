"""Phase 4.0-H: the TikTok / Reels export package."""
import tempfile
import unittest
from pathlib import Path

from agents.narrative import atomic_write_json
from uploader import social_export
from video import shorts


class ExportTests(unittest.TestCase):
    def test_package_contents(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as out:
            run = Path(temp, "2026-11-12-his-own-face")
            (run / shorts.FOLDER).mkdir(parents=True)
            (run / shorts.FOLDER / "short_1.mp4").write_bytes(b"mp4")
            atomic_write_json(run / "script.json", {"narrative_type": "philosophical_thought_experiment"})
            atomic_write_json(run / "description_parts.json", {"topic_words": ["ai clone"]})
            plan_file = run / getattr(shorts, "PLAN_FILE", "shorts_plan.json")
            atomic_write_json(plan_file, {"segments": [{"id": "short_1", "title": "He Woke Up Next to His Own Face #Shorts",
                                                        "hook_text": "Who woke up?"}]})
            original = shorts.load
            shorts.load = lambda folder: {"segments": [{"id": "short_1", "title": "He Woke Up Next to His Own Face #Shorts",
                                                       "hook_text": "Who woke up?"}]}
            try:
                found = social_export.export(run, "short_1", out_root=out)
            finally:
                shorts.load = original
            folder = Path(found["folder"])
            self.assertEqual(folder, Path(out) / run.name)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["short_1.mp4", "short_1.txt"])
            self.assertEqual((folder / "short_1.mp4").read_bytes(), b"mp4")
            text = (folder / "short_1.txt").read_text(encoding="utf-8")
            self.assertIn("== TikTok ==", text)
            self.assertIn("== Instagram Reels ==", text)
            self.assertIn("Who woke up? He Woke Up Next to His Own Face", text)
            self.assertNotIn("#Shorts", text)
            self.assertEqual(text.count("Full story on YouTube: Private Gravity"), 2)
            for line in [line for line in text.splitlines() if line.startswith("#")]:
                self.assertTrue(3 <= len(line.split()) <= 5)
            self.assertIn("#thoughtexperiment", text)

    def test_an_unrendered_short_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(LookupError):
                social_export.export(temp, "short_9")


if __name__ == "__main__":
    unittest.main()
