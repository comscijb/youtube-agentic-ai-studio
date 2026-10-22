"""Phase 3.1-G: GUI image-generation endpoints and pipeline steps (ComfyUI mocked)."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from agents import identity
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401


class GuiImagegenTests(unittest.TestCase):
    def setUp(self):
        import gui
        self.gui = gui
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        atomic_write_json(self.output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
        shots = json.loads(json.dumps(SHOTS))
        (self.output / "images" / "shots").mkdir(parents=True)
        Image.new("RGB", (1920, 1080), (20, 20, 20)).save(self.output / "images" / "shots" / "s01_01.jpg")
        shots[0]["image_path"] = "images/shots/s01_01.jpg"
        shots[2]["image_path"] = "images/shots/s02_01.jpg"     # file missing -> not done
        atomic_write_json(self.output / "shot_list.json", {"shots": shots, "unique_shots": 3})
        (self.output / "character_refs" / "candidates").mkdir(parents=True)
        Image.new("RGB", (64, 64)).save(self.output / "character_refs" / "candidates" / "julian_vance_1.jpg")
        atomic_write_json(self.output / gen.CANDIDATES_FILE, {"character:julian_vance": [
            {"number": 1, "path": "character_refs/candidates/julian_vance_1.jpg", "seed": 5, "prompt": "p",
             "style_hash": identity.style_hash()}]})
        (self.output / "final_video.mp4").write_bytes(b"old video")
        self.patches = [patch.object(config, "OUTPUT_DIR", str(self.output)),
                        patch.object(ComfyClient, "available", return_value=False),
                        patch.dict(gui._state, {"imagegen_busy": False, "running": False})]
        for item in self.patches:
            item.start()
        self.client = gui.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def status(self):
        return self.client.get("/api/imagegen/status").get_json()

    def test_status_reports_references_progress_and_shots(self):
        data = self.status()
        self.assertFalse(data["comfyui"])
        self.assertEqual((data["done"], data["unique_shots"]), (1, 3))
        julian = next(ref for ref in data["references"] if ref["id"] == "julian_vance")
        self.assertFalse(julian["approved"])
        self.assertEqual(len(julian["candidates"]), 1)
        self.assertIn("julian_vance", data["missing_approvals"])
        self.assertEqual([shot["shot_id"] for shot in data["shots"]], [s["shot_id"] for s in SHOTS])

    def test_approve_and_serve_files_safely(self):
        response = self.client.post("/api/imagegen/approve",
                                    json={"kind": "character", "id": "julian_vance", "number": 1})
        self.assertTrue(response.get_json()["ok"])
        self.assertTrue(next(ref for ref in self.status()["references"]
                             if ref["id"] == "julian_vance")["approved"])
        ok = self.client.get("/imagegen/file?path=images/shots/s01_01.jpg")
        self.assertEqual(ok.status_code, 200)
        ok.close()
        self.assertEqual(self.client.get("/imagegen/file?path=../../secret.png").status_code, 404)
        bad = self.client.post("/api/imagegen/approve", json={"kind": "character", "id": "x", "number": 9})
        self.assertEqual(bad.status_code, 400)

    def test_edit_prompt_invalidates_only_that_shot_and_the_video(self):
        response = self.client.post("/api/imagegen/shot/s01_01/prompt", json={"action": "He looks away"})
        self.assertTrue(response.get_json()["ok"])
        shots = load_json(self.output / "shot_list.json")["shots"]
        self.assertEqual(shots[0]["action"], "He looks away")
        self.assertNotIn("image_path", shots[0])
        self.assertEqual(shots[2]["image_path"], "images/shots/s02_01.jpg")
        self.assertFalse((self.output / "final_video.mp4").exists())
        empty = self.client.post("/api/imagegen/shot/s01_01/prompt", json={"action": " "})
        self.assertEqual(empty.status_code, 400)

    def test_regenerate_uses_a_new_seed_for_that_shot_only(self):
        with patch.object(gen, "generate_shots") as run:
            response = self.client.post("/api/imagegen/shot/s01_01/regenerate")
            self.assertTrue(response.get_json()["ok"])
            for _ in range(50):
                if run.called and not self.gui._state["imagegen_busy"]:
                    break
                time.sleep(0.05)
        self.assertEqual(run.call_args.kwargs["only"], "s01_01")
        self.assertEqual(load_json(self.output / "shot_list.json")["shots"][0]["seed_offset"], 1)
        self.assertEqual(self.client.post("/api/imagegen/shot/zz/regenerate").status_code, 404)

    def test_pipeline_stops_politely_before_approval(self):
        script = {"sections": []}
        (self.output / "narration.mp3").write_bytes(b"audio")       # narration step already done
        with patch.object(self.gui, "_state", dict(self.gui._state, research={"x": 1}, script=script)), \
                patch.object(gen, "ensure_visual_bible", return_value=VISUAL_BIBLE), \
                patch.object(gen, "ensure_shot_list"), \
                patch.object(gen, "generate_shots", side_effect=gen.ApprovalRequired("Approve julian_vance")), \
                patch("video.imagegen_comfyui.ImageGenerator"):
            stages = self.gui._state["stages"]
            self.gui._run_pipeline(["visual_bible", "shots", "video"])
        logs = []
        while not self.gui._log_queue.empty():
            logs.append(self.gui._log_queue.get())
        self.assertTrue(any("Approve julian_vance" in message for _, message in logs))
        self.assertTrue(any(kind == "done" for kind, _ in logs))


if __name__ == "__main__":
    unittest.main()
