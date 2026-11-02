"""Phase 2.2.1 section 1: editorial findings never contradict deterministic facts."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agents import scriptwriter
from agents.narrative import (
    EDITORIAL_DIMENSIONS,
    deterministic_review,
    merge_editorial_review,
    screen_editorial_findings,
    validate_character_bible,
)
from tests.test_narrative import bible, outline, screenplay_with_counts, story_brief

# The exact false positive from benchmark private-gravity-20261021T001448Z.
DURATION_FALSE_POSITIVE = {
    "severity": "major", "issue_type": "duration_mismatch",
    "scene_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "claim": ("The screenplay word count is severely deficient for the specified 12.0-minute "
              "target duration at 125 words per minute, creating a deterministic duration "
              "calculation failure."),
    "evidence": [
        "Brief sets 'target_duration_minutes': 12.0 and 'planned_words_per_minute': 125, which "
        "requires ~1,500 narrated words.",
        "The actual screenplay narration totals only 1,219 words, which yields approximately "
        "9.75 minutes of spoken content at 125 wpm.",
    ],
    "why_it_matters": "Runtime.", "recommendation": "Expand.", "certainty": "confirmed",
}
COUNTS = [144, 173, 175, 192, 181, 196, 176, 158, 141]          # 1 536 words
TARGETS = [140, 175, 165, 180, 185, 195, 170, 155, 135]         # 1 500 words


def finding(claim, issue_type="character_consistency", evidence=(), severity="major"):
    return {"severity": severity, "issue_type": issue_type, "scene_ids": [2], "claim": claim,
            "evidence": list(evidence) or ["Scene 2."], "why_it_matters": "x",
            "recommendation": "y", "certainty": "confirmed"}


def assessment(findings):
    return {"dimensions": {name: {"score": 8, "evidence": ["Scene 1."]}
                           for name in EDITORIAL_DIMENSIONS},
            "findings": findings, "strengths": [], "revision_priorities": []}


def benchmark_report():
    script = screenplay_with_counts(COUNTS, TARGETS)
    script["title"] = "The Man Who Needed Everyone to Envy Him"
    return deterministic_review(script, 12, brief={"psychological_theme": "envy"})


class DurationOwnershipTests(unittest.TestCase):
    def test_benchmark_false_positive_is_discarded_and_status_holds(self):
        report = benchmark_report()
        self.assertEqual(report["word_count"], 1536)
        self.assertEqual(report["status"], "human_review_required")
        merged = merge_editorial_review(report, assessment([DURATION_FALSE_POSITIVE]))
        self.assertEqual(merged["status"], "ready_for_human_review")
        self.assertEqual(merged["major_issues"], [])
        self.assertEqual(merged["manual_review_scene_ids"], [])
        discarded = merged["discarded_editorial_findings"]
        self.assertEqual(len(discarded), 1)
        self.assertEqual(discarded[0]["reason"], "deterministic_owner")
        self.assertEqual(discarded[0]["verified_value"]["word_count"], 1536)

    def test_duration_claim_with_number_under_other_issue_type_is_discarded(self):
        claim = finding("The narration runs 9 minutes, well under the target length.",
                        issue_type="pacing")
        kept, discarded = screen_editorial_findings([claim], benchmark_report())
        self.assertEqual((kept, discarded[0]["reason"]), ([], "deterministic_owner"))

    def test_genuine_deterministic_shortfall_still_reported(self):
        short = deterministic_review(screenplay_with_counts([60] * 9, TARGETS), 12)
        merged = merge_editorial_review(short, assessment([DURATION_FALSE_POSITIVE]))
        self.assertTrue(any(issue["issue_type"] == "total_word_budget"
                            for issue in merged["blocking_issues"]))
        self.assertEqual(merged["status"], "revision_required")
        self.assertEqual(merged["discarded_editorial_findings"][0]["reason"], "deterministic_owner")

    def test_non_numeric_findings_are_kept(self):
        lecture = finding("Scene s08 lectures about mimetic desire.", issue_type="lecture")
        merged = merge_editorial_review(benchmark_report(), assessment([lecture]))
        self.assertEqual(merged["major_issues"][-1]["issue_type"], "lecture")
        self.assertEqual(merged["status"], "revision_required")


class NumericClaimTests(unittest.TestCase):
    def test_age_claim_matching_bible_is_kept_and_contradicted_one_discarded(self):
        cast = validate_character_bible(bible())   # Eli, 47
        consistent = finding("Scene 2 calls Eli a 52-year-old, but the bible makes him aged 47.")
        wrong = finding("Eli is 52 years old, so his memory of the war is impossible.")
        kept, discarded = screen_editorial_findings([consistent, wrong], benchmark_report(), cast)
        self.assertEqual(kept, [consistent])
        self.assertEqual(discarded[0]["reason"], "contradicted_by_artifacts")
        self.assertEqual(discarded[0]["verified_value"]["bible_ages"], {"Eli": 47})

    def test_word_and_scene_counts_are_verified(self):
        # A claim with a number and "words" is deterministic-owned (see above);
        # counts quoted in the evidence are verified instead.
        right = finding("Scene 6 repeats the dinner small talk.", issue_type="repetition",
                        evidence=["Scene 6 already runs 196 words of dinner talk."])
        wrong = finding("Scene 6 repeats the dinner small talk at length.", issue_type="repetition",
                        evidence=["Scene 6 runs 412 words of dinner talk."])
        small = finding("A 40-word speech in scene 6 repeats scene 2.", issue_type="repetition")
        scenes = finding("The story spends 14 scenes on setup.", issue_type="structure")
        kept, discarded = screen_editorial_findings([right, wrong, small, scenes],
                                                    benchmark_report())
        self.assertEqual(kept, [right, small])
        self.assertEqual([item["claim"] for item in discarded], [wrong["claim"], scenes["claim"]])

    def test_discarded_findings_never_affect_manual_review_ids(self):
        wrong = finding("Eli is 30 years old here.")
        merged = merge_editorial_review(benchmark_report(), assessment([wrong]),
                                        validate_character_bible(bible()))
        self.assertEqual(merged["manual_review_scene_ids"], [])
        self.assertEqual(merged["status"], "ready_for_human_review")

    def test_old_reports_without_discard_field_still_load(self):
        old = benchmark_report()
        old.pop("discarded_editorial_findings", None)
        json.loads(json.dumps(old))
        merged = merge_editorial_review(old, None)
        self.assertEqual(merged["discarded_editorial_findings"], [])


class EditorialPromptTests(unittest.TestCase):
    def test_prompt_supplies_deterministic_counts(self):
        report = benchmark_report()
        with patch.object(scriptwriter, "generate", return_value=json.dumps(assessment([]))) as call:
            with redirect_stdout(io.StringIO()):
                scriptwriter._editorial_review({"sections": []}, story_brief(), bible(),
                                               outline(), report)
        prompt = call.call_args.args[0]
        self.assertIn("WORD COUNTS AND DURATION ARE PRE-COMPUTED", prompt)
        self.assertIn('"total_words":1536', prompt)
        self.assertIn('"actual_words":196', prompt)


if __name__ == "__main__":
    unittest.main()
