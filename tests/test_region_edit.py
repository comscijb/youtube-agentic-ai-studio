"""Phase 3.5-D: Klein region-edit template, edit instructions, fix actions with history (ComfyUI mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from tests.fake_comfy import OBJECT_INFO, FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from video import imagegen_templates as templates
from video.imagegen_comfyui import ComfyClient, ImageGenerator, ImageJob
from video.qa import base, edits, fixes

INFO = {**OBJECT_INFO,
        "CreateBoundingBoxes": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}],
                                                       "editor_state": ["BOUNDING_BOXES", {}]},
                                          "optional": {"background": ["IMAGE"], "bboxes": ["X"],
                                                       "last_incoming": ["X"]}}},
        "Flux3LayoutPrompt": {"input": {"required": {"prompt": ["STRING", {}], "element": ["ARRAY", {}]}}}}
GLASSES = base.issue("missing_marker", box=[0.4, 0.15, 0.5, 0.3], target="julian_vance",
                     note="face without black horn-rimmed glasses", marker="black horn-rimmed glasses")
EXTRA = base.issue("extra_person", box=[0.7, 0.4, 0.8, 0.8])


class TemplateTests(unittest.TestCase):
    def test_derived_from_the_users_edit_export(self):
        graph = templates.derive_region_edit(templates.load_edit_template())
        self.assertFalse({"18", "34", "44", "46", "47", "48", "50", "51", "52"} & set(graph))   # upsampler bypassed
        self.assertEqual(graph["4"]["inputs"]["text"], ["49", 0])
        self.assertEqual(graph["49"]["inputs"]["element"], ["33", 2])
        self.assertEqual(graph["33"]["inputs"]["background"], ["11", 0])
        self.assertEqual(graph["7"]["inputs"]["latent_image"], ["17", 2])                       # 16:9 from the shot
        self.assertEqual(graph["17"]["inputs"]["images.image0"], ["11", 0])
        self.assertTrue(templates.REGION_EDIT_TEMPLATE.is_file())

    def test_boxes_become_canvas_pixels_and_unused_references_are_dropped(self):
        graph = templates.build_region_edit(instruction="In <ref_image_0>: remove <obj_1>.", seed=7, shot="s.png",
                                            references=["julian.png"], boxes=[([0.5, 0.25, 0.75, 1.0], "figure")])
        state = graph["33"]["inputs"]["editor_state"]["__value__"]
        self.assertEqual({k: state[0][k] for k in ("x", "y", "width", "height")},
                         {"x": 680, "y": 192, "width": 340, "height": 576})
        self.assertEqual(state[0]["metadata"]["desc"], "figure")
        self.assertEqual((graph["11"]["inputs"]["image"], graph["12"]["inputs"]["image"]), ("s.png", "julian.png"))
        self.assertNotIn("13", graph)
        self.assertNotIn("images.image2", graph["17"]["inputs"])
        self.assertEqual(templates.validate_graph(graph, INFO), [])
        bare = templates.build_region_edit(instruction="x", seed=1, shot="s.png")
        self.assertEqual(bare["33"]["inputs"]["editor_state"], {"__value__": []})              # instruction only
        with self.assertRaises(templates.TemplateError):
            templates.build_region_edit(instruction="x", seed=1, shot="s", references=["a", "b", "c", "d"])


class InstructionTests(unittest.TestCase):
    def test_glasses_uses_the_approved_portrait_as_the_second_image(self):
        with patch("agents.visual_bible.approved_reference", return_value="/refs/julian_vance.png"):
            plan = edits.compose([GLASSES, EXTRA], VISUAL_BIBLE, "/out")
        self.assertEqual(plan["references"], ["/refs/julian_vance.png"])
        self.assertEqual([box for box, _ in plan["boxes"]], [GLASSES["box"], EXTRA["box"]])
        self.assertIn("On the man in <obj_1>, add the black horn-rimmed glasses of the man in the second image; "
                      "keep his face, pose, lighting and everything else unchanged.", plan["instruction"])
        self.assertIn("Remove the figure in <obj_2> and continue the background behind it", plan["instruction"])
        self.assertTrue(plan["instruction"].startswith("In <ref_image_0>: "))

    def test_instruction_only_fixes_and_custom_text(self):
        plan = edits.compose([base.issue("unstated_injury"), base.issue("text")], VISUAL_BIBLE, "/out")
        self.assertEqual(plan["boxes"], [])
        self.assertIn("Remove the blood, wounds and red marks on every person's skin", plan["instruction"])
        self.assertIn("Remove all lettering, numbers and watermarks", plan["instruction"])
        custom = edits.compose([], VISUAL_BIBLE, "/out", custom="make the candle brighter in <box>",
                               custom_box=[0.1, 0.1, 0.2, 0.3])
        self.assertEqual(custom["instruction"], "In <ref_image_0>, make the candle brighter in <obj_1>")
        self.assertEqual(len(custom["boxes"]), 1)


class FixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.image = self.output / "images" / "shots" / "s01_01.jpg"
        self.image.parent.mkdir(parents=True)
        Image.new("RGB", (1920, 1080), (10, 10, 10)).save(self.image)
        self.fake = FakeComfy(object_info=INFO)
        self.generator = ImageGenerator(self.output, ComfyClient("http://127.0.0.1:8188", session=self.fake,
                                                                 sleep=lambda _: None))
        self.patch = patch.object(config, "IMAGEGEN_IMAGE_CHECKS", False)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def factory(self, offset):
        return ImageJob(job_id="s01_01", prompt="a man at a table", seed=100 + offset, output_path=str(self.image),
                        size=(1360, 752))

    def fix(self, shot, **kwargs):
        with redirect_stdout(io.StringIO()):
            return fixes.apply(self.generator, self.output, shot, VISUAL_BIBLE, image_path=self.image,
                               job_factory=self.factory, progress=lambda _: None, **kwargs)

    def failed(self, *issues, verdict="edit"):
        return {"shot_id": "s01_01", "qa": {"status": "fail", "verdict": verdict, "issues": list(issues)}}

    def test_edit_saves_the_version_and_queues_a_region_edit(self):
        shot = self.failed(GLASSES)
        with patch("agents.visual_bible.approved_reference", return_value=None):
            action = self.fix(shot)
        self.assertEqual((action["action"], action["number"], action["replaced_version"]), ("edit", 1, 1))
        graph = self.fake.prompts[-1]
        self.assertEqual(graph["33"]["class_type"], "CreateBoundingBoxes")
        self.assertIn("<obj_1>", graph["49"]["inputs"]["prompt"])
        self.assertTrue((self.output / "images/shots/history/s01_01_v1.jpg").is_file())
        qa = shot["qa"]
        self.assertEqual((qa["edits"], qa["verdict"], qa["history"][0]["blocking"]), (1, "recheck", 1))

    def test_budget_edit_then_regenerate_then_keep_best(self):
        shot = self.failed(GLASSES)
        with patch("agents.visual_bible.approved_reference", return_value=None):
            for _ in range(2):
                shot["qa"].update(verdict="edit", issues=[GLASSES])
                self.assertEqual(self.fix(shot)["action"], "edit")
            shot["qa"].update(verdict="edit", issues=[GLASSES, EXTRA])
            self.assertEqual(self.fix(shot)["action"], "regenerate")        # edits spent
            self.assertEqual(self.fake.prompts[-1]["7"]["inputs"]["seed"], 100 + fixes.QA_SEED_OFFSET)
            shot["qa"].update(verdict="regenerate", issues=[GLASSES, EXTRA])
            self.assertEqual(self.fix(shot)["action"], "regenerate")
            shot["qa"].update(verdict="regenerate", issues=[GLASSES, EXTRA, EXTRA])
            Image.new("RGB", (1920, 1080), (200, 0, 0)).save(self.image)  # the worst version is current
            action = self.fix(shot)
        self.assertEqual(action["action"], "kept_best")
        self.assertTrue(shot["qa"]["flagged"])
        best = next(entry for entry in shot["qa"]["history"] if entry["version"] == action["version"])
        self.assertEqual(best["blocking"], 1)
        self.assertEqual(Image.open(self.image).getpixel((5, 5)), Image.open(self.output / best["path"]).getpixel((5, 5)))
        self.assertEqual(len(shot["qa"]["history"]), 5)                   # 4 replaced versions + the worst one

    def test_custom_instruction_and_forced_regeneration(self):
        shot = {"shot_id": "s01_01", "qa": {"status": "pass", "verdict": "pass", "issues": []}}
        action = self.fix(shot, custom="remove the vase in <box>", custom_box=[0.1, 0.1, 0.3, 0.4])
        self.assertEqual((action["action"], action["kinds"]), ("edit", ["custom"]))
        self.assertEqual(self.fake.prompts[-1]["49"]["inputs"]["prompt"],
                         "In <ref_image_0>, remove the vase in <obj_1>. Change nothing outside <obj_1>.")
        self.assertEqual(self.fix(shot, force="regenerate")["action"], "regenerate")

    def test_generation_failure_keeps_the_image_and_drops_the_version(self):
        shot = self.failed(EXTRA)
        self.fake.fail_runs = 5
        action = self.fix(shot)
        self.assertEqual(action["action"], "failed")
        self.assertEqual(shot["qa"]["history"], [])
        self.assertFalse(list((self.output / "images/shots/history").glob("*.jpg")))


if __name__ == "__main__":
    unittest.main()
