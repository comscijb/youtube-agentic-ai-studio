"""Phase 3.5.1-A: outputs within 3 % of 16:9 are fitted; edits never fall back to an empty latent."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_region_edit import GLASSES, INFO
from tests.test_shot_planning import VISUAL_BIBLE
from video import imagegen_templates as templates
from video.imagegen_comfyui import ComfyClient, GenerationFailed, ImageGenerator, ImageJob, aspect_ok, fit_resize
from video.qa import fixes


class FitTests(unittest.TestCase):
    def test_klein_sizes_near_16x9_are_accepted_and_fitted(self):
        for size in ((1360, 768), (1344, 768), (1360, 752), (1280, 720)):
            with self.subTest(size=size):
                self.assertTrue(aspect_ok(size, (1920, 1080)))
                self.assertEqual(fit_resize(Image.new("RGB", size), (1920, 1080)).size, (1920, 1080))

    def test_square_is_rejected_not_cropped(self):
        self.assertFalse(aspect_ok((1024, 1024), (1920, 1080)))
        with self.assertRaises(GenerationFailed):
            fit_resize(Image.new("RGB", (1024, 1024)), (1920, 1080))

    def test_fit_centre_crops_only_the_small_excess(self):
        image = Image.new("RGB", (1344, 768), (200, 0, 0))
        for x in range(0, 12):                                   # 12-px blue stripes at both short edges
            for y in range(768):
                image.putpixel((x, y), (0, 0, 255))
                image.putpixel((1343 - x, y), (0, 0, 255))
        fitted = fit_resize(image, (1920, 1080))
        self.assertEqual(fitted.size, (1920, 1080))
        self.assertGreater(fitted.getpixel((0, 540))[2], 150)   # 1344x768 is narrower: nothing cut sideways

    def test_tolerance_is_configurable(self):
        with patch.object(config, "IMAGEGEN_ASPECT_TOLERANCE", 0.01):
            self.assertFalse(aspect_ok((1344, 768), (1920, 1080)))

    def test_default_native_size_is_true_16x9_in_multiples_of_16(self):
        width, height = config.IMAGEGEN_SIZE
        self.assertEqual((width % 16, height % 16), (0, 0))
        self.assertAlmostEqual(width / height, 16 / 9, places=2)

    def test_region_canvas_follows_the_source(self):
        self.assertEqual(templates.region_canvas((1920, 1080)), (1360, 768))
        self.assertEqual(templates.region_canvas((1024, 1024)), (1024, 1024))
        graph = templates.build_region_edit(instruction="x", seed=1, shot="s.png", source_size=(1024, 1024),
                                            boxes=[([0.5, 0.5, 1.0, 1.0], "a")])
        self.assertEqual((graph["33"]["inputs"]["width"], graph["33"]["inputs"]["height"]), (1024, 1024))
        self.assertEqual(graph["33"]["inputs"]["editor_state"]["__value__"][0]["x"], 512)


class EditFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.image = self.output / "images" / "shots" / "s01_01.jpg"
        self.image.parent.mkdir(parents=True)
        Image.new("RGB", (1920, 1080), (10, 10, 10)).save(self.image)
        self.patch = patch.object(config, "IMAGEGEN_IMAGE_CHECKS", False)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def fix(self, sizes):
        fake = FakeComfy(object_info=INFO, sizes=sizes)
        generator = ImageGenerator(self.output, ComfyClient("http://127.0.0.1:8188", session=fake,
                                                            sleep=lambda _: None))
        shot = {"shot_id": "s01_01", "qa": {"status": "fail", "verdict": "edit", "issues": [GLASSES]}}
        factory = lambda offset: ImageJob(job_id="s01_01", prompt="p", seed=100 + offset,
                                          output_path=str(self.image), size=tuple(config.IMAGEGEN_SIZE))
        with redirect_stdout(io.StringIO()), patch("agents.visual_bible.approved_reference", return_value=None):
            action = fixes.apply(generator, self.output, shot, VISUAL_BIBLE, image_path=self.image,
                                 job_factory=factory, progress=lambda _: None)
        return action, shot, fake

    def test_an_edit_at_1360x768_is_kept(self):
        action, shot, fake = self.fix([(1360, 768)])
        self.assertEqual(action["action"], "edit")
        self.assertEqual(fake.prompts[0]["33"]["inputs"]["width"], 1360)
        with Image.open(self.image) as image:
            self.assertEqual(image.size, (1920, 1080))

    def test_an_off_frame_edit_keeps_the_original_and_never_uses_an_empty_latent(self):
        before = self.image.read_bytes()
        action, shot, fake = self.fix([(1024, 1024), (1360, 768)])
        self.assertEqual((action["action"], action["tried"]), ("failed", "edit"))
        self.assertIn("original shot is kept", action["error"])
        self.assertEqual(len(fake.prompts), 1)                             # no second render
        self.assertNotIn("44", fake.prompts[0])                            # no empty latent anywhere
        self.assertEqual(self.image.read_bytes(), before)
        self.assertEqual(shot["qa"]["history"], [])
        self.assertEqual(shot["qa"]["actions"][-1]["action"], "failed")


if __name__ == "__main__":
    unittest.main()
