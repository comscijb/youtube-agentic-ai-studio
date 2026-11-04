"""Phase 3.3.2: identity-only references — clean descriptors and outfits, neutral character sheets."""
import config
from unittest.mock import patch
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image

from agents import identity
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import BIBLE, OUTLINE, VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator

TUNIC = "a soft olive linen tunic top, loose dark linen trousers"
SILK = "an ivory silk button-down shirt with a crisp folded collar, tailored charcoal trousers"


def episode_vb():
    """The Phase 3.3.2 evidence, cleaned: Elena's tunic in s01-s02, the ivory silk shirt from s03."""
    vb = json.loads(json.dumps(VISUAL_BIBLE))
    vb["characters"][1]["wardrobe"] = [{"scene_ids": ["s01", "s02"], "outfit": TUNIC},
                                       {"scene_ids": ["s03", "s04", "s05"], "outfit": SILK}]
    vb["locations"][0]["scene_ids"] = ["s01", "s02", "s03", "s04", "s05"]
    return vb


class VisualBibleRuleTests(unittest.TestCase):
    def evidence(self):
        value = json.loads(json.dumps(VISUAL_BIBLE))
        value["characters"][1]["descriptor"] = ("35-year-old woman of mixed Mediterranean descent, weary "
                                                "expressive dark eyes, dark chestnut hair loosely pinned in "
                                                "a low knot with escaping wisps")
        value["characters"][1]["wardrobe"][0]["outfit"] = (
            "Soft olive earthy linen tunic top, bare hands with faint traces of dark soil on knuckles")
        return value

    def test_moods_in_descriptors_and_body_states_in_outfits_are_rejected(self):
        with self.assertRaises(NarrativeValidationError) as caught:
            vbible.validate_visual_bible(self.evidence(), BIBLE, OUTLINE)
        message = str(caught.exception)
        self.assertIn("'weary'", message)
        self.assertIn("'escaping wisps'", message)
        for word in ("'hands'", "'traces of'", "'soil'", "'knuckles'"):
            self.assertIn(word, message)

    def test_a_scar_is_allowed_only_when_the_screenplay_has_one(self):
        value = json.loads(json.dumps(VISUAL_BIBLE))
        value["characters"][0]["descriptor"] += ", scarred left eyebrow"
        with self.assertRaises(NarrativeValidationError):
            vbible.validate_visual_bible(value, BIBLE, OUTLINE, "He smiled.")
        vbible.validate_visual_bible(value, BIBLE, OUTLINE, "The old scar above his eyebrow whitened.")

    def test_contract_and_version(self):
        import inspect
        self.assertGreaterEqual(vbible.VISUAL_BIBLE_VERSION, 2)
        source = inspect.getsource(vbible.build_visual_bible)
        self.assertIn("physical identity only", source)
        self.assertIn("clothing and accessories only", source)


class CharacterSheetTests(unittest.TestCase):
    def test_sheet_prompt_carries_identity_only(self):
        prompt = gen.portrait_prompt(episode_vb(), "elena_vance", gen.PORTRAIT_VIEWS[1])
        self.assertIn("head and shoulders", prompt)
        self.assertIn("in a three-quarter view", prompt)
        self.assertIn("plain dark top", prompt)
        self.assertIn("Plain flat dark-grey backdrop", prompt)
        for scene_detail in ("tunic", "silk", "fog", "smoke", "staircase", "towering", "negative space"):
            self.assertNotIn(scene_detail, prompt)
        self.assertNotIn("smoke", identity.sheet_style_suffix())
        self.assertEqual(identity.style_hash("dark_illustration", "engraving"), "d043a627229faba8")

    def test_old_sheets_need_re_approval_and_only_stale_references_are_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            vb = episode_vb()
            for kind, ident, folder in (("character", "elena_vance", "character_refs"),
                                        ("location", "loft", "location_refs")):
                (output / folder).mkdir()
                Image.new("RGB", (32, 32)).save(output / folder / f"{ident}.png")
                vb.setdefault("references", {})[f"{kind}:{ident}"] = {
                    "path": f"{folder}/{ident}.png", "seed": 1, "prompt": "", "style_hash": identity.style_hash()}
            atomic_write_json(output / vbible.FILE_NAME, vb)
            self.assertIsNone(vbible.approved_reference(vb, "character", "elena_vance", output))   # sheet v1
            self.assertTrue(vbible.approved_reference(vb, "location", "loft", output))           # still valid
            fake = FakeComfy()
            with redirect_stdout(io.StringIO()):
                gen.generate_reference_candidates(vb, output, ImageGenerator(output, ComfyClient(
                    "http://127.0.0.1:8188", session=fake, sleep=lambda _: None)), characters=["elena_vance"])
            self.assertEqual(len(fake.prompts), 4)                       # no new location candidates
            self.assertTrue((output / "location_refs" / "loft.png").is_file())
            self.assertFalse((output / "character_refs" / "elena_vance.png").exists())
            self.assertEqual([path.name for path in (output / "character_refs" / "archive").rglob("*.png")],
                             ["elena_vance.png"])
            record = load_json(output / gen.CANDIDATES_FILE)["character:elena_vance"]
            self.assertTrue(all(item["sheet_version"] == vbible.CHARACTER_SHEET_VERSION for item in record))
            gen.approve_reference(vb, "character", "elena_vance", 2, output)
            self.assertTrue(vbible.approved_reference(load_json(output / vbible.FILE_NAME), "character",
                                                      "elena_vance", output))


