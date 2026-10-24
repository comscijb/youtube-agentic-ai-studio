"""Phase 3.3-B: dread from light and scale, never injuries, decay, tears or guests the text lacks."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import identity
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyClient, ImageGenerator

SEGMENTS = [{"shot_id": "s01_01", "text": "Julian aligned the decanters two hours before the dinner."},
            {"shot_id": "s01_02", "text": "Elena sat at the counter, her throat bare."}]


def plan(changes=None):
    shots = [{"shot_id": "s01_01", "shot_type": "wide", "characters": ["julian_vance"], "location_id": "loft",
              "action": "He aligns two decanters on the long table", "face_visibility": "partial",
              "reuse_of": "", "appearance": {"julian_vance": "neatly groomed, intent"}, "others_present": False},
             {"shot_id": "s01_02", "shot_type": "medium", "characters": ["elena_vance"], "location_id": "loft",
              "action": "She sits at the counter beside a velvet box", "face_visibility": "partial",
              "reuse_of": "", "appearance": {"elena_vance": "poised, hair neatly pinned, composed"},
              "others_present": False}]
    value = {"settings": {"loft": "immaculate and expensive: polished surfaces, pristine walls"}, "shots": shots}
    for (index, key), new in (changes or {}).items():
        if index is None:
            value[key] = new
        else:
            shots[index][key] = new
    return value


def validate(value):
    return shots_module.validate_shots(value, SEGMENTS, VISUAL_BIBLE, {}, "reference")


class StyleWordingTests(unittest.TestCase):
    def test_crimson_examples_drop_the_wound_but_keep_the_style_hash(self):
        suffix = identity.style_suffix("dark_illustration", "engraving")
        self.assertNotIn("wound", suffix)
        self.assertIn("(wine, a ring, a scarf, a candle reflection)", suffix)
        # Approved references, style anchors and the visual bible carry this hash: the
        # Phase 3.3 wording change must not invalidate them.
        self.assertEqual(identity.style_hash("dark_illustration", "engraving"), "d043a627229faba8")

    def test_rules_put_dread_in_light_not_decay(self):
        rules = identity.image_prompt_rules("dark_illustration")
        self.assertIn("Dread comes only from light, shadow, scale and stillness", rules)
        self.assertIn("immaculate", rules)
        self.assertIn("tears only where the text has them", rules)

    def test_unsupported_terms_need_the_narration(self):
        self.assertEqual(identity.unsupported_terms("bloody scratches on her arms", "She smiled."),
                         ["bloody", "scratches"])
        self.assertEqual(identity.unsupported_terms("a grimy, decayed warehouse", ""), ["grimy", "decayed"])
        self.assertEqual(identity.unsupported_terms("tearful eyes", "Tears ran down her face."), [])
        self.assertEqual(identity.unsupported_terms("a cut on his palm", "the knife slipped and the wound opened"), [])
        for fine in ("stainless steel shelving", "scratchboard hatching", "crystal decanter",
                     "single full-bleed illustration", "rustic oak", "a gentle tear-free smile"):
            self.assertEqual(identity.unsupported_terms(fine, ""), [], fine)


class PlannerRuleTests(unittest.TestCase):
    def test_valid_plan_records_setting_condition(self):
        shots = validate(plan())
        self.assertEqual(shots[0]["setting_condition"], "immaculate and expensive: polished surfaces, pristine walls")

    def test_rejections(self):
        cases = {
            "never mentions ['scratches']": plan({(1, "action"): "She sits with scratches on her arms"}),
            "never mentions ['tearful']": plan({(1, "appearance"): {"elena_vance": "dishevelled and tearful"}}),
            "others_present is false": plan({(0, "action"): "Men in suits wait behind him"}),
            "appearance needs grooming": plan({(0, "appearance"): {}}),
            "others_present must be true or false": plan({(0, "others_present"): None}),
            "settings.loft: the narration never mentions ['grimy']":
                plan({(None, "settings"): {"loft": "a grimy warehouse"}}),
            "settings must map": plan({(None, "settings"): None}),
        }
        for expected, value in cases.items():
            with self.subTest(expected=expected), self.assertRaises(NarrativeValidationError) as caught:
                validate(value)
            self.assertIn(expected, str(caught.exception))

    def test_guests_are_allowed_when_present(self):
        value = plan({(0, "action"): "A few guests wait in the far shadows", (0, "others_present"): True})
        self.assertEqual(len(validate(value)), 2)


class ComposeTests(unittest.TestCase):
    def shot(self, **kwargs):
        value = {"shot_id": "s01_02", "scene_id": "s01", "shot_type": "medium", "characters": ["elena_vance"],
                 "location": "loft", "action": "She sits at the counter", "face_visibility": "partial",
                 "appearance": {"elena_vance": "poised, hair neatly pinned, composed expression"},
                 "others_present": False, "setting_condition": "immaculate: polished surfaces, pristine walls",
                 "seed": 3, "seed_offset": 0}
        value.update(kwargs)
        return value

    def test_wardrobe_grooming_condition_and_solitude_reach_the_prompt(self):
        vb = json.loads(json.dumps(VISUAL_BIBLE))
        vb["characters"][1]["wardrobe"][0]["grooming"] = "light make-up"
        prompt = shots_module.compose_shot(self.shot(), vb, "unused", assume_references=True)["prompt"]
        self.assertIn("the woman from the first image, wearing an ivory silk shirt, light make-up, "
                      "poised, hair neatly pinned, composed expression", prompt)
        self.assertIn("vaulted concrete loft, blackened oak table, smoked decanters, immaculate", prompt)
        self.assertIn("Alone in the room.", prompt)
        self.assertNotIn(shots_module.CROWD_HINT, prompt)

    def test_two_alone_empty_room_and_guests(self):
        two = self.shot(characters=["julian_vance", "elena_vance"])
        self.assertIn("The two of them alone in the room.",
                      shots_module.compose_shot(two, VISUAL_BIBLE, "unused")["prompt"])
        empty = self.shot(characters=[], shot_type="insert")
        self.assertIn("The room is empty and still.",
                      shots_module.compose_shot(empty, VISUAL_BIBLE, "unused")["prompt"])
        party = self.shot(others_present=True, action="She watches the guests")
        self.assertIn(shots_module.CROWD_HINT, shots_module.compose_shot(party, VISUAL_BIBLE, "unused")["prompt"])


class PipelineTests(unittest.TestCase):
    def test_contract_and_version_bump_replan_old_shot_lists(self):
        self.assertEqual(shots_module.SHOT_LIST_VERSION, 2)
        from tests.test_shot_planning import OUTLINE, SCRIPT, ShotListTests, planned_shots
        case = ShotListTests("test_budget_is_applied_before_planning")
        case.setUp()
        try:
            atomic_write_json(case.root / shots_module.FILE_NAME, {"version": 1, "source_hash": "old", "shots": []})
            payload = json.dumps(planned_shots(case.segments()))
            with patch.object(shots_module, "generate", side_effect=lambda *a, **k: payload) as call, \
                    redirect_stdout(io.StringIO()):
                result = shots_module.build_shot_list(SCRIPT, OUTLINE, VISUAL_BIBLE, case.root)
            prompt = call.call_args.args[0]
        finally:
            case.tearDown()
        for phrase in ("others_present", "appearance", "settings maps every location_id", "scene_setting"):
            self.assertIn(phrase, prompt)
        shot = result["shots"][0]
        self.assertEqual(result["version"], 2)
        self.assertFalse(shot["others_present"])
        self.assertIn("neatly groomed", shot["prompt"])
        self.assertIn("pristine walls", shot["prompt"])

    def test_generation_refuses_a_prompt_with_unstated_injuries(self):
        vb = json.loads(json.dumps(VISUAL_BIBLE))
        vb["characters"][0]["wardrobe"][0]["outfit"] = "a charcoal suit, bloody scratches on his knuckles"
        shot = {"shot_id": "s01_01", "scene_id": "s01", "section_id": 1, "start": 0, "end": 8,
                "narration_excerpt": "He set the table.", "shot_type": "insert", "characters": [],
                "location": "loft", "action": "A decanter on the table", "face_visibility": "none",
                "reuse_of": "", "seed": 1, "seed_offset": 0, "others_present": False}
        hands = dict(shot, shot_id="s01_02", shot_type="hands", characters=["julian_vance"],
                     face_visibility="none", appearance={"julian_vance": "composed"})
        fake = FakeComfy()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            atomic_write_json(Path(temp, vbible.FILE_NAME), vb)
            atomic_write_json(Path(temp, shots_module.FILE_NAME), {"version": 2, "shots": [shot, hands]})
            with patch.object(gen, "missing_approvals", return_value=[]):
                summary = gen.generate_shots(temp, ImageGenerator(temp, ComfyClient(
                    "http://127.0.0.1:8188", session=fake, sleep=lambda _: None)))
            saved = load_json(Path(temp, shots_module.FILE_NAME))["shots"]
        self.assertEqual(summary["generated"], 1)
        self.assertEqual(summary["failed"], ["s01_02"])
        self.assertEqual(len(fake.prompts), 1)
        self.assertNotIn("image_path", saved[1])

    def test_gui_rejects_unstated_injuries(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            atomic_write_json(Path(temp, "shot_list.json"), {"shots": [
                {"shot_id": "s01_01", "action": "x", "narration_excerpt": "She sat at the counter."}]})
            client = gui.app.test_client()
            bad = client.post("/api/imagegen/shot/s01_01/prompt", json={"action": "Blood on her sleeve"})
            good = client.post("/api/imagegen/shot/s01_01/prompt", json={"action": "She sits, composed"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("blood", bad.get_json()["error"].lower())
        self.assertEqual(good.status_code, 200)


if __name__ == "__main__":
    unittest.main()
