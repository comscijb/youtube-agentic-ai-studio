"""Phase 3.5.3: crop → edit → composite region edits, keep clauses and verification (no ComfyUI)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from video.imagegen_comfyui import ImageJob
from video.qa import fixes, region, review

VB = json.loads(json.dumps(VISUAL_BIBLE))
SIZE = (1920, 1080)
JULIAN = [0.08, 0.15, 0.36, 1.0]       # large figure, left
ELENA = [0.64, 0.30, 0.72, 0.95]       # standing mid-frame right
SILHOUETTE = [0.76, 0.42, 0.79, 0.70]  # ~3 % wide, in the lit doorway


def scene() -> Image.Image:
    """Dark room, a lit doorway on the right, three figures."""
    image = Image.new("RGB", SIZE, (40, 38, 36))
    draw = ImageDraw.Draw(image)
    for x in range(0, SIZE[0], 24):                                    # some texture on the walls
        draw.line([(x, 0), (x, SIZE[1])], fill=(48, 45, 42), width=3)
    px = lambda box: [round(box[0] * SIZE[0]), round(box[1] * SIZE[1]), round(box[2] * SIZE[0]), round(box[3] * SIZE[1])]
    draw.rectangle(px([0.74, 0.35, 0.84, 0.80]), fill=(235, 215, 170))   # the doorway
    draw.rectangle(px(SILHOUETTE), fill=(15, 14, 14))
    draw.rectangle(px(JULIAN), fill=(70, 60, 120))
    draw.rectangle(px(ELENA), fill=(200, 190, 160))
    return image


def person(box):
    return {"box": box, "score": 0.9, "face": None}


class FakeKlein:
    """Stands in for ComfyUI: paints the first box with the doorway colour (the removal) and, like the
    model in s01_06, also changes things it should not — a grey band over the whole left half."""

    def __init__(self, paint=True, spill=True):
        self.paint, self.spill, self.jobs = paint, spill, []

    def generate(self, job):
        self.jobs.append(job)
        with Image.open(job.references[0]) as opened:
            image = opened.convert("RGB").resize(tuple(job.final_size))
        draw = ImageDraw.Draw(image)
        if self.spill:
            draw.rectangle([0, 0, image.width // 2, image.height], fill=(128, 128, 128))
        if self.paint and job.edit_boxes:
            box = job.edit_boxes[0][0]
            draw.rectangle([box[0] * image.width - 4, box[1] * image.height - 4, box[2] * image.width + 4,
                            box[3] * image.height + 4], fill=(235, 215, 170))
        image.save(job.output_path, "JPEG", quality=95)
        return {"seed": job.seed, "seconds": 0.1}


class FakeDetector:
    def __init__(self, *detections):
        self.detections = list(detections)

    def detect(self, path):
        return self.detections.pop(0) if len(self.detections) > 1 else self.detections[0]


ALL = {"persons": [person(JULIAN), person(ELENA), person(SILHOUETTE)]}
WITHOUT_SILHOUETTE = {"persons": [person(JULIAN), person(ELENA)]}


class CropGeometryTests(unittest.TestCase):
    def test_tiny_boxes_get_a_large_16x9_crop_and_ground_better(self):
        crop = region.crop_window(SILHOUETTE, SIZE)
        width, height = crop[2] - crop[0], crop[3] - crop[1]
        self.assertAlmostEqual(width / height, 16 / 9, delta=0.01)
        self.assertGreaterEqual(height, 0.25 * SIZE[1] - 1)
        self.assertTrue(crop[0] <= SILHOUETTE[0] * SIZE[0] and crop[2] >= SILHOUETTE[2] * SIZE[0])
        self.assertTrue(0 <= crop[0] and crop[2] <= SIZE[0] and 0 <= crop[1] and crop[3] <= SIZE[1])
        inside = region.to_crop(SILHOUETTE, crop, SIZE)
        self.assertGreater(inside[2] - inside[0], 2 * (SILHOUETTE[2] - SILHOUETTE[0]))   # > 2× more of the canvas
        self.assertLess(width, 0.5 * SIZE[0])
        self.assertTrue(inside[1] > 0.1 and inside[3] < 0.9)                            # room for the grown mask
        self.assertEqual(region.edit_size(crop), (1360, 768))

    def test_crop_is_clamped_at_the_frame_edge(self):
        crop = region.crop_window([0.97, 0.9, 0.995, 0.99], SIZE)
        self.assertEqual((crop[2], crop[3]), SIZE)

    def test_mask_is_the_box_grown_by_15_percent_and_feathered_inwards(self):
        crop = region.crop_window(SILHOUETTE, SIZE)
        soft, hard, ring = region.mask_for([SILHOUETTE], crop, SIZE)
        bw = (SILHOUETTE[2] - SILHOUETTE[0]) * SIZE[0]
        left, top, right, bottom = hard.getbbox()
        self.assertAlmostEqual(right - left, bw * 1.3, delta=3)
        self.assertEqual(soft.getbbox() and ImageChops.multiply(soft, ImageChops.invert(hard)).getbbox(), None)
        self.assertIsNone(ImageChops.multiply(ring, hard).getbbox())                # the ring is outside

    def test_tone_match_removes_a_global_shift(self):
        source = scene().crop(region.crop_window(SILHOUETTE, SIZE))
        shifted = Image.eval(source, lambda value: min(255, value + 40))
        _, _, ring = region.mask_for([SILHOUETTE], region.crop_window(SILHOUETTE, SIZE), SIZE)
        matched = region.tone_match(shifted, source, ring)
        before = ImageStat.Stat(ImageChops.difference(shifted, source).convert("L"), mask=ring).mean[0]
        after = ImageStat.Stat(ImageChops.difference(matched, source).convert("L"), mask=ring).mean[0]
        self.assertGreater(before, 30)
        self.assertLess(after, 6)


class CompositeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.image = self.output / "images" / "shots" / "s01_06.jpg"
        self.image.parent.mkdir(parents=True)
        scene().save(self.image, "JPEG", quality=95)
        self.shot = {"shot_id": "s01_06", "scene_id": "s01", "characters": ["julian_vance", "elena_vance"],
                     "image_path": "images/shots/s01_06.jpg", "qa": {"status": "pass", "issues": []}}

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, request, klein, detector):
        plan = review.plan_for(request, VB, self.output)
        base_job = ImageJob(job_id="s01_06", prompt="p", seed=10, output_path=str(self.image))
        with redirect_stdout(io.StringIO()):
            return fixes.edit_with_plan(klein, self.output, self.shot, plan, image_path=self.image,
                                        base_job=base_job, origin="human", vb=VB, detector=detector,
                                        progress=lambda _: None)

    def test_removing_the_small_figure_changes_only_its_masked_area(self):
        before = Image.open(self.image).convert("RGB")
        klein = FakeKlein()
        action = self.edit(review.make_request("remove_figure", VB, box=SILHOUETTE), klein,
                           FakeDetector(ALL, WITHOUT_SILHOUETTE))
        self.assertEqual(action["action"], "edit")
        job = klein.jobs[0]
        self.assertEqual(Path(job.references[0]).name, "s01_06_v1_crop_before.jpg")    # the crop, not the frame
        self.assertGreater(job.edit_boxes[0][0][2] - job.edit_boxes[0][0][0], 0.06)  # grounded on the crop
        self.assertTrue((self.output / "images/shots/history/s01_06_v1_crop_after.jpg").is_file())
        after = Image.open(self.image).convert("RGB")
        px = lambda box: (round(box[0] * SIZE[0]), round(box[1] * SIZE[1]), round(box[2] * SIZE[0]), round(box[3] * SIZE[1]))
        # Elena and Julian were painted over by the model's spill: outside the mask they are untouched
        for figure in (ELENA, JULIAN):
            diff = ImageStat.Stat(ImageChops.difference(before.crop(px(figure)), after.crop(px(figure)))).mean
            self.assertLess(max(diff), 2.0)                                            # JPEG re-encode only
        removed = ImageStat.Stat(after.crop(px(SILHOUETTE))).mean
        self.assertGreater(removed[0], 150)                                           # the silhouette is gone
        self.assertEqual(action["verify"]["people_inside"], [1, 0])
        self.assertNotIn("warning", action["verify"])
        self.assertIn("Remove the small dark silhouette standing far back against the bright background on the right "
                      "in <obj_1>", action["instruction"])
        # only the characters visible in the crop: Elena is, Julian (far left) is not
        self.assertIn("Keep the woman in the ivory silk shirt exactly as they are.", action["instruction"])
        self.assertNotIn("charcoal tailored suit", action["instruction"])
        self.assertIn("Change nothing outside <obj_1>.", action["instruction"])

    def test_composite_is_byte_identical_outside_the_mask(self):
        source = scene()
        crop = region.crop_window(SILHOUETTE, SIZE)
        soft, hard, ring = region.mask_for([SILHOUETTE], crop, SIZE)
        edited = Image.new("RGB", region.edit_size(crop), (255, 0, 0))                 # changes everything
        result = region.composite(source, edited, crop, soft, ring)
        self.assertGreater(region.check(source, result, crop, hard), region.MIN_CHANGE)
        outside_elena = tuple(round(v * s) for v, s in zip(ELENA, SIZE * 2))
        self.assertIsNone(ImageChops.difference(source.crop(outside_elena), result.crop(outside_elena)).getbbox())
        broken = result.copy()
        broken.putpixel((5, 5), (0, 255, 0))
        with self.assertRaises(AssertionError):
            region.check(source, broken, crop, hard)

    def test_an_edit_that_changes_nothing_inside_is_reported_not_saved(self):
        original = self.image.read_bytes()
        action = self.edit(review.make_request("remove_figure", VB, box=SILHOUETTE), FakeKlein(paint=False, spill=False),
                           FakeDetector(ALL))
        self.assertEqual(action["action"], "no_effect")
        self.assertEqual(self.image.read_bytes(), original)
        self.assertEqual(self.shot["qa"]["history"], [])
        self.assertNotIn("human_edits", self.shot["qa"])

    def test_a_removal_the_detector_still_sees_is_flagged(self):
        action = self.edit(review.make_request("remove_figure", VB, box=SILHOUETTE), FakeKlein(), FakeDetector(ALL, ALL))
        self.assertEqual(action["verify"]["warning"], "a figura continua dentro da caixa (detector)")

    def test_instruction_only_edits_keep_the_whole_frame_path_with_keep_clauses(self):
        klein = FakeKlein(paint=False, spill=False)
        action = self.edit(review.make_request("remove_marks", VB), klein, False)
        job = klein.jobs[0]
        self.assertIsNone(action["crop"])
        self.assertEqual(Path(job.references[0]).name, "s01_06_v1.jpg")
        self.assertIn("otherwise unchanged (faces, clothes, poses)", job.prompt)

    def test_marker_target_is_not_in_its_own_keep_clause(self):
        klein = FakeKlein()
        self.edit(review.make_request("add_marker", VB, character="julian_vance", marker="horn-rimmed glasses",
                                      box=[0.18, 0.18, 0.26, 0.32]), klein, False)
        self.assertIn("the woman in the ivory silk shirt", klein.jobs[0].prompt)
        self.assertNotIn("charcoal tailored suit", klein.jobs[0].prompt)


class OverlapTests(unittest.TestCase):
    def test_a_box_over_a_main_character_warns(self):
        shot = {"characters": ["julian_vance", "elena_vance"]}
        on_elena = [0.62, 0.30, 0.70, 0.80]
        warnings = region.overlap_warnings(ALL, shot, on_elena)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["box"], ELENA)
        self.assertGreater(warnings[0]["covered"], 0.2)
        self.assertEqual(region.overlap_warnings(ALL, shot, SILHOUETTE), [])
        self.assertEqual(region.overlap_warnings(ALL, {"characters": ["elena_vance"]}, on_elena), [])   # Julian is larger


class CheckBoxEndpointTests(unittest.TestCase):
    def test_gui_warns_before_queuing_a_box_over_elena(self):
        import config
        import gui
        from unittest.mock import patch
        from agents.narrative import atomic_write_json
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "images" / "shots").mkdir(parents=True)
            scene().save(output / "images" / "shots" / "s01_06.jpg")
            atomic_write_json(output / "shot_list.json", {"shots": [
                {"shot_id": "s01_06", "scene_id": "s01", "characters": ["elena_vance"],
                 "image_path": "images/shots/s01_06.jpg"}]})
            client = gui.app.test_client()
            with patch.object(config, "OUTPUT_DIR", str(output)),                     patch.object(fixes, "_detector", return_value=FakeDetector({"persons": [person(ELENA)]})):
                hit = client.post("/api/imagegen/shot/s01_06/check_box", json={"box": [0.62, 0.3, 0.7, 0.8]}).get_json()
                clear = client.post("/api/imagegen/shot/s01_06/check_box", json={"box": SILHOUETTE}).get_json()
                bad = client.post("/api/imagegen/shot/s01_06/check_box", json={"box": [0.5, 0.5, 0.1, 0.1]})
            with patch.object(config, "OUTPUT_DIR", str(output)), patch.object(fixes, "_detector", return_value=None):
                missing = client.post("/api/imagegen/shot/s01_06/check_box", json={"box": SILHOUETTE}).get_json()
        self.assertEqual(hit["warnings"][0]["message"], "a caixa cobre 58% de elena_vance")
        self.assertEqual(clear["warnings"], [])
        self.assertEqual(bad.status_code, 400)
        self.assertEqual((missing["warnings"], missing["detector"]), ([], False))


if __name__ == "__main__":
    unittest.main()
