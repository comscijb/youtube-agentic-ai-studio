"""Phase 3.4-D: visual QA with Gemini vision (always mocked here)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from agents import gemini_client
from agents import visual_bible as vbible
from agents.gemini_client import GeminiErrorInfo, GeminiGenerationError
from agents.narrative import atomic_write_json, load_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video import visual_qa
from video.imagegen_comfyui import ComfyClient, ImageGenerator

PASS = {"people_count": 1, "duplicated_person": False,
        "characters": [{"id": "julian_vance", "visible": True, "face_visible": True, "missing_markers": []}],
        "unstated_injury": False, "extra_people": False, "text_or_watermark": False,
        "borders_or_panels": False, "notes": ""}
TWO_JULIANS = dict(PASS, people_count=2, duplicated_person=True)
NO_GLASSES = dict(PASS, characters=[{"id": "julian_vance", "visible": True, "face_visible": True,
                                     "missing_markers": ["horn-rimmed glasses"]}])


def responder(*answers):
    """Fake Gemini: returns the given checklist answers in order (the last one repeats)."""
    calls = []

    def respond(prompt, model=None, **kwargs):
        calls.append(kwargs)
        return json.dumps(answers[min(len(calls) - 1, len(answers) - 1)])
    respond.calls = calls
    return respond


EXPECTED = {"people": 1, "background": 0, "others_present": False, "narration": "He set the table.",
            "characters": [{"id": "julian_vance", "who": "a man", "markers": ["horn-rimmed glasses"], "outfit": ""}]}


class EvaluateTests(unittest.TestCase):
    def test_blocking_and_advisory_findings(self):
        self.assertEqual(visual_qa.evaluate(PASS, EXPECTED), ([], []))
        blocking, _ = visual_qa.evaluate(TWO_JULIANS, EXPECTED)
        self.assertEqual(blocking, ["headcount: 2 people instead of 1", "duplicate: the same person appears twice"])
        self.assertEqual(visual_qa.evaluate(NO_GLASSES, EXPECTED)[0],
                         ["signature_marker: julian_vance without horn-rimmed glasses"])
        hidden = dict(NO_GLASSES, characters=[dict(NO_GLASSES["characters"][0], face_visible=False)])
        self.assertEqual(visual_qa.evaluate(hidden, EXPECTED)[0], [])          # a back view needs no glasses
        blocking, advisory = visual_qa.evaluate(dict(PASS, unstated_injury=True, text_or_watermark=True,
                                                     borders_or_panels=True), EXPECTED)
        self.assertEqual(len(blocking), 2)
        self.assertIn("borders or panels", advisory)
        party = dict(EXPECTED, others_present=True)
        self.assertEqual(visual_qa.evaluate(dict(PASS, people_count=4), party)[0], [])

    def test_review_sends_a_downscaled_image_and_skips_when_gemini_is_down(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "shot.jpg")
            Image.new("RGB", (1920, 1080), (20, 20, 20)).save(path)
            fake = responder(PASS)
            result = visual_qa.review(path, EXPECTED, generator=fake)
            self.assertEqual(result["status"], "pass")
            image, mime = fake.calls[0]["images"][0]
            with Image.open(io.BytesIO(image)) as sent:
                self.assertEqual(max(sent.size), 768)
            self.assertEqual((mime, fake.calls[0]["stage"]), ("image/jpeg", "visual_qa"))

            def down(*args, **kwargs):
                raise GeminiGenerationError(GeminiErrorInfo("provider_unavailable", 503, True, True, "", "down"))
            skipped = visual_qa.review(path, EXPECTED, generator=down)
        self.assertEqual(skipped["status"], "skipped")
        self.assertIn("visual QA skipped", skipped["warning"])


class GenerationQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        atomic_write_json(self.output / vbible.FILE_NAME, json.loads(json.dumps(VISUAL_BIBLE)))
        shots = [dict(SHOTS[0], others_present=False, appearance={"julian_vance": "composed"})]
        atomic_write_json(self.output / "shot_list.json", {"version": 2, "shots": shots, "unique_shots": 1})
        self.fake = FakeComfy()
        self.patches = [patch.object(config, "IMAGEGEN_VISUAL_QA", True),
                        patch.object(gen, "missing_approvals", return_value=[])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_shots(self, gemini):
        notes = []
        with patch("agents.gemini_client.generate", side_effect=gemini):
            summary = gen.generate_shots(self.output, ImageGenerator(self.output, ComfyClient(
                "http://127.0.0.1:8188", session=self.fake, sleep=lambda _: None)), progress=notes.append)
        shot = load_json(self.output / "shot_list.json")["shots"][0]
        record = next(item for item in load_json(self.output / "asset_manifest.json")["generated_assets"]
                      if item["job_id"] == shot["shot_id"])
        return summary, shot, record, notes

    def test_pass_needs_no_regeneration(self):
        summary, shot, record, _ = self.run_shots(responder(PASS))
        self.assertEqual(len(self.fake.prompts), 1)
        self.assertEqual(shot["qa"], {"status": "pass", "notes": []})
        self.assertEqual(record["visual_qa"]["status"], "pass")
        self.assertEqual(summary["qa_failed"], [])

    def test_fail_then_pass_regenerates_with_a_new_seed(self):
        summary, shot, record, _ = self.run_shots(responder(TWO_JULIANS, PASS))
        seeds = [graph["7"]["inputs"]["seed"] for graph in self.fake.prompts]
        self.assertEqual(len(seeds), 2)
        self.assertEqual(seeds[1] - seeds[0], gen.QA_SEED_OFFSET)
        self.assertEqual(shot["qa"]["status"], "pass")
        self.assertEqual([item["status"] for item in record["visual_qa"]["attempts"]], ["fail", "pass"])

    def test_failing_twice_keeps_the_best_attempt_and_flags_it(self):
        summary, shot, record, notes = self.run_shots(responder(TWO_JULIANS, NO_GLASSES, TWO_JULIANS))
        self.assertEqual(len(self.fake.prompts), 1 + config.IMAGEGEN_QA_RETRIES)
        self.assertEqual(shot["qa"]["status"], "fail")
        self.assertEqual(shot["qa"]["notes"], ["signature_marker: julian_vance without horn-rimmed glasses"])
        self.assertEqual(record["seed"], self.fake.prompts[1]["7"]["inputs"]["seed"])   # the best one
        self.assertEqual(summary["qa_failed"], [shot["shot_id"]])
        self.assertTrue((self.output / shot["image_path"]).is_file())
        self.assertFalse(list(self.output.rglob("*.qa-best.jpg")))
        self.assertTrue(any("still fails" in note for note in notes))

    def test_gemini_down_skips_with_a_warning(self):
        def down(*args, **kwargs):
            raise GeminiGenerationError(GeminiErrorInfo("provider_unavailable", 503, True, True, "", "down"))
        summary, shot, record, notes = self.run_shots(down)
        self.assertEqual(shot["qa"]["status"], "skipped")
        self.assertEqual(summary["generated"], 1)
        self.assertTrue(any("visual QA skipped" in note for note in notes))


class UsageTests(unittest.TestCase):
    def test_visual_qa_calls_appear_in_the_usage_line(self):
        gemini_client.clear_call_records()
        try:
            gemini_client._record_call("visual_qa", "gemini-flash", 900, 2.5, 60, "success")
            self.assertIn("(visual QA: 1 calls, 2s)", gemini_client.usage_line())
        finally:
            gemini_client.clear_call_records()


if __name__ == "__main__":
    unittest.main()
