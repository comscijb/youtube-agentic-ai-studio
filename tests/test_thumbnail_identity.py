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
from agents import identity
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from tests.test_visual_qa import NO_GLASSES, PASS, responder
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator

BRIEF = {"thumbnail_concept": "A medium shot of an impeccably dressed man at a candlelit dinner table; across "
                              "from him, an empty chair and a half-full wine glass"}


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

    def test_visual_qa_runs_on_the_thumbnail(self):
        self.approve()
        with patch.object(config, "IMAGEGEN_VISUAL_QA", True), \
                patch("agents.gemini_client.generate", side_effect=responder(NO_GLASSES, PASS)), \
                redirect_stdout(io.StringIO()):
            path = gen.generate_thumbnail_image(self.output, self.generator(), progress=lambda _: None)
        self.assertEqual(len(self.fake.prompts), 2)              # regenerated once for the missing glasses
        with Image.open(path) as image:
            self.assertEqual(image.size, (1280, 720))
        record = next(item for item in load_json(self.output / "asset_manifest.json")["generated_assets"]
                      if item["job_id"] == "thumbnail")
        self.assertEqual(record["visual_qa"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
