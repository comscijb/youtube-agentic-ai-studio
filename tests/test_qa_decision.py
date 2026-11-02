"""Phase 3.5-B: edit vs regenerate decision rules (code, not the model)."""
import unittest
from unittest.mock import patch

import config
from video.qa import base, decision


def result(*issues):
    return {"people_count": 1, "issues": list(issues)}


SMALL = [0.6, 0.3, 0.8, 0.9]          # 12 % of the frame
FACE = [0.4, 0.1, 0.5, 0.3]
LARGE = [0.1, 0.0, 0.8, 1.0]          # 70 %


class DecisionTests(unittest.TestCase):
    def test_no_blocking_issue_passes_even_with_advisories(self):
        found = decision.finalize(result(base.issue("extra_person", severity="advisory", box=SMALL)))
        self.assertEqual((found["verdict"], found["status"]), ("pass", "pass"))

    def test_small_localized_issues_are_edited(self):
        for issues in ([base.issue("missing_marker", box=FACE)],
                       [base.issue("duplicate_person", box=SMALL), base.issue("missing_marker", box=FACE)],
                       [base.issue("extra_person", box=SMALL)]):
            self.assertEqual(decision.verdict(result(*issues))[0], "edit")

    def test_instruction_only_fixes_need_no_box(self):
        self.assertEqual(decision.verdict(result(base.issue("unstated_injury")))[0], "edit")
        self.assertEqual(decision.verdict(result(base.issue("text")))[0], "edit")
        self.assertEqual(decision.verdict(result(base.issue("missing_marker")))[0], "regenerate")

    def test_threshold_is_35_percent_of_the_frame(self):
        just_under = [0.0, 0.0, 0.5, 0.69]            # 34.5 %
        at = [0.0, 0.0, 0.5, 0.7]                     # 35 %
        self.assertEqual(decision.verdict(result(base.issue("duplicate_person", box=just_under)))[0], "edit")
        verdict, reason = decision.verdict(result(base.issue("duplicate_person", box=at)))
        self.assertEqual(verdict, "regenerate")
        self.assertIn("main subject", reason)
        self.assertEqual(decision.verdict(result(base.issue("missing_marker", box=LARGE)))[0], "regenerate")

    def test_three_issues_style_border_wrong_subject_regenerate(self):
        three = [base.issue("extra_person", box=SMALL)] * 3
        self.assertEqual(decision.verdict(result(*three))[0], "regenerate")
        for kind in ("style_break", "border", "wrong_subject"):
            self.assertEqual(decision.verdict(result(base.issue(kind, box=SMALL)))[0], "regenerate")

    def test_after_two_failed_edits_regenerate(self):
        issues = result(base.issue("missing_marker", box=FACE))
        self.assertEqual(decision.verdict(issues, edits_done=1)[0], "edit")
        self.assertEqual(decision.verdict(issues, edits_done=2)[0], "regenerate")
        with patch.object(config, "IMAGEGEN_QA_MAX_EDITS", 1):
            self.assertEqual(decision.verdict(issues, edits_done=1)[0], "regenerate")

    def test_skipped_results_keep_their_status(self):
        self.assertEqual(decision.finalize({"status": "skipped", "issues": []})["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
