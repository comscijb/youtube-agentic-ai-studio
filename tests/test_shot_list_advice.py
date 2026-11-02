"""Phase 3.3.1-C: only unusable shots block; content doubts are advisory notes."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agents import identity
from agents import shot_list as shots_module
from agents.narrative import load_json
from tests import test_shot_planning as planning  # not imported by name: it would run twice
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import OUTLINE, SCRIPT, VISUAL_BIBLE, planned_shots, scene_responder

SCENE_FIVE = ("Elena stood by the window, her face blotched and wet. \"I can't,\" she choked out, "
              "the words breaking.")


class RealPhraseTests(unittest.TestCase):
    def test_evidence_phrases_raise_no_notes(self):
        crying = {"shot_id": "s05_02", "characters": ["elena_vance"], "others_present": False,
                  "action": "Close on her tear-stained face, sobbed breaths fogging the glass",
                  "appearance": {"elena_vance": "tear-stained, hair loosened"}}
        self.assertEqual(shots_module.advice_for(crying, SCENE_FIVE), [])
        skyline = {"shot_id": "s08_04", "characters": ["julian_vance"], "others_present": False,
                   "action": "He stands at the glass against faint urban silhouettes of the city",
                   "appearance": {"julian_vance": "composed"}}
        self.assertEqual(shots_module.advice_for(skyline, "Julian looked out at the city."), [])
        two = {"shot_id": "s01_05", "characters": ["julian_vance", "elena_vance"], "others_present": False,
               "action": "Two figures stand in quiet confrontation near the kitchen counter",
               "appearance": {"julian_vance": "tense", "elena_vance": "composed"}}
        self.assertEqual(shots_module.advice_for(two, "They argued."), [])

    def test_genuine_doubts_are_notes(self):
        guests = {"characters": ["julian_vance", "elena_vance"], "others_present": False,
                  "action": "two guests watch from the doorway", "appearance": {}}
        self.assertIn("guests", shots_module.advice_for(guests, "They argued.")[0])
        tears = {"characters": ["elena_vance"], "others_present": False, "action": "She sits",
                 "appearance": {"elena_vance": "tearful"}}
        self.assertIn("tearful", shots_module.advice_for(tears, "She smiled at him.")[0])
        self.assertEqual(identity.unsupported_terms("tearful", SCENE_FIVE), [])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.case = planning.ShotListTests("test_budget_is_applied_before_planning")
        self.case.setUp()
        self.plan = planned_shots(self.case.segments())
        self.first = self.plan["shots"][0]["shot_id"]
        self.second = self.plan["shots"][1]["shot_id"]

    def tearDown(self):
        self.case.tearDown()

    def test_blocking_subjects_are_flagged_and_doubts_go_to_the_report(self):
        def edit(scene, number, value, prompt):
            for shot in value["shots"]:
                if shot["shot_id"] == self.first:
                    shot["action"] = "Tentacles curl around the table leg"          # always blocking
                if shot["shot_id"] == self.second:
                    shot["action"] = "Two guests watch him from the doorway"       # advisory
            return value
        with patch.object(shots_module, "generate", side_effect=scene_responder(self.plan, edit)), \
                redirect_stdout(io.StringIO()) as out:
            result = shots_module.build_shot_list(SCRIPT, OUTLINE, VISUAL_BIBLE, self.case.root)
        report = load_json(self.case.root / shots_module.REPORT_FILE)
        shots = {shot["shot_id"]: shot for shot in result["shots"]}
        self.assertEqual(result["flagged"], [self.first])
        self.assertIn("excluded subjects", shots[self.first]["flags"][0])
        self.assertNotIn("flags", shots[self.second])
        self.assertIn("guests", shots[self.second]["warnings"][0])
        self.assertEqual(list(report["flagged"]), [self.first])
        self.assertEqual(list(report["advisory"]), [self.second])
        self.assertIn("advisory notes", out.getvalue())


if __name__ == "__main__":
    unittest.main()
