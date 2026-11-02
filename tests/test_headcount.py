"""Phase 3.4-C: every prompt states the exact number and identity of the people in the frame."""
import unittest

from agents import shot_list as shots_module
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE


def shot(characters, **extra):
    value = {"shot_id": "s01_01", "scene_id": "s01", "shot_type": "medium", "characters": characters,
             "location": "loft", "action": "They wait", "face_visibility": "partial",
             "appearance": {cid: "composed" for cid in characters}, "others_present": False,
             "seed": 1, "seed_offset": 0}
    value.update(extra)
    return value


class HeadcountTests(unittest.TestCase):
    def test_sentences(self):
        self.assertEqual(shots_module.headcount([]), "The room is empty and still.")
        self.assertEqual(shots_module.headcount(["the man from the first image"]),
                         "Exactly one person in the scene: the man from the first image.")
        self.assertEqual(shots_module.headcount(["the man from the first image", "the woman from the second image"]),
                         "Exactly two people in the scene: the man from the first image and the woman from the "
                         "second image; nobody else is present.")
        guests = shots_module.headcount(["the man from the first image"], others=True)
        self.assertTrue(guests.startswith("Exactly one person in the scene: the man from the first image, plus a few "
                                          "distant, indistinct guests in shadow"))

    def test_composed_prompts_count_the_listed_characters(self):
        two = shots_module.compose_shot(shot(["julian_vance", "elena_vance"]), VISUAL_BIBLE, "unused",
                                        assume_references=True)["prompt"]
        self.assertIn("Exactly two people in the scene: the man from the first image and the woman from the "
                      "second image; nobody else is present.", two)
        party = shots_module.compose_shot(shot(["julian_vance", "elena_vance"], others_present=True),
                                          VISUAL_BIBLE, "unused", assume_references=True)["prompt"]
        self.assertIn("second image, plus a few distant, indistinct guests in shadow", party)
        self.assertNotIn("nobody else is present", party)

    def test_over_the_shoulder_never_adds_a_third_body(self):
        prompt = shots_module.compose_shot(shot(["julian_vance", "elena_vance"], shot_type="over_the_shoulder"),
                                           VISUAL_BIBLE, "unused", assume_references=True)["prompt"]
        self.assertIn("seen over the shoulder of the man from the first image; the woman from the second image "
                      "faces the viewer", prompt)
        self.assertIn("Exactly two people in the scene", prompt)


if __name__ == "__main__":
    unittest.main()
