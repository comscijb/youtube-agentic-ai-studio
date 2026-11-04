"""Phase 3.1-D: renderer uses shot_list.json timings (moviepy frame function, no encode)."""
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image

import config
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

try:
    from video import creator
except Exception as exc:  # moviepy missing in a minimal environment
    creator = None
    IMPORT_ERROR = exc

SECTIONS = [{"id": 1, "title": "One", "section_type": "opening", "narration": "x"},
            {"id": 2, "title": "Two", "section_type": "climax", "narration": "y"}]


@unittest.skipIf(creator is None, "moviepy is not installed")
class ShotScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        shots_dir = self.output / "images" / "shots"
        shots_dir.mkdir(parents=True)
        for name, color in (("s01_01", (200, 30, 30)), ("s01_02", (30, 30, 200)), ("s02_01", (30, 200, 30))):
            Image.new("RGB", (1920, 1080), color).save(shots_dir / f"{name}.jpg")
        self.shots = [
            {"shot_id": "s01_01", "section_id": 1, "start": 0.0, "end": 6.0, "shot_type": "close_up",
             "image_path": "images/shots/s01_01.jpg", "reuse_of": ""},
            {"shot_id": "s01_02", "section_id": 1, "start": 6.0, "end": 12.0, "shot_type": "wide",
             "image_path": "images/shots/s01_02.jpg", "reuse_of": ""},
            {"shot_id": "s02_01", "section_id": 2, "start": 12.0, "end": 18.0, "shot_type": "medium",
             "image_path": "images/shots/s02_01.jpg", "reuse_of": ""},
            {"shot_id": "s02_02", "section_id": 2, "start": 18.0, "end": 20.0, "shot_type": "insert",
             "reuse_of": "s01_01"},
        ]
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        (self.output / "shot_list.json").write_text(json.dumps({"shots": self.shots}), encoding="utf-8")

    def schedule(self):
        with redirect_stdout(io.StringIO()):
            return creator.load_shot_schedule(str(self.output), SECTIONS, [12.0, 8.0])

    def test_schedule_is_section_relative_and_follows_reuse(self):
        schedule = self.schedule()
        self.assertEqual([(s["start"], s["end"]) for s in schedule[1]], [(0.0, 6.0), (6.0, 12.0)])
        self.assertEqual([(s["start"], s["end"]) for s in schedule[2]], [(0.0, 6.0), (6.0, 8.0)])
        self.assertTrue(schedule[2][1]["image"].endswith("s01_01.jpg"))

    def test_incomplete_or_missing_shot_list_keeps_old_behaviour(self):
        self.shots[2]["image_path"] = "images/shots/missing.jpg"
        self.write()
        from video import render_check
        with self.assertRaises(render_check.ShotImagesMissing):       # never stock or gradients (3.9.4-F)
            self.schedule()
        (self.output / "shot_list.json").unlink()
        self.assertIsNone(self.schedule())

    def test_motion_and_adaptive_overlay(self):
        self.assertEqual(creator.shot_motion("close_up", 0), (1.0, 1.12, (0, 0)))
        self.assertEqual(creator.shot_motion("wide", 0)[2], (1, 0))
        self.assertEqual(creator.shot_motion("wide", 1)[2], (-1, 0))
        self.assertLess(creator.adaptive_opacity(30, 0.38), creator.adaptive_opacity(90, 0.38))
        self.assertEqual(creator.adaptive_opacity(200, 0.38), 0.38)
        self.assertEqual(creator.adaptive_opacity(0, 0.38), 0.08)

    def test_frames_show_the_shot_for_each_moment_with_crossfade(self):
        def dominant(clip, t):
            red, green, blue = clip.get_frame(t)[540, 960][:3]
            return "red" if red > blue + 20 else "blue" if blue > red + 20 else "mixed"

        with patch.object(config, "SHOT_TRANSITIONS", "crossfade"), patch.object(config, "MOTION_STYLE", "kenburns"):
            shots = creator._prepare_shots(self.schedule()[1])
            clip = creator._make_clip(SECTIONS[0], 12.0, [], 1, 2, cinematic=True, shots=shots)
            self.assertEqual(dominant(clip, 2.0), "red")
            self.assertEqual(dominant(clip, 5.75), "mixed")        # 0.5 s crossfade into the next shot
            self.assertEqual(dominant(clip, 9.0), "blue")
        self.assertEqual(creator.shot_at(shots, 6.5), 1)
        # Phase 3.9-B.4: inside a scene the default is a hard cut (no time jump in the narration).
        with patch.object(config, "SHOT_TRANSITIONS", "smart"), patch.object(config, "MOTION_STYLE", "kenburns"):
            shots = creator._prepare_shots(self.schedule()[1])
            clip = creator._make_clip(SECTIONS[0], 12.0, [], 1, 2, cinematic=True, shots=shots)
            self.assertEqual(dominant(clip, 5.75), "red")


if __name__ == "__main__":
    unittest.main()
