"""Shot list validators: counted figures, blocking vs advisory rules, shot-scoped repair."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents import shot_list as shots_module
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import OUTLINE, SCRIPT, VISUAL_BIBLE, planned_shots, scene_responder
from tests import test_shot_planning as planning  # not imported by name: it would run twice

JULIAN_ELENA = ["julian_vance", "elena_vance"]
JULIAN_MARCUS = ["julian_vance", "marcus_croft"]
# The five actions the Phase 3.3 shot list wrongly rejected (projects/<slug>/debug/shot-list-repair-2.txt).
REAL_ACTIONS = [
    ("s01_05", JULIAN_ELENA, "Two figures stand in quiet confrontation near the kitchen counter, illuminated by "
                             "dim overhead downlighting against raw concrete walls."),
    ("s02_03", JULIAN_ELENA, "Two small figures stand in a vast, dark kitchen space defined by sharp geometry "
                             "and severe shadows."),
    ("s03_02", JULIAN_ELENA, "Two figures stand dwarfed within a vast, symmetrical wardrobe chamber lined with "
                             "floor-to-ceiling dark panels and cool linear reflections."),
    ("s04_04", JULIAN_MARCUS, "Two figures stand small within the monumental brutalist dining gallery, "
                              "surrounded by towering raw concrete and distant urban silhouettes."),
    ("s04_06", JULIAN_MARCUS + ["elena_vance"], "Three figures occupy opposite ends of the cavernous concrete "
                                                "loft as one woman slips quietly toward the background shadows."),
]


def document(actions, others_present=False):
    segments = [{"shot_id": sid, "text": "They waited in the loft."} for sid, _, _ in actions]
    shots = [{"shot_id": sid, "shot_type": "wide", "characters": cast, "location_id": "loft", "action": action,
              "face_visibility": "partial", "reuse_of": "", "others_present": others_present,
              "appearance": {cid: "composed" for cid in cast}} for sid, cast, action in actions]
    return {"settings": {"loft": "immaculate"}, "shots": shots}, segments


class UnnamedPeopleTests(unittest.TestCase):
    def test_counted_figures_that_are_the_cast_pass(self):
        value, segments = document(REAL_ACTIONS)
        self.assertEqual(len(shots_module.validate_shots(value, segments, VISUAL_BIBLE, OUTLINE, "reference")), 5)
        self.assertEqual(shots_module.unnamed_people("the two of them alone at the table", 2), [])

    def test_guests_crowds_and_extra_figures_are_advice_not_rejections(self):
        for action, cast in (("two guests watch from the doorway", JULIAN_ELENA),
                             ("a crowd fills the gallery", JULIAN_ELENA),
                             ("Three figures stand at the table", JULIAN_ELENA),
                             ("A waiter pours wine beside them", JULIAN_ELENA)):
            with self.subTest(action=action):
                value, segments = document([("s01_01", cast, action)])
                shots_module.validate_shots(value, segments, VISUAL_BIBLE, OUTLINE, "reference")   # never blocks
                notes, _ = shots_module.shot_advice(value, segments)
                self.assertIn("while others_present is false", notes["s01_01"][0])
                allowed, segments = document([("s01_01", cast, action)], others_present=True)
                self.assertEqual(shots_module.shot_advice(allowed, segments)[0], {})

    def test_scenery_silhouettes_and_uncounted_plurals_are_not_people(self):
        for action, size in (("Julian stands before faint urban silhouettes of the skyline", 1),
                             ("Two silhouettes of towers beyond the glass", 1),
                             ("Figures linger behind him", 1)):
            with self.subTest(action=action):
                self.assertEqual(shots_module.unnamed_people(action, size), [])
        self.assertEqual(shots_module.unnamed_people("three silhouettes wait at the door", 2),
                         ["three silhouettes"])


class AdvisoryRuleTests(unittest.TestCase):
    def test_wide_target_is_read_from_the_environment(self):
        env = dict(os.environ, IMAGEGEN_MIN_WIDE_SHARE="0.25")
        result = subprocess.run([sys.executable, "-c", "import config; print(config.IMAGEGEN_MIN_WIDE_SHARE)"],
                                capture_output=True, text=True, env=env, cwd=Path(__file__).parent.parent)
        self.assertEqual(result.stdout.strip(), "0.25")

    def test_few_wides_and_many_faces_are_warnings_in_the_report(self):
        case = planning.ShotListTests("test_budget_is_applied_before_planning")
        case.setUp()
        try:
            segments = case.segments()
            plan = planned_shots(segments, full=len(segments))
            for shot in plan["shots"]:
                shot["shot_type"] = "medium"
            with patch.object(shots_module, "generate", side_effect=scene_responder(plan)) as call, \
                    redirect_stdout(io.StringIO()) as out:
                result = shots_module.build_shot_list(SCRIPT, OUTLINE, VISUAL_BIBLE, case.root)
            saved = load_json(case.root / shots_module.FILE_NAME)
        finally:
            case.tearDown()
        self.assertEqual(call.call_count, 2)                     # one per scene; no repair for targets
        self.assertEqual(len(result["warnings"]), 2)
        self.assertIn("wide/establishing shots: 0/", saved["warnings"][0])
        self.assertIn("a target, not a failure", out.getvalue())
        prompt = call.call_args.args[0]
        self.assertIn("a target, not a quota", prompt)
        self.assertIn("Aim for about 30%", prompt)


if __name__ == "__main__":
    unittest.main()
