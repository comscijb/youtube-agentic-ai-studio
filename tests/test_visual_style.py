"""Phase 3.2-B: dark-illustration visual identity, style anchors and style-specific assets."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from agents import identity, style_refs
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator, ImageJob


def generator(output, fake):
    return ImageGenerator(output, ComfyClient("http://127.0.0.1:8188", session=fake, sleep=lambda _: None))


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class StyleTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.patches = [patch.object(config, "STYLE_REFS_DIR", str(self.root / "style_refs")),
                        patch.object(config, "VISUAL_STYLE", "dark_illustration"),
                        patch.object(config, "VISUAL_STYLE_VARIANT", "hybrid"),
                        patch.object(config, "IMAGEGEN_VISUAL_QA", False)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def approve_anchor(self, number=1):
        folder = style_refs.candidates_dir()
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 36), (12, 18, 24)).save(folder / f"board_{number}.jpg")
        return style_refs.approve(number)


class IdentitySpecTests(StyleTestCase):
    def test_palette_is_read_from_config_and_suffix_is_illustrated(self):
        suffix = identity.style_suffix()
        for key in ("background", "primary", "white", "highlight", "accent", "red"):
            self.assertIn(identity._hex(config.COLORS[key]), suffix)
        self.assertIn("scratchboard", suffix)
        self.assertIn("stay formless", suffix)
        for photographic in ("35 mm", "depth of field", "photorealistic,"):
            self.assertNotIn(photographic, suffix)
        colors = dict(config.COLORS, background=(1, 2, 3))
        with patch.object(config, "COLORS", colors):
            self.assertIn("#010203", identity.style_suffix())

    def test_exclusions_are_rules_not_prompt_words(self):
        suffix = identity.style_suffix()
        for banned in ("tentacles", "monster", "creature", "giant eyes", "occult"):
            self.assertNotIn(banned, suffix)               # naming them would invite them (cfg 1)
            self.assertIn(banned, identity.image_prompt_rules().lower())
        self.assertEqual(identity.forbidden_terms("Tentacles rise from the fog near giant eyes"),
                         ["tentacles", "giant eyes"])
        self.assertEqual(identity.forbidden_terms("fog that suggests a presence"), [])

    def test_variants_and_style_hash(self):
        hashes = {identity.style_hash(variant=v) for v in identity.STYLE_VARIANTS}
        self.assertEqual(len(hashes), 3)
        self.assertNotEqual(identity.style_hash("cinematic_photo"), identity.style_hash())
        current = identity.style_hash()
        with patch.object(identity, "STYLE_VERSION", identity.STYLE_VERSION + 1):
            self.assertNotEqual(identity.style_hash(), current)          # rewording bumps the version
        with patch.object(config, "VISUAL_STYLE", "cinematic_photo"):
            self.assertEqual(identity.image_noun(), "photograph")
            self.assertIn("35 mm", identity.style_suffix())
        self.assertEqual(identity.image_noun(), "illustration")

    def test_style_hash_is_in_every_cache_key(self):
        job = ImageJob(job_id="x", prompt="p", seed=1, output_path=str(self.output / "x.jpg"))
        generate = generator(self.output, FakeComfy())
        hybrid = generate.cache_key(job, 1)
        with patch.object(config, "VISUAL_STYLE_VARIANT", "engraving"):
            self.assertNotEqual(generate.cache_key(job, 1), hybrid)


class SlotPlanTests(StyleTestCase):
    def vb_with_refs(self, characters=("julian_vance", "marcus_croft", "elena_vance")):
        vb = json.loads(json.dumps(VISUAL_BIBLE))
        refs = {}
        for kind, ident in [("character", c) for c in characters] + [("location", "loft")]:
            rel = f"{kind}_refs/{ident}.png"
            (self.output / rel).parent.mkdir(exist_ok=True)
            Image.new("RGB", (32, 32)).save(self.output / rel)
            refs[f"{kind}:{ident}"] = {"path": rel, "seed": 1, "prompt": "", "style_hash": identity.style_hash(),
                                       "sheet_version": vbible.CHARACTER_SHEET_VERSION}
        vb["references"] = refs
        return vb

    def shot(self, characters, shot_type="medium"):
        return {"shot_id": "s02_01", "scene_id": "s02", "shot_type": shot_type, "characters": list(characters),
                "location": "loft", "action": "They wait across the table", "face_visibility": "partial",
                "seed": 10, "seed_offset": 0}

    def test_characters_then_location_then_style_anchor(self):
        self.approve_anchor(1)
        vb = self.vb_with_refs()
        composed = shots_module.compose_shot(self.shot(["julian_vance", "marcus_croft"]), vb, self.output)
        self.assertEqual([slot["kind"] for slot in composed["slots"]],
                         ["character", "character", "location", "style"])
        self.assertEqual([slot["slot"] for slot in composed["slots"]], [0, 1, 2, 3])
        self.assertIn("Rendered in the drawing style of the fourth image", composed["prompt"])
        self.assertIn("in the room from the third image", composed["prompt"])
        self.assertTrue(composed["prompt"].startswith("Create an illustration of the man from the first image"))
        # Three listed characters: the style anchor is dropped first, never a character.
        three = shots_module.compose_shot(self.shot(["julian_vance", "marcus_croft", "elena_vance"]), vb, self.output)
        self.assertEqual([slot["kind"] for slot in three["slots"]],
                         ["character", "character", "character", "location"])
        self.assertIn("the woman from the third image", three["prompt"])

    def test_style_anchor_takes_the_next_free_slot(self):
        self.approve_anchor(2)
        composed = shots_module.compose_shot(self.shot(["julian_vance"]), self.vb_with_refs(), self.output)
        self.assertEqual([slot["kind"] for slot in composed["slots"]], ["character", "location", "style"])
        self.assertIn("drawing style of the third image", composed["prompt"])
        class Nobody:                                    # the anchor is checked for people (3.7): it is empty
            def detect(self, path):
                return {"persons": []}
        with patch("video.qa.fixes._detector", return_value=Nobody()):
            insert = shots_module.compose_shot(self.shot([], "insert"), self.vb_with_refs(), self.output)
        # 3.9.3 hotfix: a style board is never the only reference (Klein copies it): the insert is t2i.
        self.assertEqual([slot["kind"] for slot in insert["slots"]], [])
        self.assertEqual(insert["kind"], "t2i")

    def test_references_are_style_specific(self):
        vb = self.vb_with_refs()
        vb["references"]["character:julian_vance"]["style_hash"] = identity.style_hash(variant="engraving")
        del vb["references"]["character:marcus_croft"]["style_hash"]          # legacy photographic approval
        self.assertIsNone(vbible.approved_reference(vb, "character", "julian_vance", self.output))
        self.assertIsNone(vbible.approved_reference(vb, "character", "marcus_croft", self.output))
        with patch.object(config, "VISUAL_STYLE", "cinematic_photo"):
            self.assertTrue(vbible.approved_reference(vb, "character", "marcus_croft", self.output))


class StyleAnchorTests(StyleTestCase):
    def test_approve_keeps_at_most_two_per_style(self):
        for number in (1, 2, 3):
            self.approve_anchor(number)
        self.assertEqual([entry["number"] for entry in style_refs.approved()], [2, 3])
        self.assertEqual(len(style_refs.approved_paths()), 2)
        with patch.object(config, "VISUAL_STYLE_VARIANT", "engraving"):
            self.assertEqual(style_refs.approved(), [])                        # other style hash
        with self.assertRaises(FileNotFoundError):
            style_refs.approve(9)

    def test_generate_style_refs_script(self):
        from scripts import generate_style_refs
        fake = FakeComfy()
        with patch.object(generate_style_refs, "ImageGenerator", lambda output: generator(output, fake)):
            with redirect_stdout(io.StringIO()):
                code = generate_style_refs.main(["--output-dir", str(self.output)])
                approve = generate_style_refs.main(["--output-dir", str(self.output), "--approve", "4"])
        self.assertEqual((code, approve), (0, 0))
        self.assertEqual(len(fake.prompts), 6)
        prompt = fake.prompts[0]["4"]["inputs"]["text"]
        self.assertIn("deserted and unoccupied", prompt)                       # positive wording only
        self.assertNotIn("people", prompt)
        self.assertNotIn("17", fake.prompts[0])                                 # no third-party references
        self.assertEqual(style_refs.approved()[0]["number"], 4)

    def test_calibrate_style_three_variants_six_prompts(self):
        from scripts import calibrate_style
        fake = FakeComfy()
        with patch.object(calibrate_style, "ImageGenerator", lambda output: generator(output, fake)), \
                patch.object(calibrate_style, "RESULT_FILE", self.root / "style_calibration.json"):
            with redirect_stdout(io.StringIO()):
                code = calibrate_style.main(["--output-dir", str(self.output)])
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.prompts), 18)
        texts = [graph["4"]["inputs"]["text"] for graph in fake.prompts]
        self.assertIn(identity.STYLE_VARIANTS["engraving"], texts[0])
        self.assertIn(identity.STYLE_VARIANTS["hybrid"], texts[-1])
        data = json.loads((self.root / "style_calibration.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data["variants"]), {"engraving", "charcoal_gouache", "hybrid"})
        sheet = self.output / "style_calibration" / "contact_sheet.jpg"
        with Image.open(sheet) as image:
            self.assertEqual(image.size[0], 150 + 384 * 3)


class GenerationInStyleTests(StyleTestCase):
    def setUp(self):
        super().setUp()
        atomic_write_json(self.output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))

    def vb(self):
        return load_json(self.output / vbible.FILE_NAME)

    def test_old_photographic_references_are_archived_and_regenerated(self):
        refs = self.output / "character_refs"
        (refs / "candidates").mkdir(parents=True)
        Image.new("RGB", (32, 32)).save(refs / "julian_vance.png")
        Image.new("RGB", (32, 32)).save(refs / "candidates" / "julian_vance_1.jpg")
        atomic_write_json(self.output / gen.CANDIDATES_FILE, {"character:julian_vance": [
            {"number": 1, "path": "character_refs/candidates/julian_vance_1.jpg", "seed": 1, "prompt": "p"}]})
        fake = FakeComfy()
        quiet(gen.generate_reference_candidates, self.vb(), self.output, generator(self.output, fake),
              characters=["julian_vance"], locations=False)
        archived = list((refs / "archive").rglob("*.png")) + list((refs / "archive").rglob("*.jpg"))
        self.assertEqual(sorted(path.name for path in archived), ["julian_vance.png", "julian_vance_1.jpg"])
        record = load_json(self.output / gen.CANDIDATES_FILE)["character:julian_vance"]
        self.assertEqual(len(record), 4)
        self.assertTrue(all(item["style_hash"] == identity.style_hash() for item in record))
        self.assertIn("An illustration character reference sheet: head and shoulders",
                      fake.prompts[0]["4"]["inputs"]["text"])

    def approve_all(self, fake):
        quiet(gen.generate_reference_candidates, self.vb(), self.output, generator(self.output, fake))
        for cid in ("julian_vance", "marcus_croft"):
            gen.approve_reference(self.vb(), "character", cid, 1, self.output)

    def test_failed_shot_tries_two_new_seeds_then_holds_previous_without_stock(self):
        shots = [
            {"shot_id": "s01_01", "scene_id": "s01", "section_id": 1, "start": 0, "end": 8,
             "shot_type": "medium", "characters": ["julian_vance"], "location": "loft",
             "action": "He waits", "face_visibility": "partial", "reuse_of": "", "seed": 1, "seed_offset": 0},
            {"shot_id": "s01_02", "scene_id": "s01", "section_id": 1, "start": 8, "end": 16,
             "shot_type": "insert", "characters": [], "location": "loft",
             "action": "A decanter", "face_visibility": "none", "reuse_of": "", "seed": 2, "seed_offset": 0},
        ]
        atomic_write_json(self.output / "shot_list.json", {"shots": shots, "unique_shots": 2})
        fake = FakeComfy()
        self.approve_all(fake)
        quiet(gen.generate_shots, self.output, generator(self.output, fake), limit=1)
        failing = FakeComfy(fail_runs=3)
        with patch.object(gen, "stock_fallback", side_effect=AssertionError("stock must not be used")):
            before = len(failing.prompts)
            summary = quiet(gen.generate_shots, self.output, generator(self.output, failing))
        self.assertEqual(len(failing.prompts) - before, 3)                     # seed + 2 new seeds
        self.assertEqual(summary["held"], ["s01_02"])
        saved = load_json(self.output / "shot_list.json")["shots"]
        self.assertTrue(saved[1]["hold_previous"])
        self.assertEqual(shots_module.resolve_image(saved, saved[1]), saved[0]["image_path"])
        manifest = load_json(self.output / "asset_manifest.json")
        held = next(item for item in manifest["generated_assets"] if item["job_id"] == "s01_02")
        self.assertEqual(held["fallback"], "hold_previous")
        generated = next(item for item in manifest["generated_assets"] if item["job_id"] == "s01_01")
        self.assertEqual(generated["visual_style"], "dark_illustration")

    def test_thumbnail_uses_the_protagonist_reference(self):
        fake = FakeComfy()
        self.approve_all(fake)
        vb = self.vb()
        vb["thumbnail_shot"]["characters"] = ["marcus_croft"]
        atomic_write_json(self.output / vbible.FILE_NAME, vb)
        quiet(gen.generate_thumbnail_image, self.output, generator(self.output, fake))
        prompt = fake.prompts[-1]["4"]["inputs"]["text"]
        self.assertIn(". Create an illustration of the man from the first image", prompt)     # after the full-bleed clause
        graph = fake.prompts[-1]
        loads = [node["inputs"]["image"] for key, node in sorted(graph.items())
                 if node["class_type"] == "LoadImage"]
        self.assertEqual(len(loads), 2)                       # protagonist first, then Marcus
        julian = vbible.approved_reference(load_json(self.output / vbible.FILE_NAME), "character",
                                           "julian_vance", self.output)
        from video.imagegen_comfyui import file_hash
        self.assertEqual(graph["11"]["inputs"]["image"][3:23], file_hash(julian)[:20])


class RenderAndDisclosureTests(StyleTestCase):
    def test_overlay_base_is_lighter_for_illustration(self):
        from video import creator
        self.assertEqual(creator.overlay_base(), config.ILLUSTRATION_OVERLAY_OPACITY)
        self.assertLess(creator.adaptive_opacity(200), 0.2)
        with patch.object(config, "VISUAL_STYLE", "cinematic_photo"):
            self.assertEqual(creator.overlay_base(), config.CINEMATIC_OVERLAY_OPACITY)

    def test_illustration_disclosure_is_a_reviewer_decision_defaulting_to_not_realistic(self):
        from uploader import approval
        manifest = {"assets": [], "sections": [], "synthetic_media": True,
                    "generated_assets": [{"job_id": "s01_01", "synthetic": True,
                                          "visual_style": "dark_illustration"}]}
        atomic_write_json(self.output / "asset_manifest.json", manifest)
        self.assertFalse(approval.realistic_synthetic_media(self.output))
        item = approval.checklist_for(self.output)["synthetic_media_disclosed"]
        self.assertIn("Reviewer decision", item)
        self.assertIn("non-photorealistic illustration", item)
        manifest["generated_assets"].append({"job_id": "old", "synthetic": True})   # legacy photo asset
        atomic_write_json(self.output / "asset_manifest.json", manifest)
        self.assertTrue(approval.realistic_synthetic_media(self.output))

    def test_gui_rejects_shot_edits_with_excluded_subjects(self):
        import gui
        atomic_write_json(self.output / "shot_list.json", {"shots": [
            {"shot_id": "s01_01", "action": "He waits"}]})
        with patch.object(config, "OUTPUT_DIR", str(self.output)):
            response = gui.app.test_client().post("/api/imagegen/shot/s01_01/prompt",
                                                  json={"action": "Tentacles curl from the fog"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("tentacles", response.get_json()["error"])

    def test_gui_style_anchor_routes(self):
        import gui
        folder = style_refs.candidates_dir()
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 36)).save(folder / "board_2.jpg")
        client = gui.app.test_client()
        with patch.object(config, "OUTPUT_DIR", str(self.output)), \
                patch.object(ComfyClient, "available", return_value=False):
            self.assertTrue(client.post("/api/imagegen/style/approve", json={"number": 2}).get_json()["ok"])
            status = client.get("/api/imagegen/status").get_json()
            ok = client.get("/imagegen/style_file?name=board_2.jpg")
            self.assertEqual(ok.status_code, 200)
            ok.close()
            self.assertEqual(client.get("/imagegen/style_file?name=../approved.json").status_code, 404)
        self.assertEqual(status["style"]["boards"], ["board_2.jpg"])
        self.assertEqual(status["style"]["approved"], [2])


if __name__ == "__main__":
    unittest.main()
