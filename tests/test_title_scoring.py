"""Phase 3.6-B: title candidates scored, the best policy-compliant one chosen, choices kept (Gemini mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import packaging, researcher
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

BRIEF = {"psychological_theme": "envy", "logline": "A man needs to be envied.",
         "potential_turning_point": "He turns his wife's grief into a performance"}
CANDIDATES = [
    {"title": "The Man Who Needed Everyone to Envy Him", "rationale": "premise"},
    {"title": "He Turned His Wife's Grief Into a Show", "rationale": "the reversal"},
    {"title": "The Psychology of Envy", "rationale": "explains the thesis"},
]


def scores_reply(rows):
    return json.dumps({"scores": [dict(index=index, note="n", **row) for index, row in enumerate(rows)]})


class ScoreTests(unittest.TestCase):
    def test_length_score_and_total(self):
        self.assertEqual(packaging.length_score("x" * 45), 5)
        self.assertEqual(packaging.length_score("x" * 60), 4)
        self.assertEqual(packaging.length_score("x" * 70), 2)
        self.assertEqual(packaging.length_score("x" * 71), 1)
        self.assertEqual(packaging.total_score({"curiosity": 5, "concreteness": 5, "emotion": 5, "clarity": 5,
                                                "length": 5}), 100)

    def test_scores_rank_policy_passing_titles_first(self):
        reply = scores_reply([{"curiosity": 3, "concreteness": 3, "emotion": 3, "clarity": 5},
                              {"curiosity": 5, "concreteness": 5, "emotion": 5, "clarity": 4},
                              {"curiosity": 5, "concreteness": 5, "emotion": 5, "clarity": 5}])
        with redirect_stdout(io.StringIO()):
            scored = packaging.score_titles(BRIEF, CANDIDATES, generator=lambda *a, **k: reply)
        self.assertEqual([row["title"] for row in scored],
                         ["He Turned His Wife's Grief Into a Show", "The Man Who Needed Everyone to Envy Him",
                          "The Psychology of Envy"])                                     # the thesis title last
        self.assertFalse(scored[-1]["passes"])
        self.assertEqual(scored[0]["scores"]["length"], 5)
        self.assertGreater(scored[0]["total"], scored[1]["total"])

    def test_bad_scores_are_rejected(self):
        with self.assertRaises(NarrativeValidationError):
            packaging.validate_title_scores({"scores": [{"index": 0, "curiosity": 9, "concreteness": 1,
                                                         "emotion": 1, "clarity": 1}]}, 1)
        with self.assertRaises(NarrativeValidationError):
            packaging.validate_title_scores({"scores": []}, 2)

    def test_contracts_ask_for_the_reversal(self):
        self.assertIn("reversal", packaging.title_scoring_contract(["a"]))
        source = Path(researcher.__file__).read_text(encoding="utf-8")
        self.assertIn("most shareable moment", source)
        self.assertIn("At most 60 characters", source)


class StoryDevelopmentTests(unittest.TestCase):
    def test_best_passing_title_is_chosen_and_saved(self):
        brief = dict(BRIEF, video_title=CANDIDATES[0]["title"],
                     title_review={"title_candidates": [dict(item) for item in CANDIDATES]})
        reply = scores_reply([{"curiosity": 3, "concreteness": 3, "emotion": 3, "clarity": 5},
                              {"curiosity": 5, "concreteness": 5, "emotion": 5, "clarity": 4},
                              {"curiosity": 5, "concreteness": 5, "emotion": 5, "clarity": 5}])
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(researcher, "generate", return_value=reply), redirect_stdout(io.StringIO()):
            researcher._score_titles(brief)
            saved = load_json(Path(temp) / packaging.TITLES_FILE)
        self.assertEqual(brief["video_title"], "He Turned His Wife's Grief Into a Show")
        self.assertEqual(saved["selected"], brief["video_title"])
        self.assertEqual(len(saved["candidates"]), 3)

    def test_scoring_failure_keeps_the_policy_choice(self):
        brief = dict(BRIEF, video_title="The Man Who Needed Everyone to Envy Him",
                     title_review={"title_candidates": [dict(item) for item in CANDIDATES]})
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(researcher, "generate", return_value="{}"), redirect_stdout(io.StringIO()):
            researcher._score_titles(brief)
        self.assertEqual(brief["video_title"], "The Man Who Needed Everyone to Envy Him")
        self.assertIn("title scoring requires", brief["title_scoring_error"])


class ComplementTests(unittest.TestCase):
    def test_thumbnail_text_must_not_repeat_the_title(self):
        title = "He Turned His Wife's Grief Into a Show"
        self.assertTrue(packaging.text_repeats_title("HER GRIEF, HIS SHOW", title))
        self.assertFalse(packaging.text_repeats_title("ARE THEY WATCHING?", title))
        from uploader.approval import packaging_findings
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "story_brief.json"), BRIEF)
            atomic_write_json(Path(temp, "script.json"), {"thumbnail_text": "His Wife's Grief"})
            rules = [item["rule"] for item in packaging_findings(temp, title)]
            atomic_write_json(Path(temp, "script.json"), {"thumbnail_text": "Are they watching?"})
            clean = [item["rule"] for item in packaging_findings(temp, title)]
        self.assertIn("thumbnail_repeats_title", rules)
        self.assertNotIn("thumbnail_repeats_title", clean)


class GuiTitleTests(unittest.TestCase):
    def test_pick_a_title_keeps_the_previous_one(self):
        import gui
        with tempfile.TemporaryDirectory() as temp:
            packaging.save_titles(temp, [{"title": CANDIDATES[1]["title"], "total": 90, "passes": True}],
                                  CANDIDATES[0]["title"])
            script = {"title": CANDIDATES[0]["title"]}
            with patch.object(config, "OUTPUT_DIR", temp), patch.dict(gui._state, {"script": script, "research": BRIEF}):
                client = gui.app.test_client()
                listed = client.get("/api/titles").get_json()
                picked = client.post("/api/titles/select", json={"title": CANDIDATES[1]["title"]}).get_json()
                self.assertEqual(client.post("/api/titles/select", json={"title": " "}).status_code, 400)
            saved = load_json(Path(temp, packaging.TITLES_FILE))
            on_disk = load_json(Path(temp, "script.json"))
        self.assertEqual(listed["candidates"][0]["total"], 90)
        self.assertTrue(picked["ok"])
        self.assertEqual(on_disk["title"], CANDIDATES[1]["title"])
        self.assertEqual(saved["history"], [{"title": CANDIDATES[0]["title"], "replaced_by": CANDIDATES[1]["title"],
                                             "reason": "user"}])


if __name__ == "__main__":
    unittest.main()
