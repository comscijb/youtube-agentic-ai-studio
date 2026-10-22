"""Phase 3.2.1: full-bleed images, border/panel detection, clones, dread, calibration seeds."""
import io
import json
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import config
from agents import identity
from agents import shot_list as shots_module
from tests.fake_comfy import FakeComfy, Response
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from video import image_checks
from video.imagegen_comfyui import ComfyClient, ImageGenerator, ImageJob

CALIBRATION = Path("output/style_calibration")


def dark_scene(size=(1360, 752), seed=1):
    """A dark, textured full-bleed 'illustration'."""
    rng = random.Random(seed)
    image = Image.new("RGB", size, (28, 30, 36))
    draw = ImageDraw.Draw(image)
    for _ in range(400):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        shade = rng.randrange(20, 90)
        draw.line([(x, y), (x + rng.randrange(-40, 40), y + rng.randrange(-40, 40))],
                  fill=(shade, shade, shade + 5), width=2)
    return image


def with_paper_margins(image, depth=0.06, ragged=True):
    """Cream torn-paper margins on all four edges."""
    framed = image.copy()
    draw = ImageDraw.Draw(framed)
    w, h = framed.size
    rng = random.Random(3)
    cream = (232, 224, 205)
    for x in range(w):
        d = int(h * depth * (0.7 + 0.6 * rng.random() if ragged else 1))
        draw.line([(x, 0), (x, d)], fill=cream)
        draw.line([(x, h - d), (x, h)], fill=cream)
    for y in range(h):
        d = int(w * depth * 0.6 * (0.7 + 0.6 * rng.random() if ragged else 1))
        draw.line([(0, y), (d, y)], fill=cream)
        draw.line([(w - d, y), (w, y)], fill=cream)
    return framed


