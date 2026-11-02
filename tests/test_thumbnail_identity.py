"""Phase 3.4-E: the thumbnail shows the protagonist's reference, large, and passes visual QA."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from tests.qa_modes import auto_mode
from agents import identity
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from tests.test_region_edit import INFO
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator

BRIEF = {"thumbnail_concept": "A medium shot of an impeccably dressed man at a candlelit dinner table; across "
                              "from him, an empty chair and a half-full wine glass"}


@auto_mode
class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.vb = json.loads(json.dumps(VISUAL_BIBLE))
        self.vb["thumbnail_shot"] = {"description": "A wide view, the left half empty", "characters": ["julian_vance"],
                                     "location_id": "loft"}
        atomic_write_json(self.output / "story_brief.json", BRIEF)
        self.fake = FakeComfy()

    def tearDown(self):
        self.temp.cleanup()

    def approve(self):
        (self.output / "character_refs").mkdir()
        Image.new("RGB", (64, 64)).save(self.output / "character_refs" / "julian_vance.png")
        self.vb.setdefault("references", {})["character:julian_vance"] = {
            "path": "character_refs/julian_vance.png", "seed": 1, "prompt": "", "style_hash": identity.style_hash(),
            "sheet_version": vbible.CHARACTER_SHEET_VERSION}
        atomic_write_json(self.output / vbible.FILE_NAME, self.vb)

    def generator(self):
        return ImageGenerator(self.output, ComfyClient("http://127.0.0.1:8188", session=self.fake,
                                                       sleep=lambda _: None))

    def test_prompt_uses_the_reference_markers_concept_and_a_large_figure(self):
        shot = gen.thumbnail_shot(self.vb, BRIEF)
        self.assertEqual(shot["characters"], ["julian_vance"])
        self.assertFalse(shot["others_present"])                 # the concept places no guests
        composed = shots_module.compose_shot(shot, self.vb, "unused", assume_references=True)
        self.assertEqual(composed["slots"][0], {"slot": 0, "kind": "character", "id": "julian_vance"})
        prompt = composed["prompt"]
        self.assertIn("the man from the first image, with horn-rimmed glasses", prompt)
        self.assertIn("impeccably dressed man at a candlelit dinner table", prompt)
        self.assertIn("head and shoulders fill at least half of the frame height", prompt)
        self.assertNotIn("the left half empty", prompt)          # the brief's concept wins

    def test_no_generic_face_without_the_protagonist_reference(self):
        atomic_write_json(self.output / vbible.FILE_NAME, self.vb)
        with self.assertRaises(gen.ApprovalRequired):
            gen.generate_thumbnail_image(self.output, self.generator())
        self.assertEqual(self.fake.prompts, [])

    def test_thumbnail_goes_through_the_same_qa_and_edit_path(self):
        from tests.test_qa_pipeline import Detector, GlassesVLM
        from video.qa.layered import LayeredQA
        self.approve()
        self.fake.object_info = INFO
        log = []
        client = ComfyClient("http://127.0.0.1:8188", session=self.fake, sleep=lambda _: None)
        qa = LayeredQA(["deterministic", "local_vlm"], detector=Detector(), vlm=GlassesVLM(log, ["no", "yes"]),
                       comfy=client, progress=lambda _: None)
        with patch.object(config, "IMAGEGEN_VISUAL_QA", True), patch.object(config, "IMAGEGEN_IMAGE_CHECKS", False),                 redirect_stdout(io.StringIO()):
            path = gen.generate_thumbnail_image(self.output, ImageGenerator(self.output, client),
                                                progress=lambda _: None, qa=qa)
        self.assertEqual(len(self.fake.prompts), 2)              # generated, then one region edit for the glasses
        self.assertEqual(self.fake.prompts[1]["33"]["class_type"], "CreateBoundingBoxes")
        self.assertEqual(log, ["vlm", "unload vlm", "vlm", "unload vlm"])
        with Image.open(path) as image:
            self.assertEqual(image.size, (1280, 720))
        saved = load_json(self.output / gen.THUMBNAIL_QA_FILE)["qa"]
        self.assertEqual((saved["verdict"], saved["edits"]), ("pass", 1))
        self.assertTrue((self.output / "images/shots/history/thumbnail_v1.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
