"""Phase 3.1-C/E: references, approval gate, shot generation, thumbnail, calibration (mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401


def generator(output, fake):
    return ImageGenerator(output, ComfyClient("http://127.0.0.1:8188", session=fake, sleep=lambda _: None))


def shot(shot_id, characters=("julian_vance",), reuse="", face="partial", shot_type="medium"):
    scene = shot_id.split("_")[0]
    return {"shot_id": shot_id, "scene_id": scene, "section_id": int(scene[1:]), "start": 0, "end": 8,
            "narration_excerpt": "x", "shot_type": shot_type, "characters": list(characters),
            "location": "loft", "action": "He waits by the window", "face_visibility": face,
            "reuse_of": reuse, "seed": vbible.seed_for("shot", shot_id), "seed_offset": 0, "prompt": ""}


SHOTS = [shot("s01_01"), shot("s01_02", characters=(), shot_type="insert", face="none"),
         shot("s02_01", characters=("julian_vance", "marcus_croft")), shot("s02_02", reuse="s01_02")]


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class GenerationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        atomic_write_json(self.output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
        atomic_write_json(self.output / "shot_list.json",
                          {"version": 1, "shots": json.loads(json.dumps(SHOTS)), "unique_shots": 3})
        self.fake = FakeComfy()

    def tearDown(self):
        self.temp.cleanup()

    def vb(self):
        return load_json(self.output / vbible.FILE_NAME)

    def approve_all(self):
        quiet(gen.generate_reference_candidates, self.vb(), self.output, generator(self.output, self.fake))
        for cid in ("julian_vance", "marcus_croft"):
            gen.approve_reference(self.vb(), "character", cid, 2, self.output)


class ReferenceTests(GenerationTestCase):
    def test_candidates_four_per_character_two_per_recurring_location(self):
        record = quiet(gen.generate_reference_candidates, self.vb(), self.output,
                       generator(self.output, self.fake))
        self.assertEqual(len(record["character:julian_vance"]), 4)
        self.assertEqual(len(record["location:loft"]), 2)
        self.assertEqual(len(self.fake.prompts), 3 * 4 + 2)
        portrait = self.fake.prompts[0]
        self.assertEqual((portrait["44"]["inputs"]["width"], portrait["44"]["inputs"]["height"]), (1024, 1024))
        self.assertIn("chest-up, front view", portrait["4"]["inputs"]["text"])
        self.assertIn("charcoal tailored suit", portrait["4"]["inputs"]["text"])
        seeds = [graph["7"]["inputs"]["seed"] for graph in self.fake.prompts[:4]]
        base = vbible.seed_for("character", "julian_vance")
        self.assertEqual(seeds, [base + 1, base + 2, base + 3, base + 4])     # deterministic

    def test_approval_copies_png_and_records_seed_and_prompt(self):
        self.approve_all()
        vb = self.vb()
        ref = vb["references"]["character:julian_vance"]
        self.assertEqual(ref["path"], "character_refs/julian_vance.png")
        self.assertEqual(ref["seed"], vbible.seed_for("character", "julian_vance") + 2)
        self.assertIn("three-quarter view", ref["prompt"])
        with Image.open(self.output / ref["path"]) as image:
            self.assertEqual(image.format, "PNG")
        with self.assertRaises(FileNotFoundError):
            gen.approve_reference(vb, "character", "julian_vance", 9, self.output)


class ShotTests(GenerationTestCase):
    def test_gate_refuses_before_approval(self):
        with self.assertRaises(gen.ApprovalRequired) as caught:
            gen.generate_shots(self.output, generator(self.output, self.fake))
        self.assertIn("julian_vance", str(caught.exception))
        self.assertIn("marcus_croft", str(caught.exception))
        self.assertEqual(self.fake.prompts, [])

    def test_generates_unique_shots_with_references_and_provenance(self):
        self.approve_all()
        before = len(self.fake.prompts)
        summary = quiet(gen.generate_shots, self.output, generator(self.output, self.fake))
        graphs = self.fake.prompts[before:]
        self.assertEqual(summary["generated"], 3)                 # s02_02 reuses s01_02
        self.assertEqual([("17" in graph) for graph in graphs], [True, False, True])
        self.assertEqual(len([key for key in graphs[2] if graphs[2][key]["class_type"] == "LoadImage"]), 2)
        self.assertIn("the man from the first image", graphs[0]["4"]["inputs"]["text"])
        shots = load_json(self.output / "shot_list.json")["shots"]
        self.assertTrue(all(item.get("image_path") for item in shots if not item["reuse_of"]))
        self.assertNotIn("image_path", shots[3])
        with Image.open(self.output / shots[0]["image_path"]) as image:
            self.assertEqual(image.size, (1920, 1080))
        manifest = load_json(self.output / "asset_manifest.json")
        self.assertTrue(manifest["synthetic_media"])
        self.assertEqual({item["job_id"] for item in manifest["generated_assets"]},
                         {"s01_01", "s01_02", "s02_01"})
        self.assertTrue(all(item["synthetic"] for item in manifest["generated_assets"]))

    def test_resumable_limit_and_only(self):
        self.approve_all()
        first = quiet(gen.generate_shots, self.output, generator(self.output, self.fake), limit=1)
        self.assertEqual(first["total"], 1)
        only = quiet(gen.generate_shots, self.output, generator(self.output, self.fake), only="s02")
        self.assertEqual(only["total"], 1)
        rest = quiet(gen.generate_shots, self.output, generator(self.output, self.fake))
        self.assertEqual(rest["total"], 1)                        # only s01_02 was left
        done = quiet(gen.generate_shots, self.output, generator(self.output, self.fake))
        self.assertEqual(done["total"], 0)

    def test_failed_generation_falls_back_to_stock(self):
        self.approve_all()
        failing = FakeComfy(fail_runs=100)

        def fake_fallback(output, vb, shot):
            def run(job):
                Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1920, 1080), (20, 20, 20)).save(job.output_path)
                return {"provider": "pixabay", "job_id": job.job_id, "synthetic": False,
                        "local_path": job.output_path}
            return run

        with patch.object(gen, "stock_fallback", fake_fallback), \
                patch.object(config, "STOCK_ENABLED", True), \
                patch.object(config, "VISUAL_STYLE", "cinematic_photo"):
            # Stock is only a fallback for the legacy photographic style.
            self.approve_all()
            summary = quiet(gen.generate_shots, self.output, generator(self.output, failing), only="s01_02")
        self.assertEqual(summary["fallback"], 1)
        entry = next(item for item in load_json(self.output / "asset_manifest.json")["generated_assets"]
                     if item["job_id"] == "s01_02")
        self.assertEqual(entry["fallback_from"], "comfyui")
        self.assertEqual(len(entry["generation_errors"]), 2)

    def test_memory_is_freed_between_batches(self):
        self.approve_all()
        with patch.object(config, "IMAGEGEN_FREE_MEMORY_EVERY", 1):
            quiet(gen.generate_shots, self.output, generator(self.output, self.fake))
        self.assertEqual(self.fake.freed, 3)

    def test_thumbnail_image_is_1280x720(self):
        self.approve_all()
        path = quiet(gen.generate_thumbnail_image, self.output, generator(self.output, self.fake))
        with Image.open(path) as image:
            self.assertEqual(image.size, (1280, 720))
        self.assertIn("negative space on the left", self.fake.prompts[-1]["4"]["inputs"]["text"])

    def test_gpu_estimate_uses_calibration(self):
        calibration = self.output / "cal.json"
        calibration.write_text(json.dumps({"modes": {"ref": {"seconds_per_image": 60}}}), encoding="utf-8")
        with patch.object(config, "IMAGEGEN_CALIBRATION_FILE", str(calibration), create=True):
            self.assertEqual(gen.estimate_gpu_seconds(load_json(self.output / "shot_list.json"), self.output), 180)

    def test_scripts_report_gate_and_offline_comfyui(self):
        from scripts import generate_shots
        with patch.object(gen, "ensure_shot_list", return_value=load_json(self.output / "shot_list.json")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = generate_shots.main(["--output-dir", str(self.output)])
        self.assertEqual(code, 3)
        self.assertIn("Approve a reference portrait", err.getvalue())


class CalibrationScriptTests(unittest.TestCase):
    def test_calibration_writes_timings_and_contact_sheet(self):
        from scripts import calibrate_imagegen
        fake = FakeComfy()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(calibrate_imagegen, "ImageGenerator",
                             lambda output: generator(output, fake)), \
                patch.object(gen, "CALIBRATION_FILE", Path(temp, "cal.json")):
            with redirect_stdout(io.StringIO()):
                code = calibrate_imagegen.main(["--output-dir", temp])
            data = json.loads(Path(temp, "cal.json").read_text(encoding="utf-8"))
            sheet = Path(temp, "imagegen_calibration", "contact_sheet.jpg")
            self.assertTrue(sheet.exists())
            with Image.open(sheet) as image:
                self.assertEqual(image.size[0], 448 * 3)
        self.assertEqual(code, 0)
        self.assertEqual(set(data["modes"]), {"t2i", "ref_reference", "ref_empty_16x9", "ref"})
        self.assertEqual(len(data["modes"]["t2i"]["samples"]), 3)
        self.assertEqual(data["modes"]["ref_reference"]["vram_peak_mb"], 5120.0)
        latents = [graph["7"]["inputs"]["latent_image"] for graph in fake.prompts if "17" in graph]
        self.assertIn(["17", 2], latents)
        self.assertIn(["44", 0], latents)


if __name__ == "__main__":
    unittest.main()
