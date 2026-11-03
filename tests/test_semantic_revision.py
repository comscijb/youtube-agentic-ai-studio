"""Phase 2.2.1 section 2: bounded targeted semantic revision (Gemini mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import scriptwriter
from agents.narrative import deterministic_review, merge_editorial_review, validate_story_brief
from tests.test_narrative import bible, screenplay_with_counts, story_brief
from tests.test_review_reliability import DURATION_FALSE_POSITIVE, assessment, finding

TARGET_MINUTES = 240 / 125


def make_script():
    script = screenplay_with_counts([80, 80, 80], [80, 80, 80])
    script["title"] = "He Waited Twenty Years"
    return script


def lecture(scene_ids=(2,), certainty="confirmed", severity="major", issue_type="Lecture"):
    item = finding("The narrator explains mimetic desire like a seminar.", issue_type=issue_type,
                   severity=severity)
    item.update(scene_ids=list(scene_ids), certainty=certainty)
    return item


def reviewed(script, findings):
    return merge_editorial_review(deterministic_review(script, TARGET_MINUTES), assessment(findings))


def revised_scene(scene_id, words=80):
    return {"id": scene_id, "narration": " ".join(f"revised{n}" for n in range(words))}


class SemanticRevisionTests(unittest.TestCase):
    def run_revision(self, script, review, responses, **settings):
        defaults = {"NARRATIVE_SEMANTIC_REVISION_ENABLED": True,
                    "NARRATIVE_SEMANTIC_REVISION_MAX_CALLS": 1,
                    "NARRATIVE_SEMANTIC_REVISION_BATCH_SIZE": 3,
                    "NARRATIVE_SEMANTIC_REREVIEW": True}
        defaults.update(settings)
        patches = [patch.object(config, key, value) for key, value in defaults.items()]
        with tempfile.TemporaryDirectory() as temp:
            for item in patches:
                item.start()
            try:
                with patch.object(scriptwriter, "generate",
                                  side_effect=[json.dumps(value) for value in responses]) as call, \
                        redirect_stdout(io.StringIO()):
                    result, new_review = scriptwriter._semantic_revision(
                        script, validate_story_brief(story_brief()), bible(), None, review,
                        Path(temp), TARGET_MINUTES, [])
                pre_revision = Path(temp, "script.pre_revision.json")
                saved = json.loads(pre_revision.read_text(encoding="utf-8")) \
                    if pre_revision.exists() else None
            finally:
                for item in reversed(patches):
                    item.stop()
        return result, new_review, call, saved

    def test_eligible_lecture_finding_changes_only_that_scene(self):
        script = make_script()
        review = reviewed(script, [lecture()])
        self.assertEqual(review["status"], "revision_required")
        result, new_review, call, saved = self.run_revision(
            script, review, [{"scenes": [revised_scene(2)]}, assessment([])])
        self.assertEqual(call.call_count, 2)                         # revision + re-review
        self.assertIn("EDITORIAL FINDINGS", call.call_args_list[0].args[0])
        self.assertIn("seminar", call.call_args_list[0].args[0])
        self.assertTrue(result["sections"][1]["narration"].startswith("revised0"))
        for index in (0, 2):
            self.assertEqual(json.dumps(result["sections"][index], sort_keys=True),
                             json.dumps(script["sections"][index], sort_keys=True))
        self.assertEqual(saved["sections"][1]["narration"], script["sections"][1]["narration"])
        record = new_review["semantic_revisions"][0]
        self.assertTrue(record["success"])
        self.assertEqual(record["scene_ids"], [2])
        self.assertEqual(record["word_counts"]["2"], {"before": 80, "after": 80})
        self.assertEqual(record["rereview"]["status"], "ready_for_human_review")
        self.assertEqual(new_review["status"], "ready_for_human_review")
        self.assertEqual(new_review["pre_revision_status"], "revision_required")

    def test_out_of_budget_revision_is_rejected_and_original_kept(self):
        script = make_script()
        review = reviewed(script, [lecture()])
        too_short = {"scenes": [revised_scene(2, words=30)]}
        result, new_review, call, saved = self.run_revision(script, review, [too_short, too_short])
        self.assertIs(result, script)
        self.assertIsNone(saved)
        self.assertEqual(call.call_count, 2)                         # attempt + its one repair
        record = new_review["semantic_revisions"][0]
        self.assertFalse(record["success"])
        self.assertIn("outside its", record["error"])
        self.assertEqual(new_review["status"], "revision_required")

    def test_discarded_and_ineligible_findings_never_trigger_revision(self):
        script = make_script()
        cases = [
            [DURATION_FALSE_POSITIVE],
            [lecture(certainty="possible")],
            [lecture(severity="minor")],
            [lecture(issue_type="ending_openness")],
        ]
        for findings in cases:
            with self.subTest(findings=findings[0]["issue_type"]):
                review = reviewed(script, findings)
                result, new_review, call, _ = self.run_revision(script, review, [])
                call.assert_not_called()
                self.assertIs(result, script)
                self.assertNotIn("semantic_revisions", new_review)

    def test_max_calls_is_respected(self):
        script = make_script()
        review = reviewed(script, [lecture(scene_ids=(1,)), lecture(scene_ids=(2, 3))])
        result, new_review, call, _ = self.run_revision(
            script, review, [{"scenes": [revised_scene(1)]}, assessment([])],
            NARRATIVE_SEMANTIC_REVISION_BATCH_SIZE=1)
        self.assertEqual(call.call_count, 2)
        self.assertEqual([item["scene_ids"] for item in new_review["semantic_revisions"]], [[1]])
        self.assertEqual(result["sections"][1], script["sections"][1])

    def test_disabled_switch_makes_no_calls(self):
        script = make_script()
        review = reviewed(script, [lecture()])
        result, new_review, call, _ = self.run_revision(
            script, review, [], NARRATIVE_SEMANTIC_REVISION_ENABLED=False)
        call.assert_not_called()
        self.assertIs(new_review, review)

    def test_without_rereview_the_editorial_review_is_marked_stale(self):
        script = make_script()
        review = reviewed(script, [lecture()])
        _, new_review, call, _ = self.run_revision(
            script, review, [{"scenes": [revised_scene(2)]}], NARRATIVE_SEMANTIC_REREVIEW=False)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(new_review["editorial_review_stale"])
        self.assertFalse(new_review["semantic_revisions"][0]["rereview"]["performed"])

    def test_issue_type_normalization(self):
        self.assertEqual(scriptwriter.semantic_issue_type("Causal Continuity"), "continuity")
        self.assertEqual(scriptwriter.semantic_issue_type("literary-style"), "style")
        self.assertEqual(scriptwriter.semantic_issue_type("character_agency"), "agency")
        self.assertIsNone(scriptwriter.semantic_issue_type("hook_strength"))


if __name__ == "__main__":
    unittest.main()
