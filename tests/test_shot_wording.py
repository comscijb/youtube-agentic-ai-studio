"""Phase 3.9.4-A: figures that don't appear in the first place — wording rewrite, headcount, reference choice."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from scripts import replan_shots
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

F = Path(__file__).parent / "fixtures" / "shot_wording.json"
DATA = json.loads(F.read_text(encoding="utf-8"))
VB = DATA["visual_bible"]
GENERIC = ("figure", "someone", "a man", "a woman", "a person", "a silhouette")


class RewriteTests(unittest.TestCase):
    def test_the_twelve_actions_of_this_project(self):
        self.assertEqual(len(DATA["shots"]), 12)
        for shot in DATA["shots"]:
            with self.subTest(shot=shot["shot_id"]):
                after = shots_module.rewrite_action(shot["action"], shot["characters"], VB, False)
                self.assertNotEqual(after, shot["action"])
                lowered = after.lower()
                for word in GENERIC:
                    self.assertNotIn(word, lowered)

    def test_adjectives_survive(self):
        by_id = {shot["shot_id"]: shot for shot in DATA["shots"]}
        rewrite = lambda sid: shots_module.rewrite_action(by_id[sid]["action"], by_id[sid]["characters"], VB, False)
        self.assertTrue(rewrite("s02_01").startswith("The man, alone, small inside the towering"))
        self.assertIn("the dark-coated man", rewrite("s06_05"))
        self.assertTrue(rewrite("s07_03").startswith("The woman, alone, stands in sharp silhouette"))
        self.assertTrue(rewrite("s07_06").startswith("The man's face"))
        both = rewrite("s07_05")
        self.assertIn("the seated man's shoulder", both)
        self.assertIn("the woman stands", both)

    def test_generic_words_stay_with_others_present(self):
        action = "A lone figure waits at the far door."
        self.assertEqual(shots_module.rewrite_action(action, ["thomas_wren"], VB, True), action)

    def test_no_cast_and_unlisted_gender_stay(self):
        self.assertEqual(shots_module.rewrite_action("A man waits.", [], VB, False), "A man waits.")
        self.assertEqual(shots_module.rewrite_action("A woman waits.", ["thomas_wren"], VB, False), "A woman waits.")

    def test_scenery_silhouettes_stay(self):
        action = "The man looks at a silhouette of the moor; dark coat framing her silhouette."
        self.assertEqual(shots_module.rewrite_action(action, ["thomas_wren"], VB, False), action)

    def test_someone_and_names_when_two_share_a_noun(self):
        vb = {"characters": [dict(VB["characters"][0]), dict(VB["characters"][0], character_id="tom", name="Tom Hale")]}
        self.assertEqual(shots_module.rewrite_action("A figure waits. Someone turns.", ["thomas_wren", "tom"], vb, False),
                         "Thomas waits. Tom turns.")
        self.assertEqual(shots_module.rewrite_action("Someone waits.", ["thomas_wren"], VB, False), "The man waits.")

    def test_the_planner_rewrites_what_slips_through(self):
        segment = {"shot_id": "s02_01", "scene_id": "s02", "section_id": 2, "start": 0, "end": 5, "text": "He walks."}
        with tempfile.TemporaryDirectory() as temp, patch("builtins.print"):
            entry = shots_module._entry(segment, {"shot_type": "wide", "characters": ["thomas_wren"], "others_present": False,
                                                  "action": "A lone figure walks.", "face_visibility": "none"},
                                        {}, VB, Path(temp), None)
        self.assertEqual(entry["action"], "The man, alone, walks.")
        self.assertEqual(entry["wording_rewritten_from"], "A lone figure walks.")

    def test_the_contract_states_the_rule(self):
        self.assertIn("never with a\ngeneric person word", shots_module._scene_contract("reference"))


class HeadcountTests(unittest.TestCase):
    def setUp(self):
        keep = patch.object(config, "FACELESS_PORTRAIT_REFERENCE", {"faceless": "keep"})
        keep.start()
        self.addCleanup(keep.stop)

    def shot(self, **fields):
        return {"action": "he waits", "characters": ["thomas_wren"], "others_present": False, "scene_id": "s02",
                "shot_type": "medium", "face_visibility": "full", **fields}

    def compose(self, **fields):
        return shots_module.compose_shot(self.shot(**fields), VB, ".", assume_references=True)

    def test_faceless_shots_state_the_headcount_and_the_framing(self):
        prompt = self.compose(shot_type="wide", face_visibility="none")["prompt"]
        self.assertIn("The only person in the image is the man from the first image, seen small and from behind, far from the viewer.", prompt)
        self.assertIn("backlit, a dark shape against the bright background", self.compose(shot_type="silhouette", face_visibility="none")["prompt"])
        self.assertIn("seen from behind, over the shoulder", self.compose(shot_type="over_the_shoulder")["prompt"])
        self.assertIn("the face not visible", self.compose(shot_type="close_up", face_visibility="none")["prompt"])

    def test_a_visible_face_keeps_the_usual_headcount(self):
        self.assertNotIn("The only person in the image", self.compose(shot_type="medium", face_visibility="full")["prompt"])

    def test_people_in_the_room_keep_the_usual_headcount(self):
        self.assertNotIn("The only person in the image", self.compose(shot_type="wide", others_present=True)["prompt"])


class ReferenceChoiceTests(unittest.TestCase):
    def compose(self, shot_type, face="none"):
        shot = {"action": "he waits", "characters": ["thomas_wren"], "others_present": False, "scene_id": "s02",
                "shot_type": shot_type, "face_visibility": face}
        return shots_module.compose_shot(shot, VB, ".", assume_references=True)

    def test_choice_follows_the_config(self):
        with patch.object(config, "FACELESS_PORTRAIT_REFERENCE", {"wide": "none", "silhouette": "keep", "faceless": "none"}):
            wide = self.compose("wide")
            silhouette = self.compose("silhouette")
            close = self.compose("close_up")
            visible = self.compose("medium", "full")
        self.assertFalse([slot for slot in wide["slots"] if slot["kind"] == "character"])
        self.assertTrue([slot for slot in silhouette["slots"] if slot["kind"] == "character"])
        self.assertFalse([slot for slot in close["slots"] if slot["kind"] == "character"])
        self.assertTrue([slot for slot in visible["slots"] if slot["kind"] == "character"])
        self.assertIn("square jaw", wide["prompt"])               # text identity, without the portrait


class ReplanTests(unittest.TestCase):
    def project(self, temp):
        folder = Path(temp)
        shots = [dict(shot, scene_id=shot["shot_id"][:3], others_present=False, image_path="images/x.jpg", prompt="old",
                      reference_slots=[]) for shot in DATA["shots"][:2]]
        shots.append({"shot_id": "s99_01", "scene_id": "s99", "characters": ["thomas_wren"], "others_present": True,
                      "action": "A figure passes.", "shot_type": "wide", "prompt": "keep"})
        atomic_write_json(folder / shots_module.FILE_NAME, {"shots": shots, "source_hash": "h"})
        atomic_write_json(folder / vbible.FILE_NAME, VB)
        return folder

    def test_dry_run_and_real_run(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = self.project(temp)
            before = (folder / shots_module.FILE_NAME).read_bytes()
            self.assertEqual(len(replan_shots.rewrite_project(folder, dry_run=True)), 2)
            self.assertEqual((folder / shots_module.FILE_NAME).read_bytes(), before)
            with patch("agents.gemini_client.generate", side_effect=AssertionError("no Gemini")):
                changed = replan_shots.rewrite_project(folder)
            saved = load_json(folder / shots_module.FILE_NAME)
        self.assertEqual([row["shot_id"] for row in changed], [DATA["shots"][0]["shot_id"], DATA["shots"][1]["shot_id"]])
        self.assertEqual(saved["source_hash"], "h")
        self.assertEqual(saved["shots"][0]["image_path"], "images/x.jpg")
        self.assertNotEqual(saved["shots"][0]["prompt"], "old")
        self.assertEqual(saved["shots"][2]["prompt"], "keep")
        self.assertEqual(saved["shots"][2]["action"], "A figure passes.")


if __name__ == "__main__":
    unittest.main()
