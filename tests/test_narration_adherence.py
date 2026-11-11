"""Phase 3.9.9: images that follow the narration. No live calls (Gemini, ComfyUI and the image API are mocked)."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import config
from agents import adherence, staging
from agents import shot_list as shots_module
from agents.narrative import NarrativeValidationError, atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import external_provider
from video.qa import image_adherence, removal

VB = {"characters": [{"character_id": "hannah", "presence": "on_screen", "descriptor": "a 39-year-old woman with long red hair"},
                     {"character_id": "leo", "presence": "on_screen", "descriptor": "a 12-year-old boy with short dark hair"}],
      "locations": [{"location_id": "supermarket_aisle", "name": "Supermarket aisle", "descriptor": "a supermarket aisle between tall shelves",
                     "scene_ids": ["s04"], "interior": True},
                    {"location_id": "kitchen", "name": "Kitchen", "descriptor": "a bright kitchen with an island", "scene_ids": ["s01"], "interior": True}],
      "references": {}}
SCENE4 = [("s04_05", "Hannah gently stepped between them, offering a polite smile until the shopper drifted away.", "Hannah steps in front of Leo"),
          ("s04_06", "In the car on the drive home, the quiet was heavy. Hannah looked at her son in the rearview mirror.", "Hannah's eyes in the mirror"),
          ("s04_07", "She remembered when he was six and lost his first baby tooth at the dinner table.", "Hannah stares ahead"),
          ("s04_10", "Leo turned his face toward the passenger window.", "Leo turned toward the window"),
          ("s04_11", "Her fingers tightening on the steering wheel.", "hands grip the wheel"),
          ("s04_13", "He just kept looking out at the passing trees.", "Framed from outside amidst towering trees")]


def scene4():
    return [{"shot_id": sid, "scene_id": "s04", "location": "supermarket_aisle", "narration_excerpt": text, "action": action,
             "characters": ["hannah", "leo"], "shot_type": "medium", "face_visibility": "partial", "others_present": False,
             "setting_condition": "a quiet retail aisle"} for sid, text, action in SCENE4]


class LocationGuardTests(unittest.TestCase):
    def test_car_words_against_the_supermarket_are_a_mismatch(self):
        shots = scene4()
        found = adherence.mismatch(shots[1], VB)
        self.assertEqual((found["category"], found["strong"]), ("car", True))
        self.assertIn("in the car", found["evidence"])
        self.assertIsNone(adherence.mismatch(shots[0], VB))

    def test_relocation_creates_the_car_interior_and_the_drive_goes_on(self):
        vb, shots = deepcopy(VB), scene4()
        changes = adherence.relocate(shots, vb, log=lambda _: None)
        self.assertEqual([change["shot_id"] for change in changes], ["s04_06", "s04_07", "s04_10", "s04_11", "s04_13"])
        car = next(item for item in vb["locations"] if item["location_id"] == "car_interior")
        self.assertTrue(car["enclosed"])
        self.assertIn("passenger", car["seating"])
        self.assertEqual(shots[5]["location"], "car_interior")                 # "looking out at the trees": continuity
        self.assertEqual(shots[1]["setting_condition"], "")                    # the old place's condition goes
        self.assertEqual(shots[0]["location"], "supermarket_aisle")

    def test_a_place_in_a_memory_only_asks_for_review(self):
        shot = {"shot_id": "x", "scene_id": "s03", "location": "kitchen", "action": "she stands",
                "narration_excerpt": "She had documented everything. The first lost tooth, every tearful tantrum in the hallway."}
        self.assertFalse(adherence.mismatch(shot, VB)["strong"])
        adherence.relocate([shot], deepcopy(VB), log=lambda _: None)
        self.assertEqual(shot["location"], "kitchen")
        self.assertIn("hallway", shot["location_review"])

    def test_the_outside_action_is_a_contradiction_and_is_fixed(self):
        vb, shots = deepcopy(VB), scene4()
        adherence.relocate(shots, vb, log=lambda _: None)
        self.assertTrue(adherence.contradictions(shots[5], vb)[0].startswith("inside/outside"))
        adherence.deterministic_fix(shots[5], vb, log=lambda _: None)
        self.assertIn("seen inside the car", shots[5]["action"])
        self.assertNotIn("Framed from outside", shots[5]["action"])

    def test_report_lists_without_changing(self):
        shots = scene4()
        found = adherence.report(shots, VB)
        self.assertEqual(len(found["relocations"]), 5)
        self.assertEqual(shots[1]["location"], "supermarket_aisle")
        self.assertEqual(found["new_locations"], ["car_interior"])


NARRATION = ("Two days later at the supermarket, a woman stopped them. Hannah stepped between them. "
             "In the car on the drive home, the quiet was heavy. Hannah looked at her son in the rearview mirror. "
             "Her fingers tightened on the steering wheel.")
SHEET = {"moments": [
    {"sentences": [0, 1], "location_id": "supermarket_aisle", "evidence": "at the supermarket",
     "characters": [{"id": "hannah", "position": "in the aisle", "posture": "standing", "holding": "", "evidence": "Hannah stepped between them"}],
     "absent": [], "relations": []},
    {"sentences": [2, 4], "location_id": "car_interior", "evidence": "In the car on the drive home",
     "characters": [{"id": "hannah", "position": "driver's seat", "posture": "seated", "holding": "", "evidence": "fingers tightened on the steering wheel"},
                    {"id": "leo", "position": "back seat", "posture": "seated", "holding": "", "evidence": "her son in the rearview mirror"},
                    {"id": "leo", "position": "roof", "posture": "standing", "holding": "", "evidence": "a quote that is not there"}],
     "absent": ["priya"], "relations": [{"character": "hannah", "object": "steering wheel", "side": "at", "evidence": "the steering wheel"}]}]}


class StagingTests(unittest.TestCase):
    def test_entries_need_evidence_from_the_narration(self):
        sheet = staging.validate(deepcopy(SHEET), NARRATION, ["hannah", "leo"])
        car = sheet["moments"][1]
        self.assertEqual([(item["id"], item["position"]) for item in car["characters"]], [("hannah", "driver's seat"), ("leo", "back seat")])
        self.assertEqual(car["absent"], [])                                    # priya is not in the cast
        with self.assertRaises(NarrativeValidationError):
            staging.validate({"moments": [{"sentences": "all"}]}, NARRATION, ["hannah"])
        with self.assertRaises(NarrativeValidationError):
            staging.validate({"moments": []}, NARRATION, ["hannah"])

    def test_a_shot_gets_the_moment_it_covers(self):
        sheet = staging.validate(deepcopy(SHEET), NARRATION, ["hannah", "leo"])
        shot = {"characters": ["hannah", "leo"], "narration_excerpt": "Hannah looked at her son in the rearview mirror."}
        staging.attach(shot, sheet, NARRATION)
        self.assertEqual(shot["staging"]["moment"], [2, 4])

    def test_phrases_inside_the_car_with_seats(self):
        sheet = staging.validate(deepcopy(SHEET), NARRATION, ["hannah", "leo"])
        shot = {"characters": ["hannah", "leo"], "narration_excerpt": "Hannah looked at her son in the rearview mirror."}
        staging.attach(shot, sheet, NARRATION)
        text = staging.phrase(shot["staging"], {"hannah": "the woman", "leo": "the boy"}, {"hannah": "woman", "leo": "man"},
                              {"name": "Car interior", "enclosed": True})
        self.assertEqual(text, "The woman, seated in the driver's seat, her hands on the wheel; the boy, seated in the back seat, both inside the car.")
        self.assertNotIn("chair", text)

    def test_behind_and_in_front_of_furniture_say_what_overlaps_what(self):
        behind = staging.relation_phrase({"object": "desk", "side": "behind"}, "her", "standing")
        self.assertEqual(behind, "standing behind the desk, the desk between her and the viewer")
        self.assertIn("the island behind him", staging.relation_phrase({"object": "island", "side": "in_front_of"}, "him"))
        self.assertEqual(staging.relation_phrase({"object": "table", "side": "at"}, "her", "seated"), "seated on a chair at the table")
        self.assertEqual(staging.contact_wording("She is leaning into the counter and sitting at the desk"),
                         "She is leaning on the edge of the counter and seated on a chair at the desk")

    def test_adjacent_shots_of_one_moment_keep_their_staging(self):
        seat = {"moment": [2, 4], "characters": [{"id": "leo", "position": "back seat", "posture": "seated"}]}
        moved = {"moment": [2, 4], "characters": [{"id": "leo", "position": "passenger seat", "posture": "seated"}]}
        shots = [{"shot_id": "a", "scene_id": "s04", "location": "car_interior", "staging": seat},
                 {"shot_id": "b", "scene_id": "s04", "location": "supermarket_aisle", "staging": moved}]
        problems = [item["problem"] for item in staging.continuity(shots)]
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("two locations" in item for item in problems))
        self.assertEqual(staging.continuity([shots[0], {**shots[0], "shot_id": "c"}]), [])

    def test_the_composed_prompt_carries_the_staging(self):
        vb = deepcopy(VB)
        vb["locations"].append({"location_id": "car_interior", "name": "Car interior", "descriptor": "the inside of a car", "enclosed": True})
        shot = {"shot_id": "s04_06", "scene_id": "s04", "shot_type": "medium", "characters": ["hannah", "leo"], "location": "car_interior",
                "action": "Hannah sits at the wheel", "face_visibility": "partial", "others_present": False, "seed": 1,
                "staging": {"moment": [2, 4], "characters": SHEET["moments"][1]["characters"][:2], "relations": []}}
        with patch("agents.visual_bible.approved_reference", return_value=None), patch("agents.style_refs.approved_paths", return_value=[]):
            prompt = shots_module.compose_shot(shot, vb, "out")["prompt"]
        self.assertIn("in the driver's seat", prompt)
        self.assertIn("both inside the car", prompt)


class PromptCheckTests(unittest.TestCase):
    def test_unnamed_people_and_memories_are_matched(self):
        self.assertTrue(adherence.UNNAMED.search("until the shopper drifted away"))
        self.assertFalse(adherence.UNNAMED.search("Hannah and Leo"))
        self.assertTrue(adherence.MEMORY.search("She remembered when he was six"))
        self.assertTrue(staging.FURNITURE.search("behind the desk"))

    def test_the_check_fixes_the_spec_and_drops_invented_quotes(self):
        vb, shots = deepcopy(VB), scene4()[:2]
        answer = {"shots": [
            {"shot_id": "s04_05", "ok": False, "certain": True, "suggested_prompt_patch": "",
             "problems": [{"kind": "presence", "evidence_quote": "offering a polite smile until the shopper drifted away", "fix": "show the shopper"}]},
            {"shot_id": "s04_06", "ok": False, "certain": True, "suggested_prompt_patch": "Leo sits in the back seat.",
             "problems": [{"kind": "location", "evidence_quote": "In the car on the drive home", "fix": "inside the car"},
                          {"kind": "action", "evidence_quote": "words nobody wrote", "fix": "x"}]}]}
        calls = []

        def generator(prompt, *args, **kwargs):
            calls.append(prompt)
            return json.dumps(answer)

        with tempfile.TemporaryDirectory() as temp:
            usage = adherence.check_entries(shots, vb, temp, compose=lambda entry: {"prompt": f"prompt of {entry['shot_id']}"}, generator=generator,
                                            log=lambda _: None)
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(len(calls), 1)                                        # one call for the scene
        self.assertTrue(shots[0]["others_present"])                            # the shopper the text names
        self.assertEqual(shots[1]["location"], "car_interior")
        self.assertEqual(shots[1]["prompt_patch"], "Leo sits in the back seat.")
        self.assertEqual(len(shots[1]["adherence"]["problems"]), 1)            # the invented quote is not evidence
        self.assertIn("ajustado", adherence.summary_line(shots))

    def test_without_gemini_the_deterministic_checks_stand(self):
        vb, shots = deepcopy(VB), scene4()
        adherence.relocate(shots, vb, log=lambda _: None)
        with tempfile.TemporaryDirectory() as temp:
            usage = adherence.check_entries(shots, vb, temp, compose=lambda entry: {"prompt": ""},
                                            generator=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")), log=lambda _: None)
        self.assertEqual(usage["calls"], 0)
        self.assertEqual(shots[5]["adherence"]["status"], "fixed")

    def test_cost_comes_from_config(self):
        with patch.object(config, "GEMINI_PRICE_INPUT_PER_MTOK", 0.0), patch.object(config, "GEMINI_PRICE_OUTPUT_PER_MTOK", 0.0):
            self.assertEqual(adherence.estimate_cost(40000, 4000), 0.0)
        with patch.object(config, "GEMINI_PRICE_INPUT_PER_MTOK", 1.0), patch.object(config, "GEMINI_PRICE_OUTPUT_PER_MTOK", 2.0):
            self.assertEqual(adherence.estimate_cost(4_000_000, 400_000), 1.2)


class VisionQuestionTests(unittest.TestCase):
    def test_questions_from_the_spec_and_staging(self):
        vb = deepcopy(VB)
        vb["locations"].append({"location_id": "car_interior", "name": "Car interior", "enclosed": True})
        shot = {"shot_id": "s04_06", "location": "car_interior", "characters": ["hannah", "leo"], "others_present": False,
                "staging": {"characters": [{"id": "hannah", "position": "driver's seat", "posture": "seated"},
                                           {"id": "leo", "position": "back seat", "posture": "seated"}]}}
        prompts = {item["qid"]: item["prompt"] for item in image_adherence.questions(shot, vb)}
        self.assertEqual(prompts["pos_hannah"], "Is the woman seated inside a car, in the driver's seat?")
        self.assertEqual(prompts["pos_leo"], "Is the boy seated inside a car, in the back seat?")
        self.assertIn("merged with", prompts["fused"])
        self.assertEqual(prompts["extra"], "Are there more than 2 people in the image?")
        self.assertEqual(prompts["setting"], "Does the setting look like car interior?")

    def test_one_call_per_shot_and_failures_become_hints_and_a_patch(self):
        vb = deepcopy(VB)
        shot = {"shot_id": "x", "location": "kitchen", "characters": ["hannah"], "others_present": False}
        calls = []

        def generator(prompt, model, **kwargs):
            calls.append(kwargs["images"][0][1])
            return json.dumps({"present_hannah": "yes", "fused": "yes", "extra": "no", "setting": "no"})

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "a.jpg")
            Image.new("RGB", (1920, 1080), (90, 90, 90)).save(path)
            result = image_adherence.ask(shot, path, vb, generator=generator)
        self.assertEqual(calls, ["image/jpeg"])
        self.assertEqual(sorted(item["qid"] for item in result["failures"]), ["fused", "setting"])
        notes = [item["note"] for item in image_adherence.hints(result)]
        self.assertIn("personagem fundido com objeto", notes)
        self.assertIn("fora da narração: cenário: kitchen", notes)
        self.assertIn("clearly separate from the furniture", image_adherence.patch_for(result))

    def test_the_estimate_reads_the_price_from_config(self):
        with patch.object(config, "GEMINI_PRICE_PER_IMAGE", 0.002):
            self.assertEqual(image_adherence.estimate(50)["total"], 0.1)
        with patch.object(config, "GEMINI_PRICE_PER_IMAGE", 0):
            self.assertFalse(image_adherence.estimate(50)["known"])


class FusedTests(unittest.TestCase):
    def frame(self, cut=True):
        image = Image.new("L", (640, 360), 90)
        draw = ImageDraw.Draw(image)
        draw.rectangle((280, 80, 360, 200 if cut else 300), fill=150)   # a torso (whole, legs down, when not cut)
        if cut:
            draw.rectangle((150, 200, 500, 300), fill=20)               # a dark counter whose top edge cuts it
        return image.convert("RGB")

    def test_a_body_ending_on_a_furniture_edge_is_a_candidate(self):
        person = [{"box": [280 / 640, 80 / 360, 360 / 640, 200 / 360]}]
        found = image_adherence.fused_with_object(self.frame(), person)
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]["edge"], image_adherence.EDGE_RATIO)
        self.assertEqual(image_adherence.fused_with_object(self.frame(cut=False), person), [])

    def test_a_figure_cut_by_the_bottom_of_the_frame_is_framing(self):
        self.assertEqual(image_adherence.fused_with_object(self.frame(), [{"box": [0.4, 0.3, 0.6, 0.97]}]), [])

    def test_depth_is_reported_not_used(self):
        person = [{"box": [280 / 640, 80 / 360, 360 / 640, 200 / 360]}]
        depth = np.zeros((360, 640), dtype=np.float32)
        depth[:200] = 0.9
        found = image_adherence.fused_with_object(self.frame(), person, depth)
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]["depth_gap"], 0.5)


class RemovalLadderTests(unittest.TestCase):
    def test_the_start_follows_the_mask_size(self):
        self.assertEqual([removal.start_step(value) for value in (0.03, 0.1, 0.3)], [0, 1, 2])
        self.assertEqual([removal.PROFILES[removal.start_step(value)]["denoise"] for value in (0.03, 0.1, 0.3)], [0.6, 0.75, 0.85])

    def test_auto_substituir_por_comes_from_the_location_and_stays_plain(self):
        crop = Image.new("RGB", (400, 300))
        low = Image.new("L", (400, 300), 0)
        low.paste(255, (150, 150, 250, 290))
        text = removal.auto_replace(crop, low, {"descriptor": "a bright kitchen with pale walls and wooden floorboards"})
        self.assertTrue(text.startswith("floorboards"))                # a hole low in the crop: the floor
        self.assertIn(removal.PLAIN, text)
        high = Image.new("L", (400, 300), 0)
        high.paste(255, (150, 10, 250, 100))
        self.assertTrue(removal.auto_replace(crop, high, {"descriptor": "a moor under an open sky"}).startswith("bare sky"))
        tall = Image.new("L", (400, 300), 0)
        tall.paste(255, (150, 20, 250, 290))
        self.assertTrue(removal.auto_replace(crop, tall, {"descriptor": "pale walls and wooden floorboards"}).startswith("bare wall and floorboards"))

    def test_novelty_flags_a_new_person_or_clutter(self):
        source = Image.new("RGB", (400, 300), (90, 90, 90))
        mask = Image.new("L", (400, 300), 0)
        mask.paste(255, (150, 100, 250, 200))
        plain = removal.novelty(source, source, (0, 0, 400, 300), mask, [1, 0])
        self.assertFalse(plain["novel"])
        self.assertTrue(removal.novelty(source, source, (0, 0, 400, 300), mask, [0, 1])["novel"])
        busy = source.copy()
        draw = ImageDraw.Draw(busy)
        for x in range(150, 250, 6):
            draw.line((x, 100, x, 200), fill=(250, 250, 250))
        self.assertTrue(removal.novelty(source, busy, (0, 0, 400, 300), mask, [1, 0])["novel"])


class ExternalProviderTests(unittest.TestCase):
    def test_off_by_default(self):
        with patch.object(config, "EXTERNAL_IMAGE_PROVIDER", ""):
            with self.assertRaises(external_provider.ProviderDisabled):
                external_provider.provider()
        with patch.object(config, "EXTERNAL_IMAGE_PROVIDER", "openai"), patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with self.assertRaises(external_provider.ProviderDisabled):
                external_provider.provider()

    def test_the_budget_cap_and_an_unknown_price_block_spending(self):
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "project.json"), {})
            with patch.object(config, "EXTERNAL_IMAGE_PRICE", 0):
                with self.assertRaises(external_provider.BudgetExceeded):
                    external_provider.check(temp, 1)
            with patch.object(config, "EXTERNAL_IMAGE_PRICE", 0.2):
                with self.assertRaises(external_provider.BudgetExceeded):
                    external_provider.check(temp, 1)                    # no cap set
                external_provider.set_cap(temp, 0.5)
                self.assertTrue(external_provider.check(temp, 2)["allowed"])
                with self.assertRaises(external_provider.BudgetExceeded):
                    external_provider.check(temp, 3)

    def test_a_redo_goes_through_the_import_checks_and_is_recorded(self):
        class Fake:
            name, model = "openai", "gpt-image-1"

            def __init__(self):
                self.calls = []

            def edit(self, prompt, images, mask=None):
                self.calls.append((prompt, list(images), mask))
                buffer = __import__("io").BytesIO()
                Image.new("RGB", (1536, 1024), (200, 120, 90)).save(buffer, "PNG")
                return buffer.getvalue()

        with tempfile.TemporaryDirectory() as temp, patch.object(config, "EXTERNAL_IMAGE_PRICE", 0.2), \
                patch("video.qa.copy_check.boards", return_value=[]):
            output = Path(temp)
            (output / "images/shots").mkdir(parents=True)
            Image.new("RGB", (1920, 1080), (60, 60, 60)).save(output / "images/shots/s04_06.jpg")
            atomic_write_json(output / "project.json", {"external_images": {"cap": 1.0}})
            shot = {"shot_id": "s04_06", "scene_id": "s04", "image_path": "images/shots/s04_06.jpg", "characters": [], "location": "kitchen",
                    "shot_type": "medium", "action": "an empty kitchen", "others_present": False, "seed": 1}
            fake = Fake()
            with self.assertRaises(external_provider.BudgetExceeded):
                external_provider.redo_shot(output, shot, deepcopy(VB), confirmed=False, chosen=fake)
            action = external_provider.redo_shot(output, shot, deepcopy(VB), confirmed=True, chosen=fake, detector=None)
            with Image.open(output / "images/shots/s04_06.jpg") as image:
                self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(shot["source"], "external")
            self.assertEqual(shot["external_provider"]["provider"], "openai")
            self.assertEqual(action["fit"]["fit"], "crop")
            self.assertEqual(external_provider.budget(output)["spent"], 0.2)
            self.assertEqual(len(fake.calls), 1)
            from uploader import editorial_record
            atomic_write_json(output / "shot_list.json", {"shots": [shot]})
            self.assertEqual(editorial_record.build(output)["shots_external_provider"], ["s04_06"])

    def test_the_openai_request_never_leaks_the_key(self):
        sent = {}

        class Session:
            def post(self, url, headers, data, files, timeout):
                sent.update(url=url, data=data, names=[name for name, _ in files])
                return type("R", (), {"status_code": 200, "json": lambda self: {"data": [{"b64_json": "aGk="}]}})()

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "ref.png")
            Image.new("RGB", (8, 8)).save(path)
            out = external_provider.OpenAIImages(session=Session(), key="secret").edit("a prompt", [path])
        self.assertEqual(out, b"hi")
        self.assertEqual(sent["url"], external_provider.OPENAI_EDITS)
        self.assertNotIn("secret", json.dumps(sent["data"]))
        self.assertEqual(sent["names"], ["image[]"])


if __name__ == "__main__":
    unittest.main()
