"""Phase 3.9.3: copies of style boards, paper margins, exterior locations, crimson hints (no live ComfyUI)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from agents import identity, style_refs
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from scripts import fix_borders
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator, ImageJob
from video.qa import borders, copy_check, deterministic

F = Path(__file__).parent / "fixtures" / "copy_check"


def framed(image: Image.Image, margin: int = 8) -> Image.Image:
    """A picture on an ivory sheet: the shape the model draws."""
    copy = image.copy()
    width, height = copy.size
    paper = (232, 226, 210)
    for box in ((0, 0, width, margin), (0, height - margin, width, height), (0, 0, margin, height),
                (width - margin, 0, width, height)):
        copy.paste(paper, box)
    return copy


def rgb(name: str) -> Image.Image:
    with Image.open(F / name) as image:
        return image.convert("RGB")


def generator_in(temp) -> ImageGenerator:
    return ImageGenerator(temp, ComfyClient("http://127.0.0.1:8188", session=FakeComfy(), sleep=lambda _: None))


class CopyDetectorTests(unittest.TestCase):
    def test_thresholds_on_real_pairs(self):
        board = F / "board_6.jpg"
        for name in ("bakery_copy.jpg", "bakery_copy2.jpg", "moor_pasted.jpg"):
            with self.subTest(name=name):
                self.assertTrue(copy_check.is_copy(copy_check.compare(F / name, board)))
        self.assertFalse(copy_check.is_copy(copy_check.compare(F / "ep2_shot.jpg", board)))

    def test_blank_images_never_copy(self):
        flat = Image.new("RGB", (640, 360), (20, 20, 20))
        self.assertFalse(copy_check.is_copy(copy_check.compare(flat, flat.copy())))

    def test_only_boards_flag_other_references_are_reported(self):
        image = rgb("bakery_copy.jpg")
        found = copy_check.check(image, [str(F / "bakery_copy2.jpg")], anchors=[str(F / "board_6.jpg")])
        alone = copy_check.check(image, [str(F / "bakery_copy2.jpg")], anchors=[])
        self.assertTrue(found["flagged"])
        self.assertEqual(found["of"], "board_6.jpg")
        self.assertEqual(found["reference"]["of"], "bakery_copy2.jpg")
        self.assertFalse(alone["flagged"])

    def test_style_sentence_is_removed(self):
        self.assertEqual(copy_check.without_style_sentence("A man. Rendered in the drawing style of the second image. Dark."),
                         "A man. Dark.")


class RegenerationTests(unittest.TestCase):
    def test_a_copy_is_regenerated_once_without_the_anchor_then_labelled(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = generator_in(temp)
            job = ImageJob(job_id="s1", prompt="A man. Rendered in the drawing style of the first image. Dark.", seed=5,
                           output_path=str(Path(temp, "s1.jpg")), kind="ref", references=[str(F / "board_6.jpg")])
            image = Image.new("RGB", (64, 36), (10, 10, 10))
            jobs = []

            def render(bare, seed, uploaded):
                jobs.append((bare, uploaded))
                return image, {}, None
            flagged = {"flagged": True, "matches": 600, "edge": .9, "of": "board_6.png"}
            with patch.object(copy_check, "check", side_effect=lambda *a, **k: dict(flagged)), \
                    patch.object(copy_check, "is_anchor", return_value=True), \
                    patch.object(generator, "_render_framed", side_effect=render):
                _, _, _, checks = generator._quality_pass(job, image, 5, {}, {}, ["board_6.png"])
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0][0].references, [])
            self.assertEqual(jobs[0][0].kind, "t2i")
            self.assertNotIn("drawing style", jobs[0][0].prompt)
            self.assertEqual(jobs[0][1], [])
            self.assertTrue(checks["copy_regenerated"])
            self.assertEqual(checks["copy_check"]["label"], copy_check.LABEL)
            self.assertEqual(gen.image_labels(checks), [copy_check.LABEL])

    def test_second_try_clean_has_no_label(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = generator_in(temp)
            job = ImageJob(job_id="s1", prompt="A man.", seed=5, output_path=str(Path(temp, "s1.jpg")))
            image = Image.new("RGB", (64, 36), (10, 10, 10))
            answers = [{"flagged": True, "matches": 600, "edge": .9, "of": "b"},
                       {"flagged": False, "matches": 2, "edge": .1, "of": "b"}]
            with patch.object(copy_check, "check", side_effect=answers), \
                    patch.object(generator, "_render_framed", return_value=(image, {}, None)):
                _, _, _, checks = generator._quality_pass(job, image, 5, {}, {}, [])
            self.assertEqual(gen.image_labels(checks), [])
            self.assertEqual(checks["copy_check"]["matches"], 2)

    def test_edits_are_not_compared(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = generator_in(temp)
            job = ImageJob(job_id="s1", prompt="x", seed=5, output_path=str(Path(temp, "s1.jpg")), kind="region_edit",
                           references=[str(F / "board_6.jpg")])
            image = Image.new("RGB", (64, 36), (10, 10, 10))
            with patch.object(copy_check, "check", side_effect=AssertionError("no compare")):
                generator._quality_pass(job, image, 5, {}, {}, [])

    def test_a_reference_with_a_margin_is_cropped_before_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = generator_in(temp)
            path = Path(temp, "the_moor.png")
            rgb("margin_shot.jpg").save(path)
            cleaned = generator._clean_reference(path)
            self.assertNotEqual(cleaned, str(path))
            with Image.open(cleaned) as image:
                self.assertFalse(borders.detect(image.convert("RGB"))["margin"])
            self.assertEqual(generator._clean_reference(F / "no_margin_shot.jpg"), str(F / "no_margin_shot.jpg"))


class HotfixTests(unittest.TestCase):
    def test_location_job_is_text_to_image(self):
        job = gen._location_job(VISUAL_BIBLE, VISUAL_BIBLE["locations"][0]["location_id"], 1, Path("."))
        self.assertEqual(job.references, [])
        self.assertEqual(job.kind, "t2i")

    def test_style_slot_only_follows_a_content_slot(self):
        with patch.object(style_refs, "empty_paths", return_value=["board.png"]), \
                patch.object(style_refs, "approved_paths", return_value=["board.png"]):
            alone = shots_module.compose_shot({"shot_type": "insert", "action": "a key", "characters": [],
                                               "others_present": False}, VISUAL_BIBLE, ".")
            with_person = shots_module.compose_shot({"shot_type": "medium", "action": "he waits", "others_present": False,
                                                     "characters": [VISUAL_BIBLE["characters"][0]["character_id"]]},
                                                    VISUAL_BIBLE, ".", assume_references=True)
        self.assertEqual(alone["references"], [])
        self.assertEqual(alone["kind"], "t2i")
        self.assertEqual(with_person["slots"][-1]["kind"], "style")
        self.assertGreater(len(with_person["slots"]), 1)


class BorderDetectorTests(unittest.TestCase):
    def test_real_margin_and_real_clean_shot(self):
        report = borders.detect(rgb("margin_shot.jpg"))
        self.assertTrue(report["margin"])
        self.assertEqual(sorted(report["marked"]), ["bottom", "left", "right", "top"])
        self.assertEqual(borders.verdict(report), "crop")
        self.assertEqual(borders.verdict(borders.detect(rgb("no_margin_shot.jpg"))), "clean")

    def test_crop_keeps_size_and_removes_it(self):
        cleaned, removed = borders.crop(rgb("margin_shot.jpg"))
        self.assertEqual(cleaned.size, (960, 540))
        self.assertFalse(borders.detect(cleaned)["margin"])
        self.assertGreater(removed["left"], 0)
        self.assertAlmostEqual((960 - removed["left"] - removed["right"]) / (540 - removed["top"] - removed["bottom"]),
                               16 / 9, places=1)

    def test_bright_window_or_fog_on_one_side_is_not_a_margin(self):
        scene = np.full((540, 960, 3), 30, dtype=np.uint8)
        scene[100:300, 0:60] = 240                         # a bright window touching the left edge
        scene[200:500, 900:] = np.linspace(40, 230, 60).astype(np.uint8)[None, :, None]     # a shaft of fog
        self.assertFalse(borders.detect(Image.fromarray(scene))["margin"])

    def test_a_deep_margin_is_regenerated_not_cropped(self):
        deep = framed(rgb("no_margin_shot.jpg"), 60)
        self.assertEqual(borders.verdict(borders.detect(deep)), "regenerate")

    def test_frame_line_counts(self):
        scene = np.full((540, 960, 3), 30, dtype=np.uint8)
        scene[10, :] = scene[529, :] = 235
        scene[:, 10] = scene[:, 949] = 235
        self.assertTrue(borders.detect(Image.fromarray(scene))["margin"])

    def test_generator_crops_a_thin_margin_and_records_it(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = generator_in(temp)
            job = ImageJob(job_id="s1", prompt="x", seed=5, output_path=str(Path(temp, "s1.jpg")))
            image, _, _, checks = generator._quality_pass(job, rgb("margin_shot.jpg"), 5, {}, {}, [])
        self.assertIn("border_cropped", checks)
        self.assertFalse(borders.detect(image)["margin"])

    def test_generator_regenerates_a_deep_margin_once_then_labels(self):
        deep = framed(rgb("no_margin_shot.jpg"), 60)
        with tempfile.TemporaryDirectory() as temp:
            generator = generator_in(temp)
            job = ImageJob(job_id="s1", prompt="x", seed=5, output_path=str(Path(temp, "s1.jpg")))
            with patch.object(generator, "_render_framed", return_value=(deep, {}, None)) as render:
                _, _, _, checks = generator._quality_pass(job, deep, 5, {}, {}, [])
        self.assertEqual(render.call_count, 1)
        self.assertEqual(checks["border"]["label"], "moldura")
        self.assertEqual(gen.image_labels(checks), ["moldura"])


class WordingTests(unittest.TestCase):
    def test_hash_is_unchanged_and_paper_is_gone(self):
        self.assertEqual(identity.style_hash(), "d043a627229faba8")
        self.assertNotIn("paper grain", identity.style_suffix())
        self.assertIn("fine grain and ink texture", identity.style_suffix())

    def test_full_bleed_first(self):
        prompt = identity.lead_with_full_bleed(f"An illustration of a man. {identity.style_suffix()}")
        self.assertTrue(prompt.startswith(identity.FULL_BLEED))
        self.assertEqual(prompt.count(identity.FULL_BLEED), 1)
        self.assertIn("An illustration of a man.", prompt)
        self.assertEqual(identity.lead_with_full_bleed("no clause"), "no clause")

    def test_the_generator_sends_it_first(self):
        with tempfile.TemporaryDirectory() as temp:
            job = ImageJob(job_id="s1", prompt=f"A man. {identity.style_suffix()}", seed=5,
                           output_path=str(Path(temp, "s1.jpg")))
            record = generator_in(temp).generate(job)
        self.assertTrue(record["prompt"].startswith(identity.FULL_BLEED))


class FixCommandTests(unittest.TestCase):
    def project(self, temp):
        folder = Path(temp)
        (folder / "images" / "shots").mkdir(parents=True)
        (folder / "location_refs").mkdir()
        (folder / "character_refs").mkdir()
        margin, clean = rgb("margin_shot.jpg"), rgb("no_margin_shot.jpg")
        margin.save(folder / "images/shots/s06_02.jpg", quality=93)
        margin.save(folder / "location_refs/the_moor.png")
        clean.save(folder / "images/shots/s01_01.jpg", quality=93)
        framed(clean, 60).save(folder / "images/shots/s07_01.jpg", quality=93)
        atomic_write_json(folder / "shot_list.json", {"shots": [{"image_path": f"images\\shots\\{name}.jpg"}
                                                               for name in ("s06_02", "s01_01", "s07_01")]})
        atomic_write_json(folder / "asset_manifest.json", {"generated_assets": [{"job_id": "s06_02"}]})
        atomic_write_json(folder / "visual_bible.json", {"locations": [{"location_id": "the_moor", "descriptor": "open moor"},
                                                                       {"location_id": "kitchen", "descriptor": "a kitchen"}]})
        (folder / "final_video.mp4").write_bytes(b"v")
        return folder

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = self.project(temp)
            before = (folder / "images/shots/s06_02.jpg").read_bytes()
            report = fix_borders.fix_project(folder, dry_run=True)
            self.assertEqual((folder / "images/shots/s06_02.jpg").read_bytes(), before)
            self.assertFalse((folder / "images/shots/history").exists())
            self.assertEqual(sorted(row["image"].replace("\\", "/") for row in report["cropped"]),
                             ["images/shots/s06_02.jpg", "location_refs/the_moor.png"])
            self.assertFalse(report["video_stale"])

    def test_repair_keeps_originals_and_updates_the_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = self.project(temp)
            report = fix_borders.fix_project(folder)
            self.assertEqual(len(list((folder / "images/shots/history").glob("s06_02.border-*"))), 1)
            self.assertEqual(len(list((folder / "location_refs/history").glob("the_moor.border-*"))), 1)
            with Image.open(folder / "images/shots/s06_02.jpg") as image:
                self.assertFalse(borders.detect(image.convert("RGB"))["margin"])
            self.assertIn("border_cropped", load_json(folder / "asset_manifest.json")["generated_assets"][0])
            self.assertEqual([row["image"].replace("\\", "/") for row in report["regenerate"]], ["images/shots/s07_01.jpg"])
            self.assertTrue(report["video_stale"])
            flags = {item["location_id"]: item["interior"] for item in load_json(folder / "visual_bible.json")["locations"]}
            self.assertEqual(flags, {"the_moor": False, "kitchen": True})


class ExteriorTests(unittest.TestCase):
    def test_exterior_text_has_no_rooms_and_interior_keeps_them(self):
        for figures in (True, False):
            with self.subTest(figures=figures):
                outside = identity.style_suffix(figures=figures, exterior=True)
                inside = identity.style_suffix(figures=figures)
                for word in ("doorways", "staircases", "rooms", "room", "corridors"):
                    self.assertNotIn(word, outside)
                self.assertIn("doorways", inside)
                self.assertIn("staircases", inside)
                self.assertIn("vast sky", outside)
        self.assertEqual(identity.style_hash(), "d043a627229faba8")

    def test_flag_by_keyword(self):
        cases = {"The Open Moorland": False, "a kitchen": True, "the stone lane": False, "a cemetery": False,
                 "an old bakery": True, "somewhere": True}
        for descriptor, interior in cases.items():
            with self.subTest(descriptor=descriptor):
                self.assertIs(vbible.location_interior({"location_id": "x", "descriptor": descriptor}), interior)
        self.assertIs(vbible.location_interior({"location_id": "x", "descriptor": "somewhere", "interior": False}), False)
        self.assertIs(vbible.location_interior({"location_id": "x", "descriptor": "a moor", "interior": True}), False)
        vb = vbible.backfill_interior({"locations": [{"location_id": "the_moor", "descriptor": "vast heather"}]})
        self.assertIs(vb["locations"][0]["interior"], False)

    def bible(self, descriptor):
        return dict(VISUAL_BIBLE, locations=[{"location_id": "here", "name": "Here", "descriptor": descriptor, "scene_ids": []}])

    def test_location_prompts(self):
        outside = gen.location_prompt(self.bible("the open moor under a cold sky"), "here")
        self.assertNotIn("only architecture", outside)
        self.assertNotIn("doorways", outside)
        inside = gen.location_prompt(self.bible("a cottage kitchen"), "here")
        self.assertIn("only architecture, furniture and objects", inside)
        self.assertIn("doorways", inside)

    def test_shots_on_a_moor_never_ask_for_architecture(self):
        cid = VISUAL_BIBLE["characters"][0]["character_id"]
        shot = {"shot_type": "wide", "action": "he waits", "characters": [cid], "location": "here", "others_present": False}
        outside = shots_module.compose_shot(shot, self.bible("the open moor"), ".", assume_references=True)["prompt"]
        inside = shots_module.compose_shot(shot, self.bible("a cottage kitchen"), ".", assume_references=True)["prompt"]
        self.assertNotIn("towering architecture", outside)
        self.assertNotIn("doorways", outside)
        self.assertIn("towering architecture", inside)


class RedPatchTests(unittest.TestCase):
    def test_real_shoulder_patch_gets_a_hint(self):
        issues = deterministic.red_patch_issues(rgb("red_patch_shot.jpg"), [{"box": [0.33, 0.1, 0.62, 1.0]}])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "advisory")
        self.assertTrue(issues[0]["hint"])
        self.assertIn("parece sangue", issues[0]["note"])

    def test_small_box_or_no_red_is_quiet(self):
        scene = np.full((540, 960, 3), 40, dtype=np.uint8)
        self.assertEqual(deterministic.red_patch_issues(Image.fromarray(scene), [{"box": [.1, .1, .6, .9]}]), [])
        scene[100:135, 100:135] = (150, 30, 40)
        self.assertEqual(deterministic.red_patch_issues(Image.fromarray(scene), [{"box": [.1, .18, .13, .23]}]), [])
        self.assertTrue(deterministic.red_patch_issues(Image.fromarray(scene), [{"box": [.08, .15, .4, .6]}]))


if __name__ == "__main__":
    unittest.main()
