"""Phase 2.2 channel identity: title policy, arc movements, identity prompts, review dims."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents import identity, researcher, scriptwriter
from agents.narrative import (
    EDITORIAL_DIMENSIONS,
    LEGACY_EDITORIAL_DIMENSIONS,
    NarrativeValidationError,
    deterministic_review,
    merge_editorial_review,
    missing_arc_movements,
    select_title,
    validate_editorial_assessment,
    validate_outline,
    validate_story_brief,
    validate_thumbnail_text,
    validate_title,
)
from tests.test_narrative import bible, outline, scene_batch, screenplay_with_counts, story_brief

ENVY = {"psychological_theme": "Envy and resentment"}
WEAK = (
    "The Psychology of Envy: A Story About Social Comparison",
    "The Psychology of People Pleasing",
    "Why Success Doesn't Make You Happy",
    "The Architecture of Envy",
    "The Poison of Being Envied",
)
STRONG = (
    "He Secretly Wanted His Best Friend to Fail",
    "She Said Yes to Everyone for 20 Years",
    "He Got Everything He Wanted at 37",
    "The Man Who Needed Everyone to Envy Him",
)


def blocking(findings):
    return [item for item in findings if item["severity"] == "blocking"]


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def assessment(narrative_type="realistic_psychological_fiction", keys=EDITORIAL_DIMENSIONS):
    dimensions = {name: {"score": 7, "evidence": ["Scene 2 shows it."]} for name in keys}
    if narrative_type != "realistic_psychological_fiction" and "strategy_success_then_cost" in keys:
        dimensions["strategy_success_then_cost"] = {
            "score": None, "not_applicable": True, "evidence": ["Parable without a strategy arc."],
        }
    return {"dimensions": dimensions, "findings": [], "strengths": [], "revision_priorities": []}


class TitlePolicyTests(unittest.TestCase):
    def test_weak_examples_are_rejected(self):
        for title in WEAK:
            with self.subTest(title=title):
                self.assertTrue(blocking(validate_title(title, ENVY)))

    def test_strong_examples_are_accepted(self):
        for title in STRONG:
            with self.subTest(title=title):
                self.assertEqual(validate_title(title, ENVY), [])

    def test_specific_rules(self):
        rules = lambda title: {item["rule"] for item in blocking(validate_title(title, ENVY))}
        self.assertIn("colon_subtitle", rules("He Lied: A Family Story"))
        self.assertIn("thesis_pattern", rules("What Happens When He Wins"))
        self.assertIn("abstract_noun_form", rules("The Anatomy of a Promise"))
        self.assertIn("names_theme", rules("Envious"))
        self.assertIn("too_long", rules("He " + "really " * 12 + "wanted it"))

    def test_theme_word_inside_quoted_line_is_accepted(self):
        self.assertEqual(blocking(validate_title('"I Envy You," She Said at the Funeral', ENVY)), [])
        self.assertTrue(blocking(validate_title("Envy at the Funeral", ENVY)))

    def test_shorts_suffix_is_ignored(self):
        self.assertEqual(validate_title("He Got Everything He Wanted at 37 #Shorts", ENVY), [])
        title = "He Got Everything He Wanted at Thirty-Seven, Then Lost It #Shorts"
        self.assertEqual(blocking(validate_title(title, ENVY)), [])

    def test_missing_human_anchor_is_major_only(self):
        findings = validate_title("Glass Houses Never Sleep", ENVY)
        self.assertEqual([(item["severity"], item["rule"]) for item in findings],
                         [("major", "no_human_anchor")])

    def test_thumbnail_text_policy(self):
        self.assertEqual(validate_thumbnail_text("I hope he loses.", ENVY), [])
        self.assertEqual(validate_thumbnail_text("Now what?", ENVY), [])
        rules = {item["rule"] for item in validate_thumbnail_text("My envy is six words #x", ENVY)}
        self.assertEqual(rules, {"thumbnail_too_long", "thumbnail_hashtag", "thumbnail_names_theme"})

    def test_fixture_regression_v1_title_fails_requested_premise_passes(self):
        fixture = json.loads(Path("tests/fixtures/architecture_of_envy_benchmark.json")
                             .read_text(encoding="utf-8"))
        theme = {"psychological_theme": "Pathological envy, mimetic desire"}
        self.assertTrue(blocking(validate_title(fixture["title"], theme)))
        self.assertEqual(validate_title(fixture["requested_premise"], theme), [])
        self.assertTrue(blocking(validate_title("The Cost of Being Looked At", theme)))

    def test_select_title_picks_first_passing_candidate(self):
        brief = dict(ENVY, video_title="The Architecture of Envy", title_candidates=[
            {"title": "The Poison of Being Envied", "rationale": "x"},
            {"title": "He Secretly Wanted His Best Friend to Fail", "rationale": "y"},
        ], thumbnail_text_candidates=["Envy everywhere", "I hope he loses."])
        review = select_title(brief)
        self.assertEqual(review["status"], "passed")
        self.assertEqual(review["selected_title"], "He Secretly Wanted His Best Friend to Fail")
        self.assertEqual(review["selected_thumbnail_text"], "I hope he loses.")
        self.assertEqual(len(review["title_candidates"]), 3)


class ResearcherTitleTests(unittest.TestCase):
    def run_research(self, responses):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
             patch.object(researcher, "generate", side_effect=responses) as generated:
            brief = quiet(researcher.research_topic, "channel", topic_override="P",
                          target_duration_minutes=12)
            saved = json.loads(Path(temp, "story_brief.json").read_text(encoding="utf-8"))
        return brief, saved, generated

    def test_failed_titles_get_one_bounded_repair(self):
        payload = story_brief()
        payload.update(video_title="The Architecture of Regret",
                       title_candidates=[{"title": "Why Regret Wins", "rationale": "x"}],
                       thumbnail_text_candidates=["Regret"])
        repaired = {"title_candidates": [{"title": "He Waited Twenty Years to Say It",
                                          "rationale": "anchor"}],
                    "thumbnail_text_candidates": ["Say it now."]}
        brief, saved, generated = self.run_research([json.dumps(payload), json.dumps(repaired)])
        self.assertEqual(generated.call_count, 2)
        repair_prompt = generated.call_args_list[1].args[0]
        self.assertIn("Why Regret Wins", repair_prompt)
        self.assertIn("TITLE POLICY", repair_prompt)
        self.assertEqual(brief["video_title"], "He Waited Twenty Years to Say It")
        self.assertEqual(brief["thumbnail_text"], "Say it now.")
        self.assertEqual(saved["title_review"]["status"], "passed")
        self.assertTrue(saved["title_review"]["repair_attempted"])

    def test_titles_still_failing_mark_revision_required(self):
        payload = story_brief()
        payload.update(video_title="The Architecture of Regret", thumbnail_text_candidates=["x"])
        bad = {"title_candidates": [{"title": "The Truth About Regret", "rationale": "x"}],
               "thumbnail_text_candidates": ["ok then"]}
        brief, _, _ = self.run_research([json.dumps(payload), json.dumps(bad)])
        self.assertEqual(brief["title_review"]["status"], "revision_required")
        self.assertTrue(blocking(brief["title_review"]["selected_title_findings"]))


class ArcMovementTests(unittest.TestCase):
    def tagged_outline(self, drop=()):
        value = outline()
        movements = [m for m in identity.ARC_MOVEMENTS if m not in drop]
        value["scenes"][0]["arc_movements"] = movements[:3]
        value["scenes"][1]["arc_movements"] = movements[3:6]
        value["scenes"][2]["arc_movements"] = movements[6:]
        return value

    def review(self, narrative_type, plan):
        script = screenplay_with_counts([80, 80, 80], [80, 80, 80])
        script["narrative_type"] = narrative_type
        script["title"] = "He Waited Twenty Years"
        return deterministic_review(script, 240 / 125, plan)

    def test_type_a_missing_movement_is_major(self):
        report = self.review("realistic_psychological_fiction",
                             self.tagged_outline(drop=("escalation_success",)))
        self.assertEqual(report["missing_arc_movements"], ["escalation_success"])
        issue = next(item for item in report["major_issues"] if item["issue_type"] == "arc_movements")
        self.assertIn("escalation_success", issue["claim"])
        self.assertFalse(any(item["issue_type"] == "arc_movements"
                             for item in report["blocking_issues"]))

    def test_complete_type_a_and_types_b_c_have_no_finding(self):
        self.assertEqual(self.review("realistic_psychological_fiction",
                                     self.tagged_outline())["missing_arc_movements"], [])
        for narrative_type in ("philosophical_thought_experiment", "dark_human_parable"):
            self.assertEqual(missing_arc_movements(
                self.tagged_outline(drop=("escalation_success",)), narrative_type), [])

    def test_old_outline_without_field_stays_valid_and_unflagged(self):
        validate_outline(outline(), bible(), True)
        self.assertEqual(missing_arc_movements(outline(), "realistic_psychological_fiction"), [])

    def test_unknown_movement_is_invalid(self):
        value = self.tagged_outline()
        value["scenes"][1]["arc_movements"] = ["twist"]
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_outline(value, bible(), True)
        self.assertIn("scene s2: arc_movements has unknown values: twist", str(caught.exception))

    def test_review_records_title_findings(self):
        report = self.review("dark_human_parable", outline())
        report_bad = deterministic_review(
            dict(screenplay_with_counts([80, 80, 80]), title="The Anatomy of Regret"), 240 / 125)
        self.assertTrue(report["title_review"]["passes"])
        self.assertFalse(report_bad["title_review"]["passes"])
        self.assertEqual(report_bad["status"], "revision_required")


class IdentityPromptTests(unittest.TestCase):
    MARKER = "PRIVATE GRAVITY IDENTITY"

    def test_identity_in_all_four_prompts(self):
        brief = validate_story_brief(story_brief())
        prompts = {}
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            with patch.object(researcher, "generate", return_value=json.dumps(story_brief())) as call:
                quiet(researcher.research_topic, "channel", topic_override="P",
                      target_duration_minutes=12)
            prompts["researcher"] = call.call_args_list[0].args[0]
            with patch.object(scriptwriter, "generate", return_value=json.dumps(outline())) as call:
                quiet(scriptwriter._outline, brief, bible(), "normal", 12, Path(temp), True)
            prompts["outline"] = call.call_args.args[0]
        with patch.object(scriptwriter, "generate", return_value=json.dumps(scene_batch())) as call:
            quiet(scriptwriter._write_scene_batch, brief, bible(), outline(),
                  outline()["scenes"], "")
        prompts["scenes"] = call.call_args.args[0]
        with patch.object(scriptwriter, "generate", return_value=json.dumps(assessment())) as call:
            quiet(scriptwriter._editorial_review, {"sections": []}, brief, bible(), outline())
        prompts["editorial"] = call.call_args.args[0]
        for name, prompt in prompts.items():
            with self.subTest(prompt=name):
                self.assertIn(self.MARKER, prompt)
                self.assertIn(identity.CHANNEL_PROMISE, prompt)
                self.assertIn("escalation_success", prompt)
        self.assertIn("TITLE POLICY", prompts["researcher"])
        self.assertIn("title_candidates", prompts["researcher"])
        self.assertIn("arc_movements is REQUIRED", prompts["outline"])
        self.assertIn("title_curiosity", prompts["editorial"])
        # Contract (with identity) precedes context.
        self.assertLess(prompts["outline"].index(self.MARKER),
                        prompts["outline"].index("STORY BRIEF:"))

    def test_identity_block_scopes_to_type(self):
        text = identity.identity_block("dark_human_parable")
        self.assertIn("Dark Human Parable", text)
        self.assertNotIn("TYPE A MOVEMENTS", text)


class EditorialDimensionTests(unittest.TestCase):
    def test_new_dimension_set_is_accepted(self):
        self.assertEqual(len(EDITORIAL_DIMENSIONS), 21)
        validate_editorial_assessment(assessment(), "realistic_psychological_fiction",
                                      allow_legacy=False)

    def test_old_15_key_reports_still_read(self):
        old = assessment(keys=LEGACY_EDITORIAL_DIMENSIONS)
        validate_editorial_assessment(old)
        merged = merge_editorial_review(
            deterministic_review(screenplay_with_counts([80, 80, 80]), 240 / 125), old)
        self.assertTrue(merged["editorial_validation_complete"])
        with self.assertRaises(NarrativeValidationError):
            validate_editorial_assessment(old, allow_legacy=False)

    def test_not_applicable_only_for_non_type_a(self):
        validate_editorial_assessment(assessment("dark_human_parable"), "dark_human_parable")
        with self.assertRaises(NarrativeValidationError):
            validate_editorial_assessment(assessment("dark_human_parable"),
                                          "realistic_psychological_fiction")
        broken = assessment("dark_human_parable")
        broken["dimensions"]["hook_strength"] = {"score": None, "not_applicable": True,
                                                 "evidence": ["n/a"]}
        with self.assertRaises(NarrativeValidationError):
            validate_editorial_assessment(broken, "dark_human_parable")

    def test_missing_dimensions_are_named(self):
        partial = assessment()
        del partial["dimensions"]["hook_strength"]
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_editorial_assessment(partial, allow_legacy=False)
        self.assertIn("hook_strength", str(caught.exception))


class GuiTitleTests(unittest.TestCase):
    def test_validate_title_route(self):
        import gui
        with patch.dict(gui._state, {"research": dict(ENVY), "script": None}):
            client = gui.app.test_client()
            weak = client.post("/api/validate-title", json={"title": "The Architecture of Envy"})
            strong = client.post("/api/validate-title", json={"title": STRONG[0]})
        self.assertFalse(weak.get_json()["passes"])
        self.assertTrue(strong.get_json()["passes"])


if __name__ == "__main__":
    unittest.main()
