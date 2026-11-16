"""Phase 4.1-E: the attention revision loop, its guards, the shorter budget, the editorial clamp, the override."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents import attention_revision, narrative
from agents.narrative import NarrativeValidationError, atomic_write_json

SLOW = ("The valley was quiet in the cold grey morning light. The old road was long and empty. "
        "The trees stood still along the river. The sky was pale over the hills.")
SCRIPT = {"narrative_type": "dark_human_parable", "sections": [
    {"id": 1, "outline_scene_id": "s01", "section_type": "opening", "title": "Morning", "narration": SLOW + " " + SLOW},
    {"id": 2, "outline_scene_id": "s02", "section_type": "reversal", "title": "Turn",
     "narration": "\"Who sold the sky?\" she asked. Nobody answered."}]}
FAST = "\"Sign it,\" the Merchant said, and pressed his ring into the wax. Why would anyone sell the sky? The lord signed. Outside, the valley woke under a morning that now had an owner."


def evaluation(passes, majors=1):
    findings = [] if passes else [{"rule": "first_5_seconds", "severity": "major", "scene_ids": ["s01"], "at": "0:00",
                                   "claim": "dull", "recommendation": "open on action", "start": 0}]
    return {"check": {"findings": findings, "major": 0 if passes else majors, "passes": passes, "duration": 30.0},
            "sim": None, "passes": passes, "duration": 30.0 if not passes else 20.0}


class LoopTests(unittest.TestCase):
    def test_failing_scenes_get_instructions(self):
        todo = attention_revision.failing(SCRIPT, evaluation(False))
        self.assertEqual(list(todo), [1])
        self.assertIn("Open on an action", todo[1][0])

    def test_at_most_two_rounds_and_the_failed_label(self):
        calls = []

        def fake_revise(script, todo, **kwargs):
            calls.append(todo)
            return dict(script, sections=[dict(script["sections"][0], narration=FAST), script["sections"][1]])
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(attention_revision, "evaluate", return_value=evaluation(False)), \
                patch.object(attention_revision, "revise", side_effect=fake_revise):
            script, record = attention_revision.run(temp, SCRIPT, brief={}, bible={}, outline=None, max_rounds=5)
            self.assertEqual(len(calls), 2)
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["label"], "não passou no portão de atenção")
            self.assertTrue(Path(temp, "attention_review.json").is_file())

    def test_stops_when_it_passes_and_reports_durations(self):
        results = iter([evaluation(False), evaluation(True)])
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(attention_revision, "evaluate", side_effect=lambda *a, **k: next(results)), \
                patch.object(attention_revision, "revise", side_effect=lambda script, todo, **k: dict(script, sections=[
                    dict(script["sections"][0], narration=FAST), script["sections"][1]])):
            script, record = attention_revision.run(temp, SCRIPT, brief={}, bible={}, outline=None)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(len(record["rounds"]), 1)
        self.assertEqual((record["duration_before"], record["duration_after"]), (30.0, 20.0))
        self.assertEqual(script["sections"][0]["narration"], FAST)


class ReviseTests(unittest.TestCase):
    def reply(self, narration):
        return lambda *a, **k: json.dumps({"scenes": [{"id": 1, "narration": narration}]})

    def test_shorter_is_allowed_and_recorded(self):
        with redirect_stdout(io.StringIO()):
            revised = attention_revision.revise(SCRIPT, {1: ["open on action"]}, brief={}, bible={}, outline=None,
                                                generator=self.reply(FAST))
        self.assertEqual(revised["sections"][0]["narration"], FAST)
        self.assertEqual(revised["sections"][1], SCRIPT["sections"][1])            # untouched byte for byte
        self.assertEqual(revised["attention_shortened"], [1])

    def test_the_authors_line_must_survive(self):
        brief = {"author_brief": {"beats": {"hook": "\"The valley was quiet\""}}}
        with patch("agents.premise_brief.required_lines", return_value=[{"line": "The trees stood still along the river", "kind": "narrated"}]):
            with self.assertRaises(NarrativeValidationError) as caught, redirect_stdout(io.StringIO()):
                attention_revision.revise(SCRIPT, {1: ["x"]}, brief=brief, bible={}, outline=None, generator=self.reply(FAST))
        self.assertIn("author's line was lost", str(caught.exception))

    def test_too_long_is_refused(self):
        with self.assertRaises(NarrativeValidationError), redirect_stdout(io.StringIO()):
            attention_revision.revise(SCRIPT, {1: ["x"]}, brief={}, bible={}, outline=None, generator=self.reply(" ".join(["word"] * 200)))


class BudgetAndClampTests(unittest.TestCase):
    def screenplay(self, shortened):
        sections = [{"id": index + 1, "outline_scene_id": f"s{index + 1:02d}", "title": f"S{index + 1}",
                     "section_type": kind, "narration": " ".join(["word"] * 60), "target_words": 100,
                     "narrative_function": "x"} for index, kind in enumerate(["opening", "reversal", "aftermath"])]
        return {"narrative_type": "dark_human_parable", "planned_words_per_minute": 150, "video_type": "normal",
                "sections": sections, **({"attention_shortened": [1, 2, 3]} if shortened else {})}

    def test_a_shorter_total_is_allowed_after_the_attention_cut(self):
        normal = narrative.deterministic_review(self.screenplay(False), 2.0)
        cut = narrative.deterministic_review(self.screenplay(True), 2.0)
        types = lambda review: {item["issue_type"] for item in review["blocking_issues"] + review["major_issues"]}
        self.assertIn("total_word_budget", types(normal))
        self.assertNotIn("total_word_budget", types(cut))
        self.assertNotIn("scene_word_budget", types(cut))

    def test_attention_majors_clamp_progression_and_momentum(self):
        assessment = {"dimensions": {"dramatic_progression": {"score": 9}, "momentum": {"score": 10}, "literary_style": {"score": 9}}}
        clamped, record = narrative.clamp_scores(assessment, [{"issue_type": "attention", "claim": "dull opening"}])
        self.assertEqual(clamped["dimensions"]["dramatic_progression"]["score"], 6)
        self.assertEqual(clamped["dimensions"]["momentum"]["score"], 6)
        self.assertEqual(clamped["dimensions"]["literary_style"]["score"], 9)
        self.assertEqual(len(record), 2)

    def test_the_new_dimensions_are_asked_and_old_reports_still_read(self):
        self.assertEqual(narrative.ATTENTION_EDITORIAL_DIMENSIONS, ("opening_grip", "momentum", "curiosity_management", "ear_readability"))
        old = {"dimensions": {name: {"score": 8, "evidence": ["e"]} for name in narrative.PRE_ATTENTION_EDITORIAL_DIMENSIONS},
               "findings": [], "overall_score": 8, "strengths": [], "summary": "s"}
        try:
            narrative.validate_editorial_assessment(old, "dark_human_parable")
        except NarrativeValidationError as exc:
            self.assertNotIn("missing", str(exc))


class OverrideTests(unittest.TestCase):
    def test_a_failed_gate_needs_a_reason(self):
        from uploader import approval
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "narrative_review.json"), {"status": "human_review_required",
                                                                    "attention": {"status": "failed", "label": "não passou no portão de atenção"}})
            video = Path(temp, "v.mp4")
            video.write_bytes(b"0")
            with patch.object(approval, "checklist_for", return_value=[]), \
                    patch.object(approval, "packaging_findings", return_value=[]):
                with self.assertRaises(approval.UploadNotApproved) as caught:
                    approval.approve(temp, checklist={}, video_path=str(video), title="t", description="d")
                self.assertIn("portão de atenção", " ".join(caught.exception.reasons))
                with patch.object(approval, "bindings", return_value={}), \
                        patch("uploader.editorial_record.build", return_value={}), \
                        patch("uploader.editorial_record.summary", return_value=""):
                    record = approval.approve(temp, checklist={}, video_path=str(video), title="t", description="d",
                                              attention_override_reason="A abertura lenta é proposital neste episódio")
            self.assertEqual(record["attention_status"], "failed")
            self.assertIn("proposital", record["attention_override_reason"])


if __name__ == "__main__":
    unittest.main()
