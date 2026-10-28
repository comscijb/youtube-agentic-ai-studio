"""Phase 3.5-A.1: detector-driven layer 1, focused one-word VLM questions, the ComfyUI VLM backend."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from PIL import Image

from scripts import probe_local_vlm as probe
from tests.fake_comfy import OBJECT_INFO, FakeComfy, Response
from video.imagegen_comfyui import ComfyClient
from video.qa import base, focused
from video.qa import deterministic as det
from video.qa.comfy_vlm import ComfyVLM

JULIAN = {"id": "julian_vance", "who": "a 37-year-old man", "markers": ["black horn-rimmed glasses",
                                                                      "polished platinum wedding band"]}
ELENA = {"id": "elena_vance", "who": "a 35-year-old woman", "markers": ["chestnut hair in a low knot"]}
INFO = {**OBJECT_INFO,
        "PreviewAny": {"input": {"required": {"source": ["*", {}]}}},
        "TextGenerate": {"input": {
            "required": {"prompt": ["STRING", {}], "clip": ["CLIP"], "max_length": ["INT", {}],
                         "sampling_mode": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "on"}, {"key": "off"}]}]},
            "optional": {"image": ["IMAGE"], "thinking": ["BOOLEAN"], "use_default_template": ["BOOLEAN"],
                         "mtp": [["off", "on"]]}}}}
PROJECT_SHOTS = Path("projects/2026-10-21-the-man-who-needed-everyone-to-envy-him/images/shots")


def person(box, face=None, score=0.9):
    return {"box": box, "score": score, "face": {"box": face, "score": 0.9} if face else None}


def expected(*characters, others=False, background=0, narration=""):
    return {"people": len(characters), "background": background, "others_present": others,
            "characters": list(characters), "narration": narration}


def frame():
    return Image.new("RGB", (320, 180), (60, 60, 60))


class LayerOneTests(unittest.TestCase):
    def test_duplicate_face_marks_the_smaller_copy_and_is_not_also_an_extra(self):
        detection = {"persons": [person([0.1, 0.1, 0.5, 1.0], [0.2, 0.15, 0.3, 0.3]),
                                 person([0.6, 0.3, 0.8, 1.0], [0.65, 0.35, 0.72, 0.45])],
                     "unsure": [], "face_pairs": [{"persons": [0, 1], "similarity": 0.55}]}
        issues = det.layer1(detection, expected(JULIAN))
        self.assertEqual([(i["type"], i["box"]) for i in issues], [("duplicate_person", [0.6, 0.3, 0.8, 1.0])])

    def test_extra_people_block_unless_the_scene_has_guests(self):
        detection = {"persons": [person([0.2, 0.1, 0.6, 1.0]), person([0.7, 0.4, 0.75, 0.7]),
                                 person([0.1, 0.4, 0.14, 0.7])], "unsure": [], "face_pairs": []}
        issues = det.layer1(detection, expected(JULIAN))
        self.assertEqual([(i["type"], i["severity"]) for i in issues], [("extra_person", "blocking")] * 2)
        guests = det.layer1(detection, expected(JULIAN, others=True))
        self.assertEqual({i["severity"] for i in guests}, {"advisory"})
        allowed = det.layer1(detection, expected(JULIAN, background=2))
        self.assertEqual(allowed, [])

    def test_fewer_people_than_expected_is_only_advisory(self):
        issues = det.layer1({"persons": [], "unsure": [], "face_pairs": []}, expected(JULIAN))
        self.assertEqual([(i["type"], i["severity"]) for i in issues], [("other", "advisory")])


class FocusedQuestionTests(unittest.TestCase):
    def detection(self):
        return {"persons": [person([0.1, 0.1, 0.45, 1.0], [0.2, 0.15, 0.3, 0.3]),
                            person([0.55, 0.1, 0.9, 1.0], [0.65, 0.15, 0.75, 0.3])],
                "unsure": [{"box": [0.92, 0.5, 0.96, 0.7], "score": 0.4}],
                "face_pairs": [{"persons": [0, 1], "similarity": 0.35}]}

    def test_questions_cover_markers_gender_injury_text_unsure_and_borderline_faces(self):
        asked = focused.questions(frame(), self.detection(), expected(JULIAN, ELENA))
        kinds = [q.kind for q in asked]
        self.assertEqual(kinds.count("gender"), 2)                 # mixed genders → who is who
        self.assertEqual(kinds.count("marker"), 2)                 # glasses only: rings/hair are not asked
        self.assertEqual((kinds.count("injury"), kinds.count("text"), kinds.count("person"),
                          kinds.count("same_face")), (2, 1, 1, 1))
        self.assertTrue(all("one word" in q.prompt for q in asked))
        stated = focused.questions(frame(), self.detection(), expected(JULIAN, ELENA, narration="her wrist bleeds"),
                                   ocr=True)
        self.assertNotIn("injury", [q.kind for q in stated])
        self.assertNotIn("text", [q.kind for q in stated])

    def test_missing_glasses_lands_on_the_man_s_face_box(self):
        detection = self.detection()
        asked = focused.questions(frame(), detection, expected(JULIAN, ELENA))
        answers = {"gender:0": "man", "gender:1": "woman", "marker:0:black horn-rimmed glasses": "no",
                   "marker:1:black horn-rimmed glasses": "no", "injury:0": "no", "injury:1": "no", "text": "no",
                   "person:0": "no", "same:0:1": "no"}
        issues, count = focused.apply(asked, answers, detection, expected(JULIAN, ELENA), [])
        self.assertEqual(count, 2)
        self.assertEqual([(i["type"], i["target"], i["box"]) for i in issues],
                         [("missing_marker", "julian_vance", [0.2, 0.15, 0.3, 0.3])])

    def test_yes_unclear_and_confirmations(self):
        detection = self.detection()
        asked = focused.questions(frame(), detection, expected(JULIAN, ELENA))
        answers = {"gender:0": "man", "gender:1": "woman", "marker:0:black horn-rimmed glasses": "unclear",
                   "injury:0": "yes", "injury:1": "unclear", "text": "yes", "person:0": "yes", "same:0:1": "yes"}
        issues, count = focused.apply(asked, answers, detection, expected(JULIAN, ELENA), [])
        found = {(i["type"], i["severity"]) for i in issues}
        self.assertIn(("missing_marker", "advisory"), found)
        self.assertIn(("unstated_injury", "blocking"), found)
        self.assertIn(("unstated_injury", "advisory"), found)
        self.assertIn(("text", "blocking"), found)
        self.assertIn(("extra_person", "blocking"), found)
        self.assertIn(("duplicate_person", "blocking"), found)
        self.assertEqual(count, 3)
        self.assertTrue(all(i["box"] is None for i in issues if i["type"] in ("unstated_injury", "text")))
        # only "unclear" answers carry the flag the GUI hides by default (Phase 3.5.2-C)
        self.assertEqual(sorted((i["type"], i["severity"]) for i in issues if i.get("unclear")),
                         [("missing_marker", "advisory"), ("unstated_injury", "advisory")])

    def test_parse_choice(self):
        self.assertEqual(base.parse_choice("<think></think> Yes."), "yes")
        self.assertEqual(base.parse_choice("Man", ("man", "woman", "unclear")), "man")
        self.assertIsNone(base.parse_choice("The person appears to wear something"))

    def test_crops_are_scaled_to_the_crop_side_both_ways(self):
        image = Image.new("RGB", (1360, 768))
        self.assertEqual(max(focused.crop(image, [0.68, 0.3, 0.73, 0.43]).size), focused.CROP_SIDE)   # small face, enlarged
        self.assertEqual(max(focused.crop(image, [0.0, 0.0, 1.0, 1.0]).size), focused.CROP_SIDE)     # whole frame, reduced

    def test_parse_choice_reads_the_markup_qwen3vl_wraps_answers_in(self):
        # Seen in the user's focused probe (ComfyUI history): every one of these became "unclear".
        self.assertEqual(base.parse_choice('<div class="answer" data-ans="no">no</div>'), "no")
        self.assertEqual(base.parse_choice('<div class="answer" data-ans="woman">woman</div>',
                                           ("man", "woman", "unclear")), "woman")
        self.assertEqual(base.parse_choice("# **Answer: no**\n\nThere is no visible writing"), "no")
        self.assertEqual(base.parse_choice("Assistant: yes"), "yes")
        self.assertIsNone(base.parse_choice("<!---"))
        self.assertIsNone(base.parse_choice("ing the image, the person is not wearing black horn-rimmed glasses"))


class VlmComfy(FakeComfy):
    """Answers each TextGenerate with the next reply; records graphs."""

    def __init__(self, replies):
        super().__init__(object_info=INFO)
        self.replies = list(replies)

    def request(self, method, url, **kwargs):
        path = url.split("8188", 1)[-1]
        if path.startswith("/history/") and self.prompts:
            graph = self.prompts[-1]
            prompt_id = path.rsplit("/", 1)[-1]
            outputs = {node: {"text": [self.replies.pop(0) if self.replies else "hmm"]}
                       for node, value in sorted(graph.items(), key=lambda kv: int(kv[0]))
                       if value["class_type"] == "PreviewAny"}
            return Response({prompt_id: {"status": {"status_str": "success", "completed": True}, "outputs": outputs}})
        return super().request(method, url, **kwargs)


class ComfyVlmTests(unittest.TestCase):
    def questions(self, count):
        return [base.Question(f"q{index}", "text", frame(), "Is there writing? Answer with one word: yes, no or unclear.")
                for index in range(count)]

    def test_one_graph_per_batch_greedy_and_validated(self):
        fake = VlmComfy(["yes", "No.", "unclear"])
        vlm = ComfyVLM(ComfyClient(session=fake, sleep=lambda _: None), model="qwen3vl_4b_int8_convrot.safetensors")
        self.assertTrue(vlm.available())
        self.assertEqual(vlm.ask(self.questions(3)), {"q0": "yes", "q1": "no", "q2": "unclear"})
        self.assertEqual(len(fake.prompts), 1)
        generate = [node for node in fake.prompts[0].values() if node["class_type"] == "TextGenerate"][0]
        self.assertEqual((generate["inputs"]["sampling_mode"], generate["inputs"]["thinking"]), ("off", False))

    def test_a_plain_system_prompt_replaces_the_built_in_one(self):
        info = {**INFO, "PrimitiveString": {"input": {"required": {"value": ["STRING", {}]}}, "output": ["STRING"]},
                "TextGenerate": {"input": {**INFO["TextGenerate"]["input"], "optional": {
                    **INFO["TextGenerate"]["input"]["optional"], "system_prompt": ["STRING", {"forceInput": True}]}}}}
        fake = VlmComfy(['<div class="answer" data-ans="no">no</div>'])
        fake.object_info = info
        vlm = ComfyVLM(ComfyClient(session=fake, sleep=lambda _: None), model="qwen3vl_4b_int8_convrot.safetensors")
        self.assertEqual(vlm.ask(self.questions(1)), {"q0": "no"})
        graph = fake.prompts[0]
        self.assertEqual(graph["2"]["class_type"], "PrimitiveString")
        self.assertEqual(graph["11"]["inputs"]["system_prompt"], ["2", 0])
        self.assertEqual(graph["11"]["inputs"]["max_length"], 32)

    def test_unparseable_answers_are_asked_once_more_then_unclear(self):
        fake = VlmComfy(["it depends", "yes", "still rambling"])
        vlm = ComfyVLM(ComfyClient(session=fake, sleep=lambda _: None))
        self.assertEqual(vlm.ask(self.questions(2)), {"q0": "unclear", "q1": "yes"})
        self.assertEqual(len(fake.prompts), 2)
        self.assertEqual(vlm.texts, {"q0": "it depends || retry: still rambling", "q1": "yes"})

    def test_missing_model_is_reported(self):
        vlm = ComfyVLM(ComfyClient(session=VlmComfy([]), sleep=lambda _: None), model="nope.safetensors")
        self.assertIn("nope.safetensors is not among the CLIPLoader models", vlm.problems())


class FakeDetector:
    """Detections built from the fixture truth (duplicates and extras where the frame has them)."""

    def __init__(self, fixture):
        self.fixture = fixture

    def detect(self, path):
        item = next(i for i in self.fixture["images"] if i["shot_id"] == Path(path).stem)
        truth = item["truth"]
        persons = [person([0.3, 0.05, 0.6, 1.0], [0.4, 0.1, 0.5, 0.3])]
        pairs = []
        for box in truth["boxes"].get("duplicate_person", [])[1:]:
            persons.append(person(box, [box[0] + 0.01, box[1] + 0.01, box[0] + 0.05, box[1] + 0.08]))
            pairs.append({"persons": [0, len(persons) - 1], "similarity": 0.6})
        for box in truth["boxes"].get("extra_person", []):
            persons.append(person(box))
        return {"persons": persons, "unsure": [], "face_pairs": pairs, "seconds": 0.1}


class FocusedProbeTests(unittest.TestCase):
    def test_focused_mode_scores_and_reports(self):
        fixture = json.loads(probe.FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "project"
            (output / "images" / "shots").mkdir(parents=True)
            for item in fixture["images"]:
                Image.new("RGB", (192, 108)).save(output / "images" / "shots" / f"{item['shot_id']}.jpg")
            fake = VlmComfy(["no"] * 200)
            with redirect_stdout(io.StringIO()):
                report = probe.run(output, client=ComfyClient(session=fake, sleep=lambda _: None), mode="focused",
                                   swap=False, detector=FakeDetector(fixture), report_path=Path(temp) / "r.json",
                                   sheet_path=Path(temp) / "s.jpg")
        model = report["models"][0]
        self.assertEqual((report["mode"], model["mode"], len(model["results"])), ("focused", "focused", 8))
        self.assertEqual(model["metrics"]["accuracy"]["duplicate_person"], 1.0)
        self.assertEqual(model["metrics"]["accuracy"]["extra_person"], 1.0)
        self.assertIn("questions_per_image", model["metrics"])
        self.assertTrue(any("detector person recall" in line for line in probe.summary_lines(report)))


@unittest.skipUnless(det.Detector().available() and PROJECT_SHOTS.is_dir(), "local QA models or project frames missing")
class RealDetectorCalibrationTests(unittest.TestCase):
    """The calibration this module documents, on the real probe frames (CPU, local only)."""

    def test_duplicates_and_extras_on_the_probe_frames(self):
        fixture = json.loads(probe.FIXTURE.read_text(encoding="utf-8"))
        detector = det.Detector()
        for item in fixture["images"]:
            path = PROJECT_SHOTS / f"{item['shot_id']}.jpg"
            if not path.is_file():
                self.skipTest(f"{path} missing")
            if datetime.fromtimestamp(path.stat().st_mtime) > datetime.fromisoformat(fixture["frames_older_than"]):
                self.skipTest(f"{path} was regenerated after the probe; the fixture truth no longer applies")
            issues = det.layer1(detector.detect(path), probe.fixture_expected(item, fixture["characters"]))
            kinds = {i["type"] for i in issues if i["severity"] == "blocking"}
            self.assertEqual("duplicate_person" in kinds, item["truth"]["duplicate_person"], item["shot_id"])
            self.assertEqual("extra_person" in kinds, item["truth"]["extra_person"], item["shot_id"])


if __name__ == "__main__":
    unittest.main()
