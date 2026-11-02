"""Phase 3.9-E: thumbnail–video coherence — focal element shown, text in the story, Shorts hooks from
their own words, thumbnail prompts never realistic (Gemini mocked)."""
import json
import tempfile
import unittest
from pathlib import Path

from agents import identity
from agents import shot_list as shots_module
from agents.narrative import atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from video import coherence, thumbnails


def make_run(folder: Path, concept: dict, shots: list[dict]) -> Path:
    atomic_write_json(folder / "visual_bible.json", json.loads(json.dumps(VISUAL_BIBLE)))
    atomic_write_json(folder / "shot_list.json", {"shots": shots})
    atomic_write_json(folder / "thumbnails" / "thumbnails.json", {"selected": 1, "variants": [
        {"number": 1, "concept": concept}]})
    atomic_write_json(folder / "script.json", {"title": "T", "sections": [
        {"id": 1, "narration": "Julian set the table for eight and waited. Nobody came."}]})
    return folder


SHOTS = [{"shot_id": "s01_01", "characters": ["julian_vance"], "action": "Julian straightens a linen napkin",
          "prompt": "decanters on a blackened oak table"}]


class FocalTests(unittest.TestCase):
    def test_people_and_objects_the_thumbnail_relies_on(self):
        with tempfile.TemporaryDirectory() as temp:
            two = make_run(Path(temp, "a"), {"composition": "two_person", "other_character": "elena_vance",
                                              "focal_element": "two faces", "text": "SHE KNEW"}, SHOTS)
            self.assertIn("elena_vance", coherence.focal_findings(two)[0])
            shown = make_run(Path(temp, "b"), {"composition": "telling_object", "focal_element": "the linen napkin",
                                                "text": "SET FOR EIGHT"}, SHOTS)
            self.assertEqual(coherence.focal_findings(shown), [])
            missing = make_run(Path(temp, "c"), {"composition": "telling_object", "focal_element": "a burning letter",
                                                  "text": "TOO LATE"}, SHOTS)
            self.assertIn("burning letter", coherence.focal_findings(missing)[0])


class TextTests(unittest.TestCase):
    def test_gemini_check_is_cached_and_warns_only_when_absent(self):
        calls = []

        def generator(prompt, **kwargs):
            calls.append(kwargs)
            return json.dumps({"contains": False, "evidence": "nobody burns a letter"})
        with tempfile.TemporaryDirectory() as temp:
            run = make_run(Path(temp), {"composition": "big_face", "focal_element": "his face", "text": "HE BURNED IT"}, SHOTS)
            first = coherence.text_findings(run, generator=generator)
            again = coherence.text_findings(run, generator=generator)
            self.assertEqual(coherence.text_findings(run), first)                   # cached answer, no generator
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["temperature"], 0.1)                              # low temperature
        self.assertIn("HE BURNED IT", first[0])
        self.assertEqual(first, again)
        with tempfile.TemporaryDirectory() as temp:
            run = make_run(Path(temp), {"composition": "big_face", "focal_element": "his face", "text": "NOBODY CAME"}, SHOTS)
            self.assertEqual(coherence.text_findings(run, generator=lambda *a, **k: '{"contains": true}'), [])


class ShortHookTests(unittest.TestCase):
    def test_hook_must_come_from_the_segment(self):
        from video import shorts
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            words = [{"text": word, "start": index * 0.4} for index, word in
                     enumerate("That is the best conversation I have had in two years".split())]
            atomic_write_json(run / shorts.WORDS_FILE, {"words": words})
            atomic_write_json(run / shorts.FOLDER / shorts.PLAN_FILE, {"segments": [
                {"id": "short_01", "start": 0, "end": 5, "hook_text": "The best conversation", "include": True},
                {"id": "short_02", "start": 0, "end": 5, "hook_text": "She burned the letters", "include": True}]})
            found = coherence.short_findings(run)
        self.assertEqual(len(found), 1)
        self.assertIn("short_02", found[0])


class StyleTests(unittest.TestCase):
    def test_thumbnail_prompts_keep_the_style_suffix(self):
        concept = {"composition": "big_face", "emotion": "forced smile", "focal_element": "his face",
                   "text": "SMILE", "text_side": "left"}
        shot = thumbnails.concept_shot(concept, json.loads(json.dumps(VISUAL_BIBLE)), {"thumbnail_concept": "x"})
        with tempfile.TemporaryDirectory() as temp:
            prompt = shots_module.compose_shot(shot, json.loads(json.dumps(VISUAL_BIBLE)), temp)["prompt"]
        if identity.is_illustration():
            self.assertIn("illustrated, not photographic", prompt)
        self.assertNotIn("photorealistic", prompt.replace("not photographic", ""))


if __name__ == "__main__":
    unittest.main()