def comic_page(size=(1360, 752)):
    page = Image.new("RGB", size, (230, 222, 205))
    draw = ImageDraw.Draw(page)
    w, h = size
    for box in ((20, 20, w // 2 - 15, h - 20), (w // 2 + 15, 20, w - 20, h // 2 - 12),
                (w // 2 + 15, h // 2 + 12, w - 20, h - 20)):
        panel = dark_scene((box[2] - box[0], box[3] - box[1]), seed=box[0])
        page.paste(panel, box[:2])
    return page


def png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class BorderDetectionTests(unittest.TestCase):
    def test_paper_margins_are_detected_and_cropped(self):
        framed = with_paper_margins(dark_scene())
        report = image_checks.detect_border(framed)
        self.assertTrue(report["bordered"])
        self.assertEqual(sorted(report["margin_edges"]), ["bottom", "left", "right", "top"])
        cropped, box = image_checks.crop_border(framed)
        self.assertFalse(image_checks.detect_border(cropped)["bordered"])
        self.assertAlmostEqual(cropped.width / cropped.height, 16 / 9, places=2)
        self.assertGreater(box[0], 0)

    def test_full_bleed_and_bright_window_are_not_borders(self):
        self.assertFalse(image_checks.detect_border(dark_scene())["bordered"])
        window = dark_scene()
        ImageDraw.Draw(window).rectangle([1160, 150, 1360, 450], fill=(225, 230, 235))
        report = image_checks.detect_border(window)
        self.assertFalse(report["bordered"])
        self.assertLessEqual(len(report["margin_edges"]), 1)

    def test_uniform_light_strip_along_one_full_edge_is_a_border(self):
        strip = dark_scene()
        ImageDraw.Draw(strip).rectangle([0, 0, 1360, 30], fill=(235, 228, 210))
        report = image_checks.detect_border(strip)
        self.assertTrue(report["bordered"])
        self.assertEqual(report["full_edges"], ["top"])


class PanelDetectionTests(unittest.TestCase):
    def test_comic_page_gutters_are_detected(self):
        self.assertTrue(image_checks.detect_panels(comic_page())["panels"])

    def test_single_bright_frame_line_is_not_a_gutter(self):
        frame = dark_scene()
        ImageDraw.Draw(frame).line([(700, 120), (700, 600)], fill=(230, 230, 230), width=3)
        self.assertFalse(image_checks.detect_panels(frame)["panels"])
        self.assertFalse(image_checks.detect_panels(dark_scene())["panels"])

    @unittest.skipUnless((CALIBRATION / "engraving_1.jpg").exists(), "no calibration images")
    def test_real_calibration_images(self):
        expected = {0: True, 1: True, 2: False, 3: False, 5: False}      # bordered?
        for variant in ("engraving", "charcoal_gouache", "hybrid"):
            for index, bordered in expected.items():
                with self.subTest(variant=variant, index=index), \
                        Image.open(CALIBRATION / f"{variant}_{index}.jpg") as image:
                    self.assertEqual(image_checks.detect_border(image)["bordered"], bordered)
            with Image.open(CALIBRATION / f"{variant}_4.jpg") as insert:
                if variant != "hybrid":                                  # the comic pages
                    self.assertTrue(image_checks.detect_panels(insert)["panels"])


class SequenceComfy(FakeComfy):
    """Serves the given images in order, one per generation."""

    def __init__(self, images):
        super().__init__()
        self.sequence = [png(image) for image in images]
        self.served = 0

    def request(self, method, url, **kwargs):
        if url.endswith("/view"):
            data = self.sequence[min(self.served, len(self.sequence) - 1)]
            self.served += 1
            return Response(content=data)
        return super().request(method, url, **kwargs)


class GeneratorCheckTests(unittest.TestCase):
    def run_job(self, images, retries=2):
        fake = SequenceComfy(images)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(config, "IMAGEGEN_CHECK_RETRIES", retries), redirect_stdout(io.StringIO()):
            generator = ImageGenerator(temp, ComfyClient("http://127.0.0.1:8188", session=fake,
                                                         sleep=lambda _: None))
            job = ImageJob(job_id="s01_01", prompt="p", seed=100, output_path=str(Path(temp, "a.jpg")))
            record = generator.generate(job)
            again = generator.generate(job)
            with Image.open(record["local_path"]) as image:
                size = image.size
        return record, again, fake, size

    def test_bordered_image_is_regenerated_with_a_new_seed(self):
        record, again, fake, _ = self.run_job([with_paper_margins(dark_scene()), dark_scene()])
        seeds = [graph["7"]["inputs"]["seed"] for graph in fake.prompts]
        self.assertEqual(len(seeds), 2)
        self.assertNotEqual(seeds[0], seeds[1])
        self.assertEqual(record["image_checks"]["action"], "regenerated")
        self.assertEqual(record["seed"], seeds[1])
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["image_checks"]["action"], "regenerated")   # from the cache sidecar

    def test_persistent_margins_are_cropped_after_two_retries(self):
        framed = with_paper_margins(dark_scene())
        record, _, fake, size = self.run_job([framed, framed, framed])
        self.assertEqual(len(fake.prompts), 3)
        self.assertEqual(record["image_checks"]["action"], "cropped")
        self.assertIn("crop_box", record["image_checks"]["attempts"][-1])
        self.assertEqual(size, (1920, 1080))

    def test_comic_page_is_regenerated(self):
        record, _, fake, _ = self.run_job([comic_page(), dark_scene()])
        self.assertEqual(len(fake.prompts), 2)
        self.assertTrue(record["image_checks"]["attempts"][0]["panels"])

    def test_checks_can_be_disabled(self):
        with patch.object(config, "IMAGEGEN_IMAGE_CHECKS", False):
            record, _, fake, _ = self.run_job([with_paper_margins(dark_scene())])
        self.assertEqual(len(fake.prompts), 1)
        self.assertEqual(record["image_checks"], {})


class PromptRuleTests(unittest.TestCase):
    def test_full_bleed_in_every_variant_and_stronger_engraving(self):
        for variant in identity.STYLE_VARIANTS:
            self.assertIn("single full-bleed illustration filling the entire frame edge to edge",
                          identity.style_suffix("dark_illustration", variant))
        engraving = identity.style_suffix("dark_illustration", "engraving")
        for phrase in ("deep black negative space", "single small source of warm light",
                       "figures small within towering architecture", "heavy shadows swallowing"):
            self.assertIn(phrase, engraving)
        self.assertEqual(identity.STYLE_VERSION, 2)
        self.assertEqual(config.VISUAL_STYLE_VARIANT, "engraving")

    def test_gemini_rules_and_layout_check(self):
        rules = identity.image_prompt_rules("dark_illustration")
        self.assertIn("never request pages, panels, frames, borders, vignettes, paper edges or multiple views", rules)
        self.assertIn("clearly different age, build, posture and clothing", rules)
        self.assertEqual(identity.layout_terms("A comic page of three panels with a torn paper edge"),
                         ["panels", "comic page", "torn paper", "paper edge"])
        self.assertEqual(identity.layout_terms("A storyboard frame around the bottle"),
                         ["storyboard", "frame around"])
        self.assertEqual(identity.layout_terms("He waits at the edge of the table"), [])

    def test_shot_validation_rejects_layouts_and_too_few_wides(self):
        segments = [{"shot_id": f"s01_0{i}"} for i in range(1, 6)]
        shots = [{"shot_id": s["shot_id"], "shot_type": "wide" if i < 2 else "medium",
                  "characters": ["julian_vance"], "location_id": "loft", "action": "He waits",
                  "face_visibility": "partial", "reuse_of": ""} for i, s in enumerate(segments)]
        shots_module.validate_shots({"shots": shots}, segments, VISUAL_BIBLE, {}, "reference")
        shots[0]["action"] = "A storyboard of him waiting"
        shots[1]["shot_type"] = "medium"
        with self.assertRaises(Exception) as caught:
            shots_module.validate_shots({"shots": shots}, segments, VISUAL_BIBLE, {}, "reference")
        self.assertIn("page layout", str(caught.exception))
        self.assertIn("wide/establishing", str(caught.exception))

    def test_unnamed_people_get_the_crowd_hint(self):
        shot = {"shot_id": "s06_01", "scene_id": "s02", "shot_type": "wide", "characters": ["julian_vance"],
                "location": "loft", "action": "Julian toasts the guests at the long table",
                "face_visibility": "partial", "seed": 1, "seed_offset": 0}
        composed = shots_module.compose_shot(shot, VISUAL_BIBLE, "unused", assume_references=True)
        self.assertIn(shots_module.CROWD_HINT, composed["prompt"])
        alone = dict(shot, action="Julian waits alone")
        self.assertNotIn(shots_module.CROWD_HINT,
                         shots_module.compose_shot(alone, VISUAL_BIBLE, "unused")["prompt"])

    def test_gui_rejects_layout_requests(self):
        import gui
        from agents.narrative import atomic_write_json
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            atomic_write_json(Path(temp, "shot_list.json"), {"shots": [{"shot_id": "s01_01", "action": "x"}]})
            response = gui.app.test_client().post("/api/imagegen/shot/s01_01/prompt",
                                                  json={"action": "A comic page of his evening"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("comic page", response.get_json()["error"])


class CalibrateStyleTests(unittest.TestCase):
    def test_variant_seeds_and_subset_runs_merge_results(self):
        from scripts import calibrate_style
        fake = FakeComfy()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(calibrate_style, "ImageGenerator", lambda output: ImageGenerator(
                    output, ComfyClient("http://127.0.0.1:8188", session=fake, sleep=lambda _: None))), \
                patch.object(calibrate_style, "RESULT_FILE", Path(temp, "style.json")), \
                redirect_stdout(io.StringIO()):
            calibrate_style.main(["--output-dir", temp])
            seeds = [graph["7"]["inputs"]["seed"] for graph in fake.prompts]
            before = len(fake.prompts)
            subset = Path(temp, "subset")                     # fresh folder: no image cache hits
            calibrate_style.main(["--output-dir", str(subset), "--variants", "engraving"])
            self.assertTrue((subset / "style_calibration" / "contact_sheet.jpg").exists())
            data = json.loads(Path(temp, "style.json").read_text(encoding="utf-8"))
        self.assertEqual(seeds[0], calibrate_style.VARIANT_SEEDS["engraving"])
        self.assertEqual(seeds[6], calibrate_style.VARIANT_SEEDS["charcoal_gouache"])
        self.assertEqual(len(fake.prompts) - before, 6)
        self.assertEqual(set(data["variants"]), {"engraving", "charcoal_gouache", "hybrid"})
        self.assertEqual(data["variants"]["hybrid"]["seed_base"], 6464)
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            calibrate_style.main(["--variants", "watercolour"])


if __name__ == "__main__":
    unittest.main()