@patch.object(config, "FACELESS_PORTRAIT_REFERENCE", {"faceless": "keep"})  # composition with the portrait
class ShotWardrobeAndPeopleTests(unittest.TestCase):
    def shot(self, scene, **extra):
        value = {"shot_id": f"{scene}_01", "scene_id": scene, "shot_type": "medium",
                 "characters": ["elena_vance"], "location": "loft", "action": "She waits by the window",
                 "face_visibility": "partial", "appearance": {"elena_vance": "poised, composed"},
                 "others_present": False, "seed": 1, "seed_offset": 0}
        value.update(extra)
        return value

    def test_each_scene_wears_its_own_outfit(self):
        vb = episode_vb()
        for scene in ("s01", "s02"):
            self.assertIn("olive linen tunic", shots_module.compose_shot(self.shot(scene), vb, "unused",
                                                                         assume_references=True)["prompt"])
        for scene in ("s03", "s04", "s05"):
            prompt = shots_module.compose_shot(self.shot(scene), vb, "unused", assume_references=True)["prompt"]
            self.assertIn("ivory silk button-down shirt", prompt)
            self.assertNotIn("tunic", prompt)
            self.assertIn("poised, composed", prompt)

    def test_every_shot_without_others_says_who_is_alone(self):
        vb = episode_vb()
        for shot_type in shots_module.SHOT_TYPES:
            for cast in ([], ["elena_vance"], ["julian_vance", "elena_vance"]):
                with self.subTest(shot_type=shot_type, cast=cast):
                    shot = self.shot("s03", shot_type=shot_type, characters=cast,
                                     appearance={cid: "composed" for cid in cast})
                    prompt = shots_module.compose_shot(shot, vb, "unused", assume_references=True)["prompt"]
                    expected = {0: "The room is empty and still.",
                                1: "Exactly one person in the scene: the woman from the first image.",
                                2: "Exactly two people in the scene: the man from the first image and the "
                                   "woman from the second image; nobody else is present."}[len(cast)]
                    self.assertIn(expected, prompt)

    def test_over_the_shoulder_names_whose_shoulder_and_who_faces_camera(self):
        shot = self.shot("s03", shot_type="over_the_shoulder", characters=["julian_vance", "elena_vance"],
                         appearance={"julian_vance": "tense", "elena_vance": "composed"})
        prompt = shots_module.compose_shot(shot, episode_vb(), "unused", assume_references=True)["prompt"]
        self.assertIn("seen over the shoulder of the man from the first image; the woman from the second "
                      "image faces the viewer", prompt)
        self.assertIn("Exactly two people in the scene", prompt)
        alone = shots_module.compose_shot(dict(shot, characters=["julian_vance"]), episode_vb(), "unused")
        self.assertIn("seen over the shoulder of the man with horn-rimmed glasses, the only person in the frame",
                      alone["prompt"])


if __name__ == "__main__":
    unittest.main()
