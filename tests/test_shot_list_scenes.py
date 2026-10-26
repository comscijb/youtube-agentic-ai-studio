"""Phase 3.3.1-B: one Gemini call per scene, scene-local repairs, flags and checkpoints."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agents import shot_list as shots_module
from agents.narrative import load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import OUTLINE, SCRIPT, VISUAL_BIBLE, planned_shots, scene_responder
from tests import test_shot_planning as planning  # not imported by name: it would run twice

LAYOUT = "A storyboard of him straightening the napkin"


class PerSceneTests(unittest.TestCase):
    def setUp(self):
        self.case = planning.ShotListTests("test_budget_is_applied_before_planning")
        self.case.setUp()
        self.root = self.case.root
        self.plan = planned_shots(self.case.segments())
        self.ids = {scene: [shot["shot_id"] for shot in self.plan["shots"] if shot["shot_id"].startswith(scene)]
                    for scene in ("s01", "s02")}

    def tearDown(self):
        self.case.tearDown()

    def build(self, edit=None, force=False):
        with patch.object(shots_module, "generate", side_effect=scene_responder(self.plan, edit)) as call, \
                redirect_stdout(io.StringIO()):
            result = shots_module.build_shot_list(SCRIPT, OUTLINE, VISUAL_BIBLE, self.root, force=force)
        return result, call

    def test_each_scene_gets_its_own_call_with_narration_skeleton_and_continuity(self):
        result, call = self.build()
        prompts = [item.args[0] for item in call.call_args_list]
        self.assertEqual([item.kwargs["stage"] for item in call.call_args_list], ["shot_list-s01", "shot_list-s02"])
        self.assertIn("SCENE NARRATION: Julian set the table.", prompts[0])
        self.assertIn(f"return exactly {len(self.ids['s01'])} shots", prompts[0])
        self.assertIn("this is the first scene", prompts[0])
        self.assertIn("PREVIOUS SCENE'S LAST SHOT (read-only continuity)", prompts[1])
        self.assertNotIn('"shot_id"', prompts[0].split("SEGMENTS", 1)[1])
        self.assertEqual([shot["shot_id"] for shot in result["shots"]], self.ids["s01"] + self.ids["s02"])
        self.assertTrue((self.root / ".checkpoints" / "shot_list" / "s01.json").is_file())

    def test_only_the_failing_shot_is_repaired_and_merged_by_position(self):
        def edit(scene, number, value, prompt):
            if scene == "s01" and number == 1:
                value["shots"][1]["action"] = LAYOUT
            if scene == "s01" and number == 2:
                self.repair_prompt = prompt
                return {"shots": [dict(value["shots"][1], action="He straightens the napkin, alone")]}
            return value
        result, call = self.build(edit)
        self.assertEqual(call.call_count, 3)
        self.assertIn("SHOTS TO CORRECT", self.repair_prompt)
        self.assertIn("shot n=2: the action asks for a page layout", self.repair_prompt)
        to_fix, neighbours = self.repair_prompt.split("SHOTS TO CORRECT:", 1)[1].split("NEIGHBOUR SHOTS (read-only):")
        self.assertIn("storyboard", to_fix)
        self.assertEqual(to_fix.count('"n"'), 1)
        shots = {shot["shot_id"]: shot for shot in result["shots"]}
        self.assertEqual(shots[self.ids["s01"][1]]["action"], "He straightens the napkin, alone")
        self.assertEqual(result["flagged"], [])

    def test_a_scene_that_keeps_failing_is_flagged_without_aborting_the_others(self):
        def edit(scene, number, value, prompt):
            if scene == "s01":
                value["shots"][0]["action"] = LAYOUT
                if number > 1:                       # repairs keep the same mistake
                    return {"shots": [value["shots"][0]]}
            return value
        result, call = self.build(edit)
        self.assertEqual(call.call_count, 1 + shots_module.SCENE_REPAIRS + 1)   # s01 three times, s02 once
        self.assertEqual(result["flagged"], [self.ids["s01"][0]])
        flagged = result["shots"][0]
        self.assertIn("page layout", flagged["flags"][0])
        self.assertTrue(all("flags" not in shot for shot in result["shots"][1:]))

        # A rerun redoes only the flagged scene; the completed one comes from its checkpoint.
        result, call = self.build(force=True)
        self.assertEqual([item.kwargs["stage"] for item in call.call_args_list], ["shot_list-s01"])
        self.assertEqual(result["flagged"], [])

    def test_a_wrong_shot_count_is_retried_for_that_scene_then_padded_and_flagged(self):
        def edit(scene, number, value, prompt):
            if scene == "s02":
                value["shots"] = value["shots"][:1]
            return value
        result, call = self.build(edit)
        stages = [item.kwargs["stage"] for item in call.call_args_list]
        self.assertEqual(stages.count("shot_list-s01"), 1)
        self.assertEqual(stages.count("shot_list-s02-repair"), shots_module.SCENE_REPAIRS)
        self.assertIn("return exactly", call.call_args_list[-1].args[0])
        self.assertEqual(len(result["shots"]), len(self.ids["s01"]) + len(self.ids["s02"]))
        self.assertEqual(result["flagged"], self.ids["s02"][1:])
        saved = load_json(self.root / ".checkpoints" / "shot_list" / "s02.json")
        self.assertTrue(saved["flags"])


if __name__ == "__main__":
    unittest.main()
