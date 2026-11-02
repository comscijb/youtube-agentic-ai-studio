"""Phase 3.4-A: every listed character gets its reference; anchor, then location, are dropped first."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agents import shot_list as shots_module
from tests import test_shot_planning as planning  # not imported by name: it would run twice
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import OUTLINE, SCRIPT, VISUAL_BIBLE, planned_shots, scene_responder


def shot(characters, face="none", shot_type="silhouette"):
    return {"shot_id": "s01_01", "scene_id": "s01", "shot_type": shot_type, "characters": characters,
            "location": "loft", "action": "They stand at the window", "face_visibility": face,
            "others_present": False, "appearance": {cid: "composed" for cid in characters},
            "seed": 1, "seed_offset": 0}


class CoverageTests(unittest.TestCase):
    def test_hidden_faces_still_get_their_references(self):
        for face, shot_type in (("none", "silhouette"), ("none", "wide"), ("partial", "hands"),
                                ("none", "over_the_shoulder"), ("full", "close_up")):
            with self.subTest(face=face, shot_type=shot_type):
                cast = ["julian_vance", "elena_vance"]
                composed = shots_module.compose_shot(shot(cast, face, shot_type), VISUAL_BIBLE, "unused",
                                                     assume_references=True)
                self.assertEqual([slot["id"] for slot in composed["slots"] if slot["kind"] == "character"], cast)

    def test_three_characters_and_a_location_fill_the_slots(self):
        composed = shots_module.compose_shot(shot(["marcus_croft", "elena_vance", "julian_vance"], "partial", "wide"),
                                             VISUAL_BIBLE, "unused", assume_references=True)
        self.assertEqual(len(composed["references"]), 3)       # no location ref approved in this fixture
        self.assertEqual({slot["id"] for slot in composed["slots"]},
                         {"julian_vance", "marcus_croft", "elena_vance"})

    def test_no_shot_in_a_rebuilt_list_lacks_a_listed_reference(self):
        case = planning.ShotListTests("test_budget_is_applied_before_planning")
        case.setUp()
        try:
            plan = planned_shots(case.segments())
            for index, item in enumerate(plan["shots"]):
                item["face_visibility"] = "none"
                item["characters"] = ["julian_vance", "elena_vance", "marcus_croft"][:1 + index % 3]
                item["appearance"] = {cid: "composed" for cid in item["characters"]}
            with patch.object(shots_module, "generate", side_effect=scene_responder(plan)), \
                    redirect_stdout(io.StringIO()):
                result = shots_module.build_shot_list(SCRIPT, OUTLINE, VISUAL_BIBLE, case.root)
        finally:
            case.tearDown()
        for item in result["shots"]:
            with self.subTest(shot=item["shot_id"]):
                referenced = {slot["id"] for slot in item["reference_slots"] if slot["kind"] == "character"}
                self.assertEqual(referenced, set(item["characters"]))


if __name__ == "__main__":
    unittest.main()
