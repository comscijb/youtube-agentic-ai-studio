"""Phase 3.4-D: the Gemini checklist helpers (expected_for, evaluate, review), always mocked.

The generation loop around them was replaced by the layered pipeline (tests/test_qa_pipeline.py)."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from agents import gemini_client
from agents.gemini_client import GeminiErrorInfo, GeminiGenerationError
from video import visual_qa

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
