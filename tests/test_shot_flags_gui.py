"""Phase 3.3.1-D: flags and warnings in the GUI and CLI, and the pipeline's early exits."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator


def flagged_shots():
    shots = json.loads(json.dumps(SHOTS))
    shots[0]["flags"] = ["the action asks for a page layout ['storyboard']; describe one single image"]
    shots[0]["action"] = "A storyboard of his evening"
    shots[2]["warnings"] = ["people beyond the 2 listed character(s) (guests) while others_present is false"]
    return shots


class FlagTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        atomic_write_json(self.output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
        atomic_write_json(self.output / "shot_list.json",
                          {"version": 2, "shots": flagged_shots(), "unique_shots": 3, "flagged": ["s01_01"]})

    def tearDown(self):
        self.temp.cleanup()


class GenerationTests(FlagTestCase):
    def test_flagged_shots_are_skipped_and_warned_ones_generated(self):
        fake, notes = FakeComfy(), []
        with patch.object(gen, "missing_approvals", return_value=[]):
            summary = gen.generate_shots(self.output, ImageGenerator(self.output, ComfyClient(
                "http://127.0.0.1:8188", session=fake, sleep=lambda _: None)), progress=notes.append)
        self.assertEqual(summary["flagged"], ["s01_01"])
        self.assertEqual(summary["warned"], ["s02_01"])
        self.assertEqual(summary["generated"], 2)
        saved = {shot["shot_id"]: shot for shot in load_json(self.output / "shot_list.json")["shots"]}
        self.assertNotIn("image_path", saved["s01_01"])
        self.assertIn("image_path", saved["s02_01"])
        self.assertTrue(any("flagged shot(s) skipped until edited: s01_01" in note for note in notes))

    def test_cli_lists_flagged_shots_at_the_end(self):
        from scripts import generate_shots
        with patch.object(gen, "ensure_shot_list", return_value=load_json(self.output / "shot_list.json")), \
                patch.object(gen, "plan_and_render", return_value={
                    "generated": 2, "cached": 0, "fallback": 0, "failed": [], "seconds": 1.0,
                    "flagged": ["s01_01"], "warned": []}), \
                patch.object(generate_shots, "ImageGenerator"), redirect_stdout(io.StringIO()) as out:
            code = generate_shots.main(["--output-dir", str(self.output), "--no-thumbnail"])
        self.assertEqual(code, 0)
        self.assertIn("Flagged shots (skipped until edited", out.getvalue())


class GuiTests(FlagTestCase):
    def setUp(self):
        super().setUp()
        import gui
        self.gui = gui
        self.patches = [patch.object(config, "OUTPUT_DIR", str(self.output)),
                        patch.object(ComfyClient, "available", return_value=False),
                        patch.dict(gui._state, {"imagegen_busy": False, "running": False})]
        for item in self.patches:
            item.start()
        self.client = gui.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def test_status_shows_flags_and_warnings(self):
        shots = {shot["shot_id"]: shot for shot in self.client.get("/api/imagegen/status").get_json()["shots"]}
        self.assertIn("page layout", shots["s01_01"]["flags"][0])
        self.assertIn("guests", shots["s02_01"]["warnings"][0])

    def test_editing_a_flagged_shot_clears_the_flag_after_revalidation(self):
        still_bad = self.client.post("/api/imagegen/shot/s01_01/prompt", json={"action": "A comic page of him"})
        self.assertEqual(still_bad.status_code, 400)
        self.assertTrue(load_json(self.output / "shot_list.json")["shots"][0].get("flags"))
        fixed = self.client.post("/api/imagegen/shot/s01_01/prompt", json={"action": "He waits by the window"})
        self.assertEqual(fixed.get_json(), {"ok": True, "warnings": []})
        saved = load_json(self.output / "shot_list.json")
        self.assertNotIn("flags", saved["shots"][0])
        self.assertEqual(saved["flagged"], [])
        report = load_json(self.output / shots_module.REPORT_FILE)
        self.assertEqual(report["flagged"], {})
        self.assertIn("s02_01", report["advisory"])
        cleared = self.client.post("/api/imagegen/shot/s02_01/prompt", json={"action": "They face each other"})
        self.assertEqual(cleared.get_json()["warnings"], [])
        self.assertNotIn("warnings", load_json(self.output / "shot_list.json")["shots"][2])

    def test_pipeline_runs_shots_or_visual_bible_alone(self):
        (self.output / "narration.mp3").write_bytes(b"audio")
        for steps in (["shots"], ["visual_bible"]):
            with self.subTest(steps=steps), \
                    patch.object(self.gui, "_state", dict(self.gui._state, research={"x": 1},
                                                          script={"sections": []})), \
                    patch.object(gen, "ensure_visual_bible", return_value=VISUAL_BIBLE) as bible, \
                    patch.object(gen, "ensure_shot_list"), \
                    patch.object(gen, "plan_and_render", return_value={"failed": [], "flagged": []}) as shots, \
                    patch.object(gen, "generate_thumbnail_image"), \
\
                    patch("video.imagegen_comfyui.ImageGenerator"), redirect_stdout(io.StringIO()):
                self.gui._run_pipeline(steps)
            logs = []
            while not self.gui._log_queue.empty():
                logs.append(self.gui._log_queue.get())
            self.assertFalse(any("Partial pipeline complete after Story Brief" in message
                                 or "after Narrative Development" in message for _, message in logs), steps)
            self.assertEqual(shots.called, steps == ["shots"])
            self.assertEqual(bible.called, steps == ["visual_bible"])


if __name__ == "__main__":
    unittest.main()
