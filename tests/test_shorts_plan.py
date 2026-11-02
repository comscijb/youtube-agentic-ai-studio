"""Phase 3.7-B.1: Shorts plan — sentence cut points, validation, never the twist (Gemini mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import shorts
from video.timeline import build_scene_timeline

TYPES = ["opening", "setup", "development", "development", "reversal", "resolution"]
BRIEF = {"psychological_theme": "loneliness", "logline": "A lonely man and an AI companion."}


def episode(folder: Path, *, with_words=True):
    """6 scenes x 10 sentences x 5 s (one word per second); the reversal starts at 200 s."""
    sections, words = [], []
    for scene in range(6):
        sentences = [f"Walter speaks line {scene * 10 + n} now." for n in range(10)]
        sections.append({"id": scene + 1, "title": f"Scene {scene + 1}", "section_type": TYPES[scene],
                         "narration": " ".join(sentences)})
        for n, sentence in enumerate(sentences):
            base = (scene * 10 + n) * 5.0
            tokens = sentence.split()
            for k, token in enumerate(tokens):
                words.append({"text": token, "start": base + k, "end": base + k + 0.9,
                              "sentence_end": k == len(tokens) - 1, "scene_id": scene + 1})
    atomic_write_json(folder / "script.json", {"title": "The Only One Who Never Got Tired of Him", "sections": sections})
    atomic_write_json(folder / "scene_timeline.json", build_scene_timeline(sections, [50.0] * 6))
    atomic_write_json(folder / "story_brief.json", BRIEF)
    if with_words:
        atomic_write_json(folder / shorts.WORDS_FILE, {"words": words, "total_duration": 300.0})
    return sections


def segment(first, last, **extra):
    return {"start_sentence": first, "end_sentence": last, "title": "He Thanked the Voice on His Phone",
            "hook_text": "The best conversation in years", "end_text": "Full story on the channel",
            "description": "Walter found someone who never gets tired of him.", **extra}


class SentenceTests(unittest.TestCase):
    def test_sentences_and_twist(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            sections = episode(folder)
            items = shorts.sentences(shorts.word_timeline(folder))
            from video.timeline import load_timeline
            twist = shorts.twist_start({"sections": sections}, load_timeline(folder / "scene_timeline.json"), 300)
        self.assertEqual(len(items), 60)
        self.assertEqual((items[3]["start"], items[3]["end"]), (15.0, 19.9))
        self.assertEqual(items[3]["text"], "Walter speaks line 3 now.")
        self.assertEqual(twist, 200.0)
        self.assertEqual(shorts.twist_start({"sections": []}, None, 300), 210.0)          # no reversal: 70 %

    def test_rebuilt_from_the_tts_cache_for_older_projects(self):
        from video.narrator import cache_key
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            sections = episode(folder, with_words=False)
            cache = folder / ".tts_cache"
            cache.mkdir()
            for section in sections:
                events, cursor = [], 0
                for token in section["narration"].split():
                    events.append({"text": token.strip("."), "start_ms": cursor, "end_ms": cursor + 900})
                    cursor += 1000
                (cache / f"{cache_key(section['narration'])}.json").write_text(json.dumps(events), encoding="utf-8")
            words = shorts.word_timeline(folder)
        self.assertEqual(len(words), 300)
        self.assertEqual((words[50]["start"], words[50]["scene_id"]), (50.0, 2))     # scene 2 starts at 50 s


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        episode(self.folder)
        self.items = shorts.sentences(shorts.word_timeline(self.folder))

    def tearDown(self):
        self.temp.cleanup()

    def check(self, segments, **kwargs):
        return shorts.validate(segments, self.items, twist=200.0, brief=BRIEF, **kwargs)

    def test_good_plan(self):
        # the approach window is the 90 s before the twist (110-200 s): only the last one ends there
        plan = self.check([segment(0, 10), segment(11, 21), segment(28, 39)])
        self.assertEqual([item["id"] for item in plan], ["short_01", "short_02", "short_03"])
        self.assertEqual((plan[0]["start"], plan[0]["end"], plan[0]["seconds"]), (0.0, 54.9, 54.9))
        self.assertEqual(plan[0]["title"], "He Thanked the Voice on His Phone #Shorts")
        self.assertEqual([item["approaches_reversal"] for item in plan], [False, False, True])

    def test_every_rule(self):
        def problems(segments, **kwargs):
            with self.assertRaises(NarrativeValidationError) as caught:
                self.check(segments, **kwargs)
            return str(caught.exception)
        self.assertIn("(must be 45-75 s)", problems([segment(0, 3), segment(14, 25)]))
        self.assertIn("after the twist starts", problems([segment(0, 11), segment(34, 45)]))
        self.assertIn("overlap", problems([segment(0, 11), segment(5, 16)]))
        self.assertIn("only one Short may approach the reversal", problems([segment(16, 27), segment(28, 39)]))
        self.assertIn("breaks the title policy", problems([segment(0, 11, title="The Psychology of Loneliness"),
                                                          segment(14, 25)]))
        self.assertIn("hook_text must be 1-6 words", problems([segment(0, 11, hook_text="one two three four five six seven"),
                                                              segment(14, 25)]))
        self.assertIn("needs 2-3 segments", problems([segment(0, 11)]))
        self.assertIn("outside", problems([segment(0, 80), segment(14, 25)]))
        self.assertIn("1-60 characters", problems([segment(0, 11, title="x" * 60), segment(14, 25)]))


class PlanTests(unittest.TestCase):
    def test_gemini_proposes_code_decides_and_the_plan_is_cached(self):
        calls = []

        def generate(prompt, *args, **kwargs):
            calls.append(prompt)
            return json.dumps({"segments": [segment(0, 11), segment(14, 25, why="a cliffhanger")]})
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            folder = Path(temp)
            episode(folder)
            plan = shorts.build_plan(folder, generator=generate)
            again = shorts.build_plan(folder, generator=generate)
            self.assertTrue((folder / "shorts" / "shorts_plan.json").is_file())
            edited = shorts.save(folder, [dict(plan["segments"][0], end_sentence=12, include=False)])
            with self.assertRaises(NarrativeValidationError):
                shorts.save(folder, [dict(plan["segments"][0], end_sentence=45)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(again["segments"], plan["segments"])
        self.assertEqual((plan["twist_start"], plan["twist_sentence"]), (200.0, 40))
        self.assertIn("must end before sentence 40", calls[0])
        self.assertNotIn("line 45 now", calls[0])                    # sentences after the twist are not sent
        self.assertEqual((edited["segments"][0]["end_sentence"], edited["segments"][0]["include"], edited["edited"]),
                         (12, False, True))


class StageAndGuiTests(unittest.TestCase):
    def test_stage_status_and_endpoints(self):
        import gui
        from studio import projects
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            episode(folder)
            atomic_write_json(folder / "project.json", {"slug": "ep", "video_type": "normal"})
            status = lambda: {item["stage"]: item for item in projects.stage_status(folder)}["shorts"]
            self.assertEqual(status()["status"], "missing")
            plan = {"twist_start": 200.0, "segments": shorts.validate([segment(0, 11), segment(14, 25)],
                                                                      shorts.sentences(shorts.word_timeline(folder)),
                                                                      twist=200.0, brief=BRIEF)}
            atomic_write_json(folder / "shorts" / "shorts_plan.json", plan)
            self.assertEqual((status()["status"], status()["detail"]), ("partial", "0/2 Shorts rendered"))
            with patch.object(config, "OUTPUT_DIR", temp), patch.dict(gui._state, {"script": None, "research": BRIEF}):
                client = gui.app.test_client()
                data = client.get("/api/shorts").get_json()
                bad = client.post("/api/shorts/save", json={"segments": [segment(0, 2)]})
                good = client.post("/api/shorts/save", json={"segments": [segment(0, 11, include=False)]}).get_json()
                page = client.get("/").get_data(as_text=True)
        self.assertEqual((len(data["sentences"]), len(data["plan"]["segments"])), (60, 2))
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(good["plan"]["segments"][0]["include"])
        self.assertIn("Shorts do episódio", page)
        self.assertIn("Short original (história própria)", page)        # the standalone mode, renamed (B.4)
        self.assertNotIn(">YouTube Shorts<", page)


if __name__ == "__main__":
    unittest.main()
