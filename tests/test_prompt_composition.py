"""Phase 3.4-B: clean prompts — one sentence per character, scene-scoped states, no repeats."""
import config
from unittest.mock import patch
import json
import unittest

from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import NarrativeValidationError
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import BIBLE, OUTLINE, VISUAL_BIBLE

JULIAN = ("37 years old, Caucasian, lean athletic build, sharp angular jawline, dark slate grey eyes, short "
          "slicked dark brown hair with a precise side parting, wearing architectural horn-rimmed glasses and "
          "a thin platinum band on his left ring finger.")
DRESSING_ROOM = ("Sleek interior dressing space bounded by floor-to-ceiling dark timber wardrobe panels, a "
                 "full-length illuminated mirror, and polished concrete flooring.")


def evidence_vb(clean_states: bool = False) -> dict:
    """The project's visual bible at s03_02 (Phase 3.4 evidence)."""
    elena_grooming = ("Hair pinned loosely; wet tear streaks on pale cheeks in scene s05; still and pale "
                      "complexion in scenes s06 and s07.")
    return {
        "characters": [
            {"character_id": "julian_vance", "descriptor": JULIAN, "signature_markers": ["horn-rimmed glasses"],
             "wardrobe": [{"scene_ids": ["s01", "s03", "s05"],
                           "outfit": "Tailored charcoal cashmere sweater over a dark collared shirt, fitted "
                                     "charcoal trousers, thin platinum wedding ring.",
                           "grooming": "Neatly combed hair, smooth shaven face, rigid upright posture."}]},
            {"character_id": "elena_vance",
             "descriptor": "35 years old, Caucasian, slender graceful build, oval face shape, warm almond-brown "
                           "eyes, long dark chestnut hair loosely pinned at the back.",
             "signature_markers": ["long dark chestnut hair"],
             "wardrobe": [{"scene_ids": ["s03", "s05"],
                           "outfit": "Ivory silk button-up shirt with a crisp folded collar, high-waisted dark "
                                     "tailored slacks.",
                           "grooming": "Hair pinned loosely" if clean_states else elena_grooming}],
             "scene_states": {"s05": "wet tear streaks on pale cheeks"} if clean_states else {}},
        ],
        "locations": [{"location_id": "dressing_room", "descriptor": DRESSING_ROOM, "scene_ids": ["s03", "s05"]}],
    }


S03_02 = {"shot_id": "s03_02", "scene_id": "s03", "shot_type": "wide", "face_visibility": "none",
          "characters": ["julian_vance", "elena_vance"], "location": "dressing_room",
          "action": "Two tall figures stand small within the vast, symmetrical dressing gallery, a man stepping "
                    "up behind the woman facing the illuminated full-length mirror",
          "appearance": {"julian_vance": "Neatly combed hair, smooth shaven face, rigid upright posture, "
                                         "charcoal cashmere sweater.",
                         "elena_vance": "Hair pinned loosely, pale complexion, stiff stance."},
          "others_present": False, "setting_condition": DRESSING_ROOM, "seed": 1, "seed_offset": 0}


def people_part(prompt: str) -> str:
    return prompt.split(" single full-bleed illustration", 1)[0]


@patch.object(config, "FACELESS_PORTRAIT_REFERENCE", {"faceless": "keep"})  # composition with the portrait
class S03_02Tests(unittest.TestCase):
    def test_the_evidence_shot_is_clean(self):
        prompt = shots_module.compose_shot(S03_02, evidence_vb(), "unused", assume_references=True)["prompt"]
        text = people_part(prompt)
        for leak in ("s05", "s06", "in scene", "tear", "still and pale complexion"):
            self.assertNotIn(leak, text)
        self.assertNotIn("37 years old", text)                     # the reference carries identity
        self.assertIn("the man from the first image", text)        # "his left ring finger" → a man
        self.assertIn("the man from the first image, with horn-rimmed glasses, wearing tailored charcoal "
                      "cashmere sweater over a dark collared shirt", text)
        self.assertEqual(text.count("Neatly combed hair"), 1)
        self.assertEqual(text.count("charcoal cashmere sweater"), 1)
        self.assertEqual(text.count("Hair pinned loosely"), 1)
        self.assertEqual(text.count("Sleek interior dressing space"), 1)
        self.assertLess(len(text), 1600)

    def test_the_scene_state_appears_only_in_its_scene(self):
        vb = evidence_vb(clean_states=True)
        s03 = shots_module.compose_shot(S03_02, vb, "unused", assume_references=True)["prompt"]
        s05 = shots_module.compose_shot(dict(S03_02, shot_id="s05_01", scene_id="s05"), vb, "unused",
                                        assume_references=True)["prompt"]
        self.assertNotIn("tear streaks", s03)
        self.assertIn("wet tear streaks on pale cheeks", s05)

    def test_without_a_reference_the_descriptor_is_used_once_with_its_markers(self):
        sentence = shots_module.character_sentence("julian_vance", evidence_vb(), "s03")
        self.assertTrue(sentence.startswith("37 years old"))
        self.assertEqual(sentence.lower().count("horn-rimmed glasses"), 1)   # already in the descriptor

    def test_helpers(self):
        self.assertEqual(shots_module.scene_scoped(
            "Hair pinned loosely; wet tear streaks on pale cheeks in scene s05; pale in scenes s06 and s07.",
            "s05"), "Hair pinned loosely, wet tear streaks on pale cheeks")
        self.assertEqual(shots_module._cap("one, two, three, four", 12), "one, two")


class VisualBibleV3Tests(unittest.TestCase):
    def test_markers_required_and_scene_ids_kept_out_of_outfits_and_grooming(self):
        value = json.loads(json.dumps(VISUAL_BIBLE))
        del value["characters"][0]["signature_markers"]
        value["characters"][1]["wardrobe"][0]["grooming"] = "Hair pinned; tear streaks in scene s05"
        value["characters"][2]["scene_states"] = {"s09": "pale"}
        with self.assertRaises(NarrativeValidationError) as caught:
            vbible.validate_visual_bible(value, BIBLE, OUTLINE)
        message = str(caught.exception)
        self.assertIn("julian_vance: signature_markers must list 1 to 3", message)
        self.assertIn("elena_vance: grooming for ['s01'] mentions scene ids", message)
        self.assertIn("marcus_croft: scene_states has unknown scene ids ['s09']", message)
        self.assertEqual(vbible.VISUAL_BIBLE_VERSION, 4)          # 4: presence (voice only, no body)

    def test_descriptors_must_name_a_gender_noun(self):
        # The evidence descriptors ("35 years old, Caucasian, ...") made shots say "the person
        # from the second image"; v3 requires "a 35-year-old woman".
        problems = vbible.identity_problems(evidence_vb())
        self.assertTrue(any("elena_vance: start the descriptor with the apparent age and a gender noun" in item
                            for item in problems))


if __name__ == "__main__":
    unittest.main()
