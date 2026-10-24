"""Phase 3.3-A: every shot comes out 16:9 from the latent, never by cropping."""
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
from tests.test_image_checks import SequenceComfy, dark_scene, with_paper_margins
from video.imagegen_comfyui import ComfyClient, GenerationFailed, ImageGenerator, ImageJob


def generator(temp, fake):
    return ImageGenerator(temp, ComfyClient("http://127.0.0.1:8188", session=fake, sleep=lambda _: None))


def ref_job(temp, latent=""):
    portrait, location = Path(temp, "julian.png"), Path(temp, "loft.png")
    Image.new("RGB", (1024, 1024), (50, 40, 30)).save(portrait)
    Image.new("RGB", (1920, 1080), (30, 30, 40)).save(location)
    return ImageJob(job_id="s01_02", prompt="Julian at the window", seed=7, kind="ref",
                    references=[str(portrait), str(location)], latent_source=latent,
                    output_path=str(Path(temp, "images", "shots", "s01_02.jpg")))


class FrameGuardTests(unittest.TestCase):
    def test_square_first_reference_still_requests_a_16x9_latent(self):
        self.assertEqual(config.IMAGEGEN_LATENT_SOURCE, "empty_16x9")
        with tempfile.TemporaryDirectory() as temp:
            gen = generator(temp, FakeComfy())
            job = ref_job(temp)
            graph = gen._graph(job, 7, ["a.png", "b.png"])
            self.assertEqual(graph["7"]["inputs"]["latent_image"], ["44", 0])
            self.assertEqual((graph["44"]["inputs"]["width"], graph["44"]["inputs"]["height"]), (1360, 752))
            self.assertNotEqual(gen.cache_key(job, 7), gen.cache_key(ref_job(temp, "reference"), 7))

    def test_square_output_is_regenerated_from_an_empty_latent_not_cropped(self):
        fake = FakeComfy(sizes=[(1024, 1024), (1360, 752)])
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            record = generator(temp, fake).generate(ref_job(temp, "reference"))
            with Image.open(record["local_path"]) as image:
                self.assertEqual(image.size, (1920, 1080))
        self.assertEqual(len(fake.prompts), 2)
        self.assertNotIn("44", fake.prompts[0])                        # reference latent
        self.assertEqual(fake.prompts[1]["7"]["inputs"]["latent_image"], ["44", 0])
        self.assertEqual(record["image_checks"]["aspect_regenerated"], [1024, 1024])
        self.assertEqual(record["latent_source"], "empty_16x9")

    def test_wrong_ratio_twice_fails_without_writing_a_cropped_shot(self):
        fake = FakeComfy(sizes=[(1024, 1024)] * 2)
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            job = ref_job(temp)
            with self.assertRaises(GenerationFailed) as caught:
                generator(temp, fake).generate(job)
            self.assertFalse(Path(job.output_path).exists())
        self.assertIn("not cropped", str(caught.exception))

    def test_border_crop_never_cuts_more_than_six_percent(self):
        deep = with_paper_margins(dark_scene(), depth=0.06)
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()), \
                patch.object(config, "IMAGEGEN_CHECK_RETRIES", 0):
            job = ImageJob(job_id="s01_01", prompt="p", seed=1, output_path=str(Path(temp, "a.jpg")))
            with self.assertRaises(GenerationFailed) as caught:
                generator(temp, SequenceComfy([deep])).generate(job)
        self.assertIn("max 6%", str(caught.exception))

    def test_portraits_keep_their_square_frame(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            job = ImageJob(job_id="ref_julian_1", prompt="p", seed=1, size=(1024, 1024),
                           final_size=(1024, 1024), output_path=str(Path(temp, "julian_1.jpg")))
            record = generator(temp, FakeComfy()).generate(job)
            with Image.open(record["local_path"]) as image:
                self.assertEqual(image.size, (1024, 1024))


if __name__ == "__main__":
    unittest.main()
