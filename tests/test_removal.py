"""Phase 3.9.4-B: real removal by mask inpainting (ComfyUI mocked)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import OBJECT_INFO
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import imagegen_templates as templates
from video.imagegen_comfyui import GenerationFailed
from video.qa import region, removal, review

VB = {"characters": [], "references": {}, "locations": [
    {"location_id": "store", "name": "The Store", "scene_ids": ["s02"],
     "descriptor": "A dim stone storehouse with polished floorboards, a dark doorway, a baker in the doorway, and flour sacks."}]}
SHOT = {"shot_id": "s02_01", "scene_id": "s02", "location": "store", "image_path": "images/shots/s02_01.jpg", "seed": 5,
        "setting_condition": "A crowd of customers waits. Cold morning light on the boards.", "characters": []}
BOX = [0.6, 0.3, 0.7, 0.7]


def scene(size=(1920, 1080)) -> Image.Image:
    rng = np.random.default_rng(1)
    image = Image.fromarray(rng.integers(30, 90, (size[1], size[0], 3), dtype=np.uint8))
    ImageDraw.Draw(image).rectangle((1190, 340, 1320, 740), fill=(10, 10, 10))
    return image


class FakeDetector:
    """People inside the mask for the first ``dirty`` checks after the source."""

    def __init__(self, dirty=0, persons=None):
        self.dirty, self.calls = dirty, 0
        self.persons = persons if persons is not None else [{"box": [0.62, 0.32, 0.69, 0.68]}]

    def detect(self, path):
        self.calls += 1
        if self.calls == 1 or self.calls - 1 <= self.dirty:
            return {"persons": self.persons}
        return {"persons": []}


class FakeGenerator:
    """Klein: paints the crop a flat colour; fails on request."""

    def __init__(self, fail=False):
        self.jobs, self.fail = [], fail
        self.client = type("Client", (), {"object_info": staticmethod(lambda: OBJECT_INFO)})()

    def generate(self, job):
        self.jobs.append(job)
        if self.fail:
            raise GenerationFailed("no")
        Image.new("RGB", job.size, (120, 110, 100)).save(job.output_path, quality=95)
        return {"seed": job.seed}


def project(temp) -> Path:
    folder = Path(temp)
    (folder / "images/shots").mkdir(parents=True)
    scene().save(folder / SHOT["image_path"], quality=95)
    return folder


class MaskTests(unittest.TestCase):
    def test_a_box_follows_the_person_is_dilated_and_reaches_the_shadow(self):
        person = [0.62, 0.32, 0.69, 0.68]
        hard = removal.build_mask((1000, 1000), BOX, persons=[{"box": person}])
        left, top, right, bottom = hard.getbbox()
        self.assertAlmostEqual(left, (0.62 - 0.07 * .1) * 1000, delta=1.5)
        self.assertAlmostEqual(right, (0.69 + 0.07 * .1) * 1000, delta=1.5)
        self.assertAlmostEqual(top, (0.32 - 0.36 * .1) * 1000, delta=1.5)
        self.assertAlmostEqual(bottom, (0.68 + 0.36 * (.1 + removal.SHADOW)) * 1000, delta=1.5)

    def test_a_person_the_box_barely_touches_is_ignored(self):
        self.assertEqual(removal.refine_box(BOX, [{"box": [0.0, 0.0, 0.62, 0.9]}]), (BOX, False))
        self.assertTrue(removal.refine_box(BOX, [{"box": [0.61, 0.31, 0.69, 0.69]}])[1])

    def test_a_painted_mask_is_kept_and_slightly_grown(self):
        painted = Image.new("L", (100, 100), 0)
        painted.paste(255, (40, 40, 60, 60))
        hard = removal.build_mask((1000, 1000), None, painted=painted)
        left, top, right, bottom = hard.getbbox()
        self.assertLess(left, 400)
        self.assertGreater(right, 600)
        self.assertLess(bottom, 650)
        with self.assertRaises(ValueError):
            removal.build_mask((100, 100), None, painted=Image.new("L", (10, 10), 0))
        with self.assertRaises(ValueError):
            removal.build_mask((100, 100), None)

    def test_feather_6_to_12_px_and_nothing_outside(self):
        hard = removal.build_mask((1000, 1000), BOX)
        self.assertTrue(6 <= removal.feather_radius(hard) <= 12)
        small = Image.new("L", (1000, 1000), 0)
        small.paste(255, (10, 10, 40, 40))
        self.assertEqual(removal.feather_radius(small), 6)
        soft = np.asarray(removal.feather(hard))
        outside = np.asarray(hard) == 0
        self.assertEqual(int(soft[outside].max()), 0)
        left, top, right, bottom = hard.getbbox()
        self.assertEqual(int(soft[(top + bottom) // 2, (left + right) // 2]), 255)
        self.assertLess(int(soft[(top + bottom) // 2, left + 1]), 255)


class PromptTests(unittest.TestCase):
    def test_the_restore_prompt_never_names_a_person(self):
        prompt = removal.restore_prompt(VB, SHOT)
        self.assertEqual(removal.PERSON_WORDS.findall(prompt), [])
        self.assertIn("polished floorboards", prompt)
        self.assertIn("flour sacks", prompt)
        self.assertIn("Cold morning light", prompt)
        self.assertIn("ink hatching", prompt)


class WorkflowTests(unittest.TestCase):
    def info(self):
        return OBJECT_INFO

    def test_restore_graph_is_valid_and_masks_the_latent(self):
        graph = templates.build_masked_restore(prompt="floorboards", seed=3, image="a.png", mask="m.png", denoise=.45,
                                               references=["loc.png"], width=1024, height=576)
        self.assertEqual(templates.validate_graph(graph, self.info()), [])
        self.assertEqual(graph["7"]["inputs"]["latent_image"], ["64", 0])
        self.assertEqual(graph["7"]["inputs"]["denoise"], .45)
        self.assertEqual(graph["64"]["class_type"], "SetLatentNoiseMask")
        self.assertEqual(graph["63"]["inputs"], {"image": "m.png", "channel": "red"})
        self.assertNotIn("44", graph)
        bare = templates.build_masked_restore(prompt="x", seed=3, image="a.png", mask="m.png", denoise=.5)
        self.assertEqual(templates.validate_graph(bare, self.info()), [])

    def test_lama_graph_and_availability(self):
        info = {**self.info(), "INPAINT_LoadInpaintModel": {"input": {"required": {"model_name": [["big-lama.pt"]]}}},
                "INPAINT_InpaintWithModel": {"input": {"required": {"inpaint_model": ["X"], "image": ["IMAGE"], "mask": ["MASK"],
                                                                    "seed": ["INT", {}]}}}}
        self.assertEqual(removal.eraser_available(info), "lama_comfy")
        graph = removal.lama_graph("a.png", "m.png", 3, info, "p")
        self.assertEqual(templates.validate_graph(graph, info), [])
        self.assertEqual(graph["1"]["inputs"]["model_name"], "big-lama.pt")
        combo = {**info, "INPAINT_LoadInpaintModel": {"input": {"required": {"model_name": [
            "COMBO", {"multiselect": False, "options": ["big-lama.pt"]}]}}}}             # as this ComfyUI answers
        self.assertEqual(removal.eraser_available(combo), "lama_comfy")
        self.assertEqual(removal.lama_graph("a.png", "m.png", 3, combo, "p")["1"]["inputs"]["model_name"], "big-lama.pt")
        self.assertEqual(removal.eraser_available(OBJECT_INFO), "opencv")
        no_weights = dict(info, INPAINT_LoadInpaintModel={"input": {"required": {"model_name": [[]]}}})
        self.assertEqual(removal.eraser_available(no_weights), "opencv")


class PipelineTests(unittest.TestCase):
    def run_removal(self, temp, generator, detector):
        folder = project(temp)
        shot = json.loads(json.dumps(SHOT))
        action = removal.run(generator, folder, shot, VB, {"box": BOX}, detector=detector, engine="opencv", progress=lambda _: None)
        return folder, shot, action

    def test_outside_is_identical_and_three_results_are_stored_without_touching_the_shot(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = FakeGenerator()
            folder, shot, action = self.run_removal(temp, generator, FakeDetector())
            before = (folder / SHOT["image_path"]).read_bytes()
            candidates = shot["removal"]["candidates"]
            self.assertEqual(len(candidates), removal.MAX_RESULTS)
            self.assertEqual((action["results"], action["clean"]), (3, 3))
            self.assertEqual(candidates[0]["people_inside"], [1, 0])
            self.assertEqual((folder / SHOT["image_path"]).read_bytes(), before)        # nothing replaced yet
            source = scene()
            for job in generator.jobs:
                self.assertEqual(job.kind, "masked_restore")
                self.assertNotRegex(job.prompt, removal.PERSON_WORDS)
            hard = removal.build_mask(source.size, BOX, persons=[{"box": [0.62, 0.32, 0.69, 0.68]}])
            crop = region.crop_window(removal.mask_box(hard), source.size)
            hard_crop = hard.crop(crop)
            result = region.composite(source, Image.new("RGB", (64, 36), (120, 110, 100)), crop,
                                      removal.feather(hard_crop), removal.tone_ring(hard_crop))
            outside = np.asarray(hard) == 0
            self.assertTrue(np.array_equal(np.asarray(result)[outside], np.asarray(source)[outside]))

    def test_escalation_order(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = FakeGenerator()
            _, shot, action = self.run_removal(temp, generator, FakeDetector(dirty=99))
        self.assertEqual([round(job.denoise, 2) for job in generator.jobs], [.75, .75, .85])      # the OpenCV stand-in
        self.assertEqual([item["name"] for item in removal.PROFILES], ["base", "máscara maior", "denoise 0,55", "só apagar"])
        kept = shot["removal"]["candidates"]
        self.assertLessEqual(len(kept), removal.MAX_RESULTS)
        self.assertTrue(any(item["profile"] == "só apagar" for item in kept))     # the free fallback
        self.assertEqual(action["clean"], 0)

    def test_lama_runs_the_profiles_as_written(self):
        with tempfile.TemporaryDirectory() as temp, patch("video.qa.removal.erase_onnx", side_effect=lambda image, mask: image):
            folder = project(temp)
            generator = FakeGenerator()
            removal.run(generator, folder, json.loads(json.dumps(SHOT)), VB, {"box": BOX}, detector=FakeDetector(dirty=99),
                        engine="lama_onnx", progress=lambda _: None)
        self.assertEqual([job.denoise for job in generator.jobs], [.45, .45, .55])

    def test_a_clean_removal_keeps_the_base_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = FakeGenerator()
            self.run_removal(temp, generator, FakeDetector())
        self.assertEqual([round(job.denoise, 2) for job in generator.jobs], [.75, .75, .75])
        self.assertEqual(len({job.seed for job in generator.jobs}), 3)

    def test_klein_down_still_gives_the_erased_result(self):
        with tempfile.TemporaryDirectory() as temp:
            _, shot, action = self.run_removal(temp, FakeGenerator(fail=True), FakeDetector())
        self.assertEqual([item["profile"] for item in shot["removal"]["candidates"]], ["só apagar"])
        self.assertEqual(len(action["errors"]), 3)

    def test_choosing_keeps_history_and_records_the_mask(self):
        with tempfile.TemporaryDirectory() as temp:
            folder, shot, _ = self.run_removal(temp, FakeGenerator(), FakeDetector())
            chosen = shot["removal"]["candidates"][1]
            action = removal.choose(folder, shot, 1)
            self.assertEqual((folder / SHOT["image_path"]).read_bytes(), (folder / chosen["path"]).read_bytes())
            self.assertTrue((folder / shot["qa"]["history"][0]["path"]).is_file())
            self.assertEqual(action["candidate"], 2)
            self.assertEqual(action["mask"], chosen["mask"])
            self.assertTrue((folder / action["mask"]).is_file())
            self.assertNotIn("removal", shot)
            with self.assertRaises(IndexError):
                removal.choose(folder, shot, 0)

    def test_batch_queue_runs_the_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = project(temp)
            shots = {"shots": [json.loads(json.dumps(SHOT))]}
            review.enqueue(shots["shots"][0], review.make_request("remove_figure", VB, box=BOX))
            with patch("video.qa.removal.run", return_value={"action": "removal_candidates"}) as run:
                summary = review.run_queue(FakeGenerator(), folder, VB, shots, job_factory=lambda shot: (lambda offset: None),
                                           save=lambda: None, progress=lambda _: None)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(summary["removals"], 1)
        self.assertEqual(summary["edits"], 0)


class RouteTests(unittest.TestCase):
    def test_preview_queue_with_brush_and_choose(self):
        import base64
        import io
        import gui
        import config
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch("video.qa.fixes._detector", return_value=FakeDetector()):
            folder = project(temp)
            atomic_write_json(folder / "visual_bible.json", VB)
            atomic_write_json(folder / "shot_list.json", {"shots": [json.loads(json.dumps(SHOT))]})
            client = gui.app.test_client()
            preview = client.post("/api/imagegen/shot/s02_01/mask_preview", json={"box": BOX}).get_json()
            self.assertTrue(preview["refined"])
            self.assertTrue(preview["mask"].startswith("data:image/png"))
            buffer = io.BytesIO()
            brush = Image.new("RGBA", (960, 540), (0, 0, 0, 0))
            brush.paste((255, 0, 0, 255), (600, 200, 660, 400))
            brush.save(buffer, "PNG")
            url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
            queued = client.post("/api/imagegen/shot/s02_01/queue", json={"kind": "remove_figure", "mask": url}).get_json()
            self.assertTrue(queued["ok"])
            self.assertTrue((folder / queued["queue"][0]["mask"]).is_file())
            empty = base64.b64encode(b"nope").decode("ascii")
            self.assertEqual(client.post("/api/imagegen/shot/s02_01/queue", json={"kind": "remove_figure", "mask": "x," + empty}).status_code, 400)
            shots = load_json(folder / "shot_list.json")
            removal.run(FakeGenerator(), folder, shots["shots"][0], VB, queued["queue"][0], detector=FakeDetector(), engine="opencv",
                        progress=lambda _: None)
            atomic_write_json(folder / "shot_list.json", shots)
            status = client.post("/api/imagegen/shot/s02_01/removal/choose", json={"index": 9})
            self.assertEqual(status.status_code, 400)
            self.assertTrue(client.post("/api/imagegen/shot/s02_01/removal/choose", json={"index": 0}).get_json()["ok"])
            saved = load_json(folder / "shot_list.json")["shots"][0]
        self.assertNotIn("removal", saved)
        self.assertEqual(saved["qa"]["actions"][-1]["request"], "Remover figura")


if __name__ == "__main__":
    unittest.main()
