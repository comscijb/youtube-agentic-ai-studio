"""Phase 3.9.8: the referenced character, not a generic stand-in. No live calls (ComfyUI and Gemini mocked)."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import config
from agents import identity, sheet_reading
from agents import shot_list as shots_module
from agents import visual_bible as vbible
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import shot_generation as shotgen
from video.qa import decision, deterministic, identity_check, policy

FIXTURES = Path("tests/fixtures/identity")
DESCRIPTOR = ("a 39-year-old Caucasian woman with a slender build, oval face shape, hazel eyes, shoulder-length straight "
              "auburn hair, and a soft angular jawline")
VB = {"characters": [
    {"character_id": "hannah_price", "name": "Hannah", "presence": "on_screen", "descriptor": DESCRIPTOR,
     "signature_markers": ["auburn shoulder-length hair", "thin silver wristwatch"],
     "wardrobe": [{"scene_ids": ["s00", "s05", "s06"], "outfit": "cream button-front knit cardigan", "grooming": "hair neatly brushed shoulder-length"},
                  {"scene_ids": ["s02"], "outfit": "cream crewneck sweater and charcoal trousers", "grooming": "hair pulled back neatly, composed bearing"}]},
    {"character_id": "leo_price", "name": "Leo", "presence": "on_screen", "descriptor": "a 12-year-old boy with short dark brown hair",
     "signature_markers": [], "wardrobe": []},
    {"character_id": "priya", "presence": "on_screen", "descriptor": "a woman", "signature_markers": [], "wardrobe": []}],
    "locations": [{"location_id": "kitchen", "descriptor": "a vast immaculate kitchen", "scene_ids": ["s02"], "interior": True}],
    "references": {}}


def shot(**overrides):
    base = {"shot_id": "s02_01", "scene_id": "s02", "shot_type": "medium", "characters": ["hannah_price"], "location": "kitchen",
            "action": "Hannah stands still by the island", "face_visibility": "full", "others_present": False, "seed": 3}
    return {**base, **overrides}


def refs(kinds=("character", "body", "location")):
    def approved(vb, kind, ident, output):
        return f"{kind}_{ident}.png" if kind in kinds else None
    return approved


class ReferenceChoiceTests(unittest.TestCase):
    def compose(self, item, kinds=("character", "body", "location"), boards=("board.jpg",)):
        with patch.object(vbible, "approved_reference", side_effect=refs(kinds)), \
                patch("agents.style_refs.approved_paths", return_value=list(boards)), \
                patch("agents.style_refs.empty_paths", return_value=list(boards)), \
                patch.object(config, "FACELESS_PORTRAIT_REFERENCE", {"wide": "body", "silhouette": "body", "over_the_shoulder": "body", "faceless": "body"}):
            return shots_module.compose_shot(item, deepcopy(VB), "out")

    def kinds(self, composed):
        return [(slot["kind"], slot.get("id")) for slot in composed["slots"]]

    def test_close_up_face_wide_body_silhouette_body(self):
        self.assertEqual(self.kinds(self.compose(shot(shot_type="close_up")))[0], ("character", "hannah_price"))
        self.assertEqual(self.kinds(self.compose(shot(shot_type="wide", face_visibility="partial")))[0], ("body", "hannah_price"))
        self.assertEqual(self.kinds(self.compose(shot(shot_type="silhouette", face_visibility="none")))[0], ("body", "hannah_price"))
        self.assertEqual(self.kinds(self.compose(shot(shot_type="over_the_shoulder", face_visibility="partial")))[0], ("body", "hannah_price"))

    def test_medium_partial_sends_both_after_the_location_and_before_the_style(self):
        composed = self.compose(shot(face_visibility="partial"))
        self.assertEqual([kind for kind, _ in self.kinds(composed)], ["character", "location", "body", "style"])
        self.assertIn("Her full figure, hair and build as in the third image.", composed["prompt"])

    def test_four_slots_drop_the_style_anchor_then_the_extra_sheets(self):
        three = self.compose(shot(characters=["hannah_price", "leo_price", "priya"], face_visibility="partial"))
        self.assertEqual([kind for kind, _ in self.kinds(three)], ["character", "character", "character", "location"])
        two = self.compose(shot(characters=["hannah_price", "leo_price"], face_visibility="partial"))
        self.assertEqual([kind for kind, _ in self.kinds(two)], ["character", "character", "location", "body"])

    def test_no_body_sheet_yet_keeps_the_old_faceless_behaviour(self):
        composed = self.compose(shot(shot_type="wide", face_visibility="partial"), kinds=("character", "location"))
        self.assertNotIn("character", [kind for kind, _ in self.kinds(composed)])

    def test_with_a_reference_the_text_never_redescribes_face_or_hair(self):
        with_ref = self.compose(shot())["prompt"]
        self.assertNotIn("hazel eyes", with_ref)
        self.assertNotIn("shoulder-length", with_ref)
        self.assertNotIn("auburn", with_ref)
        self.assertIn("thin silver wristwatch", with_ref)            # a marker the sheet cannot carry stays
        self.assertIn("the woman from the first image", with_ref)
        text_only = self.compose(shot(), kinds=(), boards=())["prompt"]
        self.assertIn("hazel eyes", text_only)
        self.assertIn("auburn shoulder-length hair", text_only)

    def test_the_body_sheet_carries_the_default_outfit_so_a_scene_outfit_is_a_change(self):
        prompt = self.compose(shot(shot_type="wide", face_visibility="partial"))["prompt"]
        self.assertIn("now wearing cream crewneck sweater", prompt)


class BodySheetTests(unittest.TestCase):
    def test_the_job_uses_the_scene_drawing_and_the_default_wardrobe(self):
        job = shotgen._body_job(VB, "hannah_price", 1, Path("out"))
        self.assertIn("from head to feet", job.prompt)
        self.assertIn("cream button-front knit cardigan", job.prompt)          # the outfit worn in most scenes
        self.assertIn(identity.sheet_style_suffix(), job.prompt)
        self.assertNotIn("gaunt", job.prompt.lower())
        self.assertNotIn("gaunt", identity.sheet_style_suffix().lower())
        self.assertEqual(job.size, shotgen.BODY_SIZE)
        self.assertTrue(job.output_path.replace("\\\\", "/").endswith("hannah_price_body_1.jpg"))

    def test_approval_writes_its_own_file(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "character_refs/candidates").mkdir(parents=True)
            Image.new("RGB", (64, 96), (90, 90, 90)).save(output / "character_refs/candidates/hannah_price_body_2.jpg")
            json.dump({"body:hannah_price": [{"number": 2, "path": "character_refs/candidates/hannah_price_body_2.jpg", "seed": 9, "prompt": "p",
                                             "style_hash": identity.style_hash()}]}, open(output / shotgen.CANDIDATES_FILE, "w", encoding="utf-8"))
            vb = shotgen.approve_reference(deepcopy(VB), "body", "hannah_price", 2, output)
            self.assertTrue((output / "character_refs/hannah_price_body.png").is_file())
            self.assertEqual(vbible.approved_reference(vb, "body", "hannah_price", output), str(output / "character_refs/hannah_price_body.png"))


class ReconcileTests(unittest.TestCase):
    READING = {"hair_length": "long", "hair_colour": "red", "hair_style": "straight", "eye_colour": "brown", "age_range": "45-50"}

    def test_the_vision_reading_is_one_call_with_the_image(self):
        calls = []

        def generator(prompt, model, **kwargs):
            calls.append(kwargs)
            return json.dumps(self.READING)

        reading = sheet_reading.read_sheet(FIXTURES / "hannah_sheet.png", generator=generator)
        self.assertEqual(reading["eye_colour"], "brown")
        self.assertEqual(reading["provenance"]["source"], "gemini-vision")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["images"][0][1], "image/jpeg")

    def test_differences_against_the_descriptor_and_the_markers(self):
        found = sheet_reading.differences(DESCRIPTOR, VB["characters"][0]["signature_markers"], self.READING)
        fields = [item["field"] for item in found]
        self.assertEqual(fields, ["hair_length", "eye_colour", "age", "marker"])
        self.assertIn("olhos — descritor: avelã / folha: brown", [item["message"] for item in found])
        self.assertIn("auburn shoulder-length hair", found[-1]["descriptor"])
        self.assertEqual(sheet_reading.differences(DESCRIPTOR, [], {"hair_length": "shoulder", "hair_colour": "auburn", "eye_colour": "hazel",
                                                                    "age_range": "37-42"}), [])

    def test_accepting_keeps_both_values_and_silences_the_marker(self):
        vb = deepcopy(VB)
        entry = sheet_reading.apply(vb, "hannah_price", {**self.READING, "provenance": {"at": "t"}}, ["hair_length", "eye_colour", "age", "marker"])
        self.assertIn("long straight red hair", entry["descriptor"])
        self.assertIn("brown eyes", entry["descriptor"])
        self.assertIn("48-year-old", entry["descriptor"])
        self.assertEqual(entry["descriptor_history"][0]["descriptor"], DESCRIPTOR)
        self.assertEqual(entry["marker_conflicts"], ["auburn shoulder-length hair"])
        with patch.object(vbible, "approved_reference", side_effect=refs()), patch("agents.style_refs.approved_paths", return_value=[]):
            prompt = shots_module.compose_shot(shot(), vb, "out")["prompt"]
        self.assertNotIn("auburn shoulder-length hair", prompt)


@unittest.skipUnless(deterministic.Detector().available(), "layer-1 models missing (python -m scripts.setup_qa_models)")
class IdentityScoreTests(unittest.TestCase):
    def check(self):
        sheet = str(FIXTURES / "hannah_sheet.png")
        self.patcher = patch.object(vbible, "approved_reference", side_effect=lambda vb, kind, cid, out: sheet if kind == "character" else None)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        return identity_check.IdentityCheck(deterministic.Detector(), deepcopy(VB), ".", [])

    def test_a_matching_close_up_passes_and_the_generic_face_fails(self):
        check = self.check()
        good = check.score(FIXTURES / "hannah_closeup.jpg", ["hannah_price"])["hannah_price"]
        bad = check.score(FIXTURES / "generic_medium.jpg", ["hannah_price"])["hannah_price"]
        self.assertEqual(good["status"], "match")
        self.assertGreater(good["similarity"], identity_check.THRESHOLD + 0.2)
        self.assertEqual(bad["status"], "generic")
        self.assertLess(bad["similarity"], identity_check.THRESHOLD)

    def test_a_generic_face_is_blocking_once_then_advisory(self):
        result = {"hannah_price": {"status": "generic", "similarity": 0.45, "face_px": 95}}
        first = identity_check.issues(result, VB)
        self.assertEqual((first[0]["type"], first[0]["severity"]), ("identity", "blocking"))
        self.assertIn("personagem genérico: rosto não confere com a folha de Hannah", first[0]["note"])
        self.assertEqual(identity_check.issues(result, VB, regenerated=True)[0]["severity"], "advisory")
        self.assertEqual(decision.verdict({"issues": first})[0], "regenerate")
        self.assertEqual(policy.apply(first, "advisory", {})[0]["severity"], "blocking")      # acts on its own, even in advisory

    def test_generic_shots_are_listed_for_the_batch(self):
        shot_list = {"shots": [{"shot_id": "a", "qa": {"issues": identity_check.issues({"hannah_price": {"status": "generic", "similarity": .3}}, VB)}},
                               {"shot_id": "b", "qa": {"issues": []}}]}
        self.assertEqual(shotgen.generic_shots(shot_list), ["a"])


class HairCheckTests(unittest.TestCase):
    def test_colour_names(self):
        self.assertEqual(identity_check.colour_name((94, 54, 36)), "red")          # Hannah's sheet
        self.assertEqual(identity_check.colour_name((150, 116, 93)), "brown")      # the generic woman
        self.assertEqual(identity_check.colour_name((20, 18, 18)), "black")
        self.assertEqual(identity_check.colour_name((200, 170, 90)), "blond")
        self.assertEqual(identity_check.expected_colour("cream button-front knit cardigan"), "cream")

    def test_the_head_and_torso_regions_of_a_figure(self):
        image = np.zeros((200, 100, 3), dtype=np.uint8)
        image[20:40, 30:70] = (120, 40, 20)          # hair
        image[60:110, 30:70] = (230, 220, 200)       # sweater
        head, torso = identity_check.regions(image, [0.3, 0.1, 0.7, 0.95])
        self.assertEqual(identity_check.colour_name(identity_check._dominant(head)), "red")
        self.assertEqual(identity_check.colour_name(identity_check._dominant(torso)), "cream")


class FramingTests(unittest.TestCase):
    def test_from_behind_only_when_the_face_is_hidden_and_the_action_agrees(self):
        hidden = shot(shot_type="wide", face_visibility="none", action="Hannah stands motionless by the window")
        self.assertIn("seen small and from behind", shots_module.faceless_headcount(["the woman"], hidden))
        partial = shot(shot_type="wide", face_visibility="partial", action="Hannah stands motionless by the window")
        self.assertIn(shots_module.NEUTRAL_DISTANCE, shots_module.faceless_headcount(["the woman"], partial))
        for action in ("Hannah turning her head toward the door", "Hannah gently placing a cake", "Hannah smiles at the counter",
                       "Hannah looking at the photographs"):
            item = shot(shot_type="wide", face_visibility="none", action=action)
            self.assertTrue(shots_module.framing_conflict(item), action)
            self.assertIn(shots_module.NEUTRAL_DISTANCE, shots_module.faceless_headcount(["the woman"], item))

    def test_the_planner_resolves_and_logs_the_conflict(self):
        segment = {"shot_id": "s07_01", "scene_id": "s07", "section_id": 7, "start": 0, "end": 4, "text": "x"}
        planned = {"shot_type": "wide", "characters": ["hannah_price"], "location_id": "kitchen", "face_visibility": "none",
                   "action": "Hannah stands at the island, gently placing a simple frosted cake"}
        with patch("builtins.print") as printed, patch.object(vbible, "approved_reference", return_value=None), \
                patch("agents.style_refs.approved_paths", return_value=[]):
            entry = shots_module._entry(segment, planned, {}, deepcopy(VB), Path("out"), None)
        self.assertEqual(entry["face_visibility"], "partial")
        self.assertEqual(entry["framing_resolved"], "placing")
        self.assertNotIn("from behind", entry["prompt"])
        self.assertTrue(any("contradicts" in str(call) for call in printed.call_args_list))

    def test_silhouette_wording(self):
        self.assertIn("backlit", shots_module.FRAMING["silhouette"])
        self.assertIn("face and clothes in shadow", shots_module.FRAMING["silhouette"])
        self.assertIn("backlit", shots_module.faceless_headcount(["the woman"], shot(shot_type="silhouette", face_visibility="none")))

    def test_silhouette_luminance_check(self):
        dark = Image.new("L", (400, 300), 220)
        ImageDraw.Draw(dark).rectangle((180, 60, 220, 280), fill=20)
        person = [{"box": [0.45, 0.2, 0.55, 0.93]}]
        self.assertEqual(deterministic.silhouette_issue(dark.convert("RGB"), person), [])
        lit = Image.new("L", (400, 300), 60)
        ImageDraw.Draw(lit).rectangle((180, 60, 220, 280), fill=170)
        found = deterministic.silhouette_issue(lit.convert("RGB"), person)
        self.assertEqual(found[0]["severity"], "advisory")
        self.assertIn("silhueta clara demais", found[0]["note"])


if __name__ == "__main__":
    unittest.main()
