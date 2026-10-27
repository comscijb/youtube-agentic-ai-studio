"""Phase 3.5-C: layered QA (deterministic → local VLM → optional Gemini), all backends mocked."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import config
from agents import visual_bible as vbible
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video.qa import base, layered
from video.qa.gemini_vlm import GeminiVLM
from video.qa.ollama_vlm import OllamaVLM


def person(box, face=None):
    return {"box": box, "score": 0.9, "face": {"box": face, "score": 0.9} if face else None}


class FakeDetector:
    def __init__(self, detections):
        self.detections = detections            # {file stem: detection}

    def available(self):
        return True

    def detect(self, path):
        return {"seconds": 0.1, **self.detections[Path(path).stem]}


class FakeVLM:
    name = "comfy_textgenerate"

    def __init__(self, answer="no", ok=True, log=None):
        self.answer, self.ok, self.log = answer, ok, log if log is not None else []
        self.batches = []

    def available(self):
        return self.ok

    def ask(self, questions):
        self.log.append("ask")
        self.batches.append([q.qid for q in questions])
        return {q.qid: (self.answer(q) if callable(self.answer) else self.answer) for q in questions}

    def unload(self):
        self.log.append("unload vlm")


class FakeComfyClient:
    def __init__(self, log):
        self.log = log

    def unload_models(self):
        self.log.append("unload comfy")


JULIAN = {"id": "julian_vance", "who": "a 37-year-old man", "markers": ["black horn-rimmed glasses"]}
EXPECTED = {"people": 1, "background": 0, "others_present": False, "characters": [JULIAN], "narration": ""}
ALONE = {"persons": [person([0.3, 0.1, 0.6, 1.0], [0.4, 0.15, 0.5, 0.3])], "unsure": [], "face_pairs": []}
CROWD = {"persons": [person([0.3, 0.1, 0.6, 1.0], [0.4, 0.15, 0.5, 0.3])]
         + [person([0.05 + 0.08 * i, 0.4, 0.1 + 0.08 * i, 0.7]) for i in range(3)], "unsure": [], "face_pairs": []}


class LayeredTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        for name in ("alone", "crowd"):
            Image.new("RGB", (320, 180), (70, 70, 70)).save(self.folder / f"{name}.jpg")

    def tearDown(self):
        self.temp.cleanup()

    def qa(self, layers=("deterministic", "local_vlm"), **kwargs):
        kwargs.setdefault("detector", FakeDetector({"alone": ALONE, "crowd": CROWD}))
        return layered.LayeredQA(list(layers), progress=lambda _: None, **kwargs)


class LayerSelectionTests(unittest.TestCase):
    def test_configured_layers(self):
        self.assertEqual(layered.configured_layers("deterministic,local_vlm", gemini=False),
                         ["deterministic", "local_vlm"])
        self.assertEqual(layered.configured_layers("deterministic, bogus", gemini=True), ["deterministic", "gemini"])
        with patch.object(config, "IMAGEGEN_QA_LAYERS", "deterministic,local_vlm"), \
                patch.object(config, "IMAGEGEN_QA_GEMINI", False):
            self.assertEqual(layered.configured_layers(), ["deterministic", "local_vlm"])

    def test_gemini_is_off_by_default(self):
        self.assertFalse(config.IMAGEGEN_QA_GEMINI)
        self.assertEqual(config.IMAGEGEN_QA_VLM_MODEL, "qwen3vl_4b_int8_convrot.safetensors")


class LayeredTests(LayeredTestCase):
    def test_batch_asks_the_vlm_once_between_unloads(self):
        log = []
        vlm = FakeVLM("no", log=log)
        qa = self.qa(vlm=vlm, comfy=FakeComfyClient(log))
        results = qa.review_batch([(self.folder / "alone.jpg", EXPECTED), (self.folder / "alone.jpg", EXPECTED)])
        self.assertEqual(log, ["unload comfy", "ask", "unload vlm"])
        self.assertEqual(len(vlm.batches), 1)
        for result in results:
            self.assertEqual((result["verdict"], result["layers"]), ("edit", ["deterministic", "local_vlm"]))
            self.assertEqual(result["issues"][0]["type"], "missing_marker")
            self.assertEqual(result["issues"][0]["box"], [0.4, 0.15, 0.5, 0.3])

    def test_layer_two_is_skipped_when_layer_one_already_says_regenerate(self):
        vlm = FakeVLM("no")
        result = self.qa(vlm=vlm).review(self.folder / "crowd.jpg", EXPECTED)
        self.assertEqual(result["verdict"], "regenerate")
        self.assertEqual(vlm.batches, [])
        self.assertEqual(result["layers"], ["deterministic"])

    def test_unavailable_local_vlm_falls_back_to_gemini_only_when_enabled(self):
        gemini = FakeVLM("yes")
        gemini.name = "gemini"
        result = self.qa(("deterministic", "local_vlm", "gemini"), vlm=FakeVLM(ok=False), gemini=gemini) \
            .review(self.folder / "alone.jpg", EXPECTED)
        self.assertEqual(result["layers"], ["deterministic", "gemini"])
        self.assertIn("local VLM (comfy_textgenerate) unavailable", result["warnings"])
        alone = self.qa(vlm=FakeVLM(ok=False)).review(self.folder / "alone.jpg", EXPECTED)
        self.assertEqual(alone["layers"], ["deterministic"])
        self.assertTrue(any("layer 2 skipped" in warning for warning in alone["warnings"]))
        self.assertEqual(alone["verdict"], "pass")                 # layer 1 found nothing; never blocks

    def test_no_detector_and_no_vlm_is_skipped(self):
        class Missing:
            def available(self):
                return False
        result = self.qa(("deterministic",), detector=Missing()).review(self.folder / "alone.jpg", EXPECTED)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("setup_qa_models", result["warnings"][0])


class BackendTests(unittest.TestCase):
    def questions(self, count):
        image = Image.new("RGB", (64, 64))
        return [base.Question(f"q{i}", "text", image, "Writing? Answer with one word: yes, no or unclear.")
                for i in range(count)]

    def test_gemini_answers_a_batch_in_one_call(self):
        calls = []

        def generate(prompt, model=None, **kwargs):
            calls.append(kwargs)
            return json.dumps({"q0": "yes", "q1": "No", "q2": "maybe"})
        vlm = GeminiVLM(generator=generate)
        self.assertEqual(vlm.ask(self.questions(3)), {"q0": "yes", "q1": "no", "q2": "unclear"})
        self.assertEqual((len(calls), len(calls[0]["images"]), calls[0]["stage"]), (1, 3, "visual_qa"))

    def test_gemini_failure_is_vlm_unavailable(self):
        def down(*args, **kwargs):
            raise RuntimeError("503")
        with self.assertRaises(base.VLMUnavailable):
            GeminiVLM(generator=down).ask(self.questions(1))

    def test_ollama_never_pulls_and_unloads(self):
        posted = []
        session = SimpleNamespace(
            get=lambda url, timeout: SimpleNamespace(json=lambda: {"models": [{"name": "qwen2.5vl:3b"}]}),
            post=lambda url, json, timeout: posted.append((url, json)) or SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: {"response": "Yes."}))
        vlm = OllamaVLM(url="http://127.0.0.1:11434", model="qwen2.5vl:3b", session=session)
        self.assertTrue(vlm.available())
        self.assertFalse(OllamaVLM(model="other:7b", session=session).available())
        self.assertEqual(vlm.ask(self.questions(2)), {"q0": "yes", "q1": "yes"})
        vlm.unload()
        self.assertTrue(all("/api/pull" not in url for url, _ in posted))
        self.assertEqual(posted[-1][1], {"model": "qwen2.5vl:3b", "keep_alive": 0})


class QaShotsScriptTests(unittest.TestCase):
    def test_qa_shots_stores_verdict_and_issues_without_regenerating(self):
        from scripts import qa_shots
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            atomic_write_json(output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
            shot = dict(SHOTS[0], others_present=False, image_path="images/shots/s01_01.jpg")
            atomic_write_json(output / "shot_list.json", {"version": 2, "shots": [shot], "unique_shots": 1})
            (output / "images" / "shots").mkdir(parents=True)
            Image.new("RGB", (320, 180)).save(output / "images" / "shots" / "s01_01.jpg")
            qa = layered.LayeredQA(["deterministic", "local_vlm"], detector=FakeDetector({"s01_01": ALONE}),
                                   vlm=FakeVLM("no"), progress=lambda _: None)
            with redirect_stdout(io.StringIO()):
                summary = qa_shots.run(output, qa=qa, progress=lambda _: None)
            saved = load_json(output / "shot_list.json")["shots"][0]["qa"]
        self.assertEqual((summary["checked"], summary["edit"]), (1, ["s01_01"]))
        self.assertEqual((saved["status"], saved["verdict"], saved["edits"]), ("fail", "edit", 0))
        self.assertEqual(saved["issues"][0]["type"], "missing_marker")
        self.assertTrue(saved["notes"][0].startswith("⛔ missing_marker"))

    def test_shots_without_an_image_are_counted_and_explained(self):
        from scripts import qa_shots
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            atomic_write_json(output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
            atomic_write_json(output / "shot_list.json", {"version": 2, "shots": [dict(SHOTS[0])], "unique_shots": 1})
            (output / "images" / "shots").mkdir(parents=True)
            Image.new("RGB", (320, 180)).save(output / "images" / "shots" / "s01_01.jpg")   # from an older list
            printed = io.StringIO()
            with redirect_stdout(printed), patch.object(qa_shots.projects, "cli_output", return_value=output):
                qa_shots.main([])
        self.assertIn("1 shot(s) of shot_list.json have no image yet", printed.getvalue())
        self.assertIn("python -m scripts.generate_shots", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
