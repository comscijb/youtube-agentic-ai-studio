"""Phase 2.2.1 section 3: no stacked modifiers (prompt rule + deterministic lint)."""
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents import identity, scriptwriter
from agents.narrative import deterministic_review, stacked_modifier_lint
from tests.test_narrative import bible, outline, scene_batch, screenplay_with_counts, story_brief
from tests.test_review_reliability import assessment

FLAGGED = (
    "He looked at him not with sorrow, but with pure, unmitigated estrangement.",
    "It was not anger in her gaze. It was unadulterated, sorrowful pity.",
    "Julian answered with a devastating, unhurried calm.",
    "The room fell into an utterly devastating silence.",
)
PLAIN = (
    "She turned, nodding slowly.",
    "He bought bread, milk and eggs.",
    "In the evening, he waited by the door.",
    "The house was quiet, and the clock ticked.",
    "Julian smiled, lifting his glass toward the guests.",
    "It was cold, damp and late.",
    "Marcus was tired, then angry.",
)


def lint(text):
    return stacked_modifier_lint([{"id": 1, "narration": text}], threshold=3)


def script_with(narrations):
    script = screenplay_with_counts([80] * len(narrations), [80] * len(narrations))
    for scene, text in zip(script["sections"], narrations):
        scene["narration"] = text + " " + " ".join(["word"] * (80 - len(text.split())))
    script["title"] = "He Waited Twenty Years"
    return script


class StackedModifierLintTests(unittest.TestCase):
    def test_brief_examples_are_flagged(self):
        expected = ("pure, unmitigated estrangement", "unadulterated, sorrowful pity",
                    "devastating, unhurried calm", "utterly devastating silence")
        for text, example in zip(FLAGGED, expected):
            with self.subTest(text=text):
                result = lint(text)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["per_scene"]["1"]["examples"], [example])

    def test_plain_sentences_do_not_trigger(self):
        for text in PLAIN:
            with self.subTest(text=text):
                self.assertEqual(lint(text)["total"], 0)

    def test_lone_intensifier_counts_once(self):
        result = lint("The suit was impeccable.")
        self.assertEqual((result["per_scene"]["1"]["intensifiers"], result["total"]), (1, 1))

    def test_threshold_emits_minor_style_finding_only(self):
        heavy = script_with([FLAGGED[0] + " " + FLAGGED[1], FLAGGED[2] + " " + FLAGGED[3],
                             "He sat down."])
        report = deterministic_review(heavy, 240 / 125)
        self.assertEqual(report["style_lint"]["total"], 4)
        self.assertEqual(report["style_lint"]["per_scene"]["3"]["total"], 0)
        issue = report["minor_issues"][0]
        self.assertEqual((issue["severity"], issue["issue_type"]), ("minor", "style"))
        self.assertEqual(issue["scene_ids"], [1, 2])
        self.assertIn("scene 1: pure, unmitigated estrangement", issue["evidence"])
        self.assertFalse(any(item["issue_type"] == "style"
                             for item in report["blocking_issues"] + report["major_issues"]))
        self.assertEqual(report["status"], "human_review_required")

    def test_at_threshold_no_finding(self):
        report = deterministic_review(script_with(list(FLAGGED[:3])), 240 / 125)
        self.assertEqual(report["style_lint"]["total"], 3)
        self.assertEqual(report["minor_issues"], [])


class StyleRulePromptTests(unittest.TestCase):
    def test_rule_in_identity_and_generation_prompts(self):
        self.assertIn(identity.STACKED_MODIFIER_RULE, identity.CRAFT_RULES)
        with patch.object(scriptwriter, "generate", return_value=json.dumps(scene_batch())) as call, \
                redirect_stdout(io.StringIO()):
            scriptwriter._write_scene_batch(story_brief(), bible(), outline(), outline()["scenes"], "")
        self.assertIn(identity.STACKED_MODIFIER_RULE, call.call_args.args[0])
        source = Path("agents/scriptwriter.py").read_text(encoding="utf-8")
        self.assertIn("unsupported twists. {STACKED_MODIFIER_RULE}", source)   # expansion
        self.assertIn('identity_block(brief.get("narrative_type"))', source)    # revision

    def test_lint_counts_reach_the_editor(self):
        report = deterministic_review(script_with(list(FLAGGED[:3])), 240 / 125)
        with patch.object(scriptwriter, "generate", return_value=json.dumps(assessment([]))) as call, \
                redirect_stdout(io.StringIO()):
            scriptwriter._editorial_review({"sections": []}, story_brief(), bible(), outline(), report)
        prompt = call.call_args.args[0]
        self.assertIn('"style_lint":{"total":3', prompt)
        self.assertIn("devastating, unhurried calm", prompt)


if __name__ == "__main__":
    unittest.main()
