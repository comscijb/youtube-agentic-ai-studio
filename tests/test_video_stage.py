"""Phase 3.9.4-F: the Video stage — honest warnings, preflight, a Stop that stops the encoder."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from agents.narrative import atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import creator, render_check

SECTIONS = [{"id": 1, "title": "One", "section_type": "opening"}, {"id": 2, "title": "Two", "section_type": "resolution"}]


def project(temp, *, missing=(), size=(1920, 1080), end=20.0):
    folder = Path(temp)
    shots = []
    for index, shot_id in enumerate(("s01_01", "s02_01", "s02_02")):
        relative = f"images/shots/{shot_id}.jpg"
        if shot_id not in missing:
            (folder / "images/shots").mkdir(parents=True, exist_ok=True)
            Image.effect_noise(size, 40).convert("RGB").save(folder / relative, quality=60)
        shots.append({"shot_id": shot_id, "section_id": 1 if index == 0 else 2, "image_path": relative,
                      "start": index * end / 3, "end": (index + 1) * end / 3, "narration_excerpt": "x"})
    atomic_write_json(folder / "shot_list.json", {"shots": shots})
    atomic_write_json(folder / "scene_timeline.json", {"total_duration": 20.0})
    return folder


class PreflightTests(unittest.TestCase):
    def report(self, folder, seconds=None):
        result = render_check.preflight(folder, seconds)
        return result, {item["name"]: item for item in result["checks"]}

    def test_a_good_project_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            result, checks = self.report(project(temp))
        self.assertTrue(result["ok"])
        self.assertTrue(all(item["ok"] for item in checks.values()), checks)

    def test_missing_image_blocks_and_names_every_shot(self):
        with tempfile.TemporaryDirectory() as temp:
            result, checks = self.report(project(temp, missing=("s01_01", "s02_02")))
        self.assertFalse(result["ok"])
        self.assertIn("s01_01, s02_02", checks["imagens"]["detail"])

    def test_wrong_size_margin_and_timings_warn(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = project(temp, size=(1280, 720), end=12.0)
            with patch("video.qa.borders.detect", return_value={"margin": True}):
                result, checks = self.report(folder)
        self.assertTrue(result["ok"])                      # warnings, not blockers
        self.assertIn("1280x720", checks["tamanho"]["detail"])
        self.assertIn("s01_01", checks["margens"]["detail"])
        self.assertIn("12.0-20.0", checks["tempos"]["detail"])

    def test_route(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", str(project(temp))):
            answer = gui.app.test_client().get("/api/video/preflight").get_json()
        self.assertTrue(answer["ok"])


class ScheduleTests(unittest.TestCase):
    def test_missing_shots_fail_the_stage_naming_all(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = project(temp, missing=("s01_01", "s02_02"))
            with self.assertRaises(render_check.ShotImagesMissing) as failed:
                creator.load_shot_schedule(str(folder), SECTIONS, [7.0, 13.0])
        self.assertEqual(failed.exception.shots, ["s01_01", "s02_02"])
        self.assertIn("s01_01, s02_02", str(failed.exception))

    def test_a_complete_shot_list_schedules(self):
        with tempfile.TemporaryDirectory() as temp:
            schedule = creator.load_shot_schedule(str(project(temp)), SECTIONS, [7.0, 13.0])
        self.assertEqual({key: len(value) for key, value in schedule.items()}, {1: 1, 2: 2})

    def test_no_shot_list_stays_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(creator.load_shot_schedule(temp, SECTIONS, [7.0, 13.0]))


class WarningTests(unittest.TestCase):
    def render(self, temp, shots=True):
        """create_video up to the encode, with moviepy's heavy parts replaced."""
        import numpy as np
        from moviepy.editor import ColorClip
        folder = project(temp) if shots else Path(temp)
        atomic_write_json(Path(temp, "scene_timeline.json"), {"total_duration": 2.0, "scenes": [
            {"id": 1, "duration": 1.0}, {"id": 2, "duration": 1.0}]})

        class Audio:
            duration = 2.0

        def clip(section, duration, *args, **kwargs):
            return ColorClip((64, 36), color=(0, 0, 0), duration=duration)
        script = {"sections": [dict(section, narration="x") for section in SECTIONS], "content_mode": "cinematic_fiction"}
        output = io.StringIO()
        with patch.object(creator, "AudioFileClip", return_value=Audio()), patch.object(creator, "_make_clip", side_effect=clip), \
                patch.object(creator, "_prepare_shots", side_effect=lambda items: items), \
                patch.object(creator, "with_end_card", side_effect=lambda final: final), \
                patch.object(creator.tape, "look", return_value="off"), \
                patch.object(creator, "encode_final", side_effect=StopIteration), redirect_stdout(output):
            with self.assertRaises(StopIteration), patch.object(creator.VideoClip, "set_audio", lambda self, audio: self, create=True):
                creator.create_video(script, "a.mp3", str(folder), {})
        del np
        return output.getvalue()

    def test_no_gradient_warning_when_shots_cover_a_section(self):
        with tempfile.TemporaryDirectory() as temp:
            log = self.render(temp)
        self.assertNotIn("gradient fallback", log)
        self.assertIn("1 planos", log)
        self.assertIn("2 planos", log)
        self.assertIn("Verificação (imagens)", log)

    def test_the_warning_stays_for_a_section_with_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            log = self.render(temp, shots=False)
        self.assertEqual(log.count("gradient fallback"), 2)


class StopTests(unittest.TestCase):
    def test_stop_ends_the_real_encoder_and_leaves_no_file(self):
        from moviepy.editor import ColorClip
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp, "final_video.mp4")
            target.write_bytes(b"earlier render")
            calls = {"count": 0}

            def should_stop():
                calls["count"] += 1
                return calls["count"] > 5
            clip = ColorClip((64, 36), color=(9, 9, 9), duration=5.0)
            with self.assertRaises(creator.RenderStopped):
                creator.encode_final(clip, str(target), should_stop=should_stop, fps=10, codec="libx264", audio=False,
                                     verbose=False, preset="ultrafast")
            self.assertEqual(sorted(os.listdir(temp)), ["final_video.mp4"])
            self.assertEqual(target.read_bytes(), b"earlier render")

    def test_without_stop_the_output_is_replaced(self):
        from moviepy.editor import ColorClip
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp, "final_video.mp4")
            creator.encode_final(ColorClip((64, 36), color=(9, 9, 9), duration=.3), str(target), should_stop=lambda: False,
                                 fps=10, codec="libx264", audio=False, verbose=False, preset="ultrafast")
            self.assertGreater(target.stat().st_size, 0)
            self.assertEqual(os.listdir(temp), ["final_video.mp4"])

    def test_the_pipeline_marks_the_stage_interrupted(self):
        import gui
        page = Path("gui.py").read_text(encoding="utf-8")
        self.assertIn('_stage("video", "interrompido")', page)
        self.assertIn("should_stop=_stop_event.is_set", page)
        del gui


if __name__ == "__main__":
    unittest.main()
