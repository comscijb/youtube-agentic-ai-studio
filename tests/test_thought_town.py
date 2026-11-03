"""Phase 3.9.2: narrative QA that understands the brief, against the real Thought Town project (Gemini mocked).

script.pre_revision.json and script.current.json are the versions the automatic revision made worse;
script.edited.json has the author's 18 edits applied (docs/prompts/edits/thought-town-2026-11-03.json).
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agents import human_edits, premise_brief, prose_lint, scriptwriter
from agents.narrative import (
    EDITORIAL_DIMENSIONS,
    NarrativeValidationError,
    clamp_scores,
    deterministic_review,
    load_json,
    merge_editorial_review,
    rebalance_scene_word_targets,
)
from agents.quotes import thought_lines, thought_token_flags
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "thought_town"
BRIEF = load_json(FIXTURES / "story_brief.json")
AUTHOR = BRIEF["author_brief"]
OUTLINE = load_json(FIXTURES / "story_outline.json")
BIBLE = load_json(FIXTURES / "character_bible.json")
TARGET = 12.0


def script(name: str) -> dict:
    return load_json(FIXTURES / f"script.{name}.json")


def review_of(value: dict) -> dict:
    report = deterministic_review(value, TARGET, OUTLINE, None, BRIEF)
    return premise_brief.add_findings(report, BRIEF, bible=BIBLE, outline=OUTLINE, script=value)


def types(report: dict, kind: str) -> list[dict]:
    return [item for item in report["major_issues"] if item["issue_type"] == kind]


def assessment(score=9, brief_check=None) -> dict:
    return {"dimensions": {name: {"score": score, "evidence": ["scene evidence"]} for name in EDITORIAL_DIMENSIONS
                           if name != "strategy_success_then_cost"}
            | {"strategy_success_then_cost": {"score": None, "not_applicable": True, "evidence": ["Type B"]}},
            "findings": [], "strengths": [], "revision_priorities": [], "brief_check": brief_check or []}


class RegressionTests(unittest.TestCase):
    def test_the_fixture_is_the_real_edit(self):
        edits = json.loads((FIXTURES / "edits.json").read_text(encoding="utf-8"))
        applied, _ = human_edits.apply_edits(script("current"), edits)
        self.assertEqual([scene["narration"] for scene in applied["sections"]],
                         [scene["narration"] for scene in script("edited")["sections"]])

    def test_the_edited_script_passes(self):
        edited = script("edited")
        report = review_of(edited)
        self.assertEqual(report["major_issues"], [])
        self.assertEqual(report["brief_paste"], [])
        quote = "What do you actually think, Thomas?"                       # really in scene 8
        answers = [{"item": item["item"], "present": True, "scene_id": 8, "evidence_quote": quote}
                   for item in report["faithfulness_candidates"]]
        self.assertTrue(answers)
        with patch.object(scriptwriter, "generate", return_value=json.dumps(assessment(brief_check=answers))) as call:
            answer = scriptwriter._editorial_review(edited, BRIEF, BIBLE, OUTLINE, report)
        merged = scriptwriter._merge_editorial(report, answer, BIBLE, edited)
        self.assertEqual(merged["status"], "ready_for_human_review")
        self.assertEqual(merged["major_issues"], [])
        self.assertEqual(merged["faithfulness_candidates"], [])
        self.assertEqual(len(merged["faithfulness_verified"]), len(answers))
        self.assertFalse([item for item in merged["minor_issues"] if "candidate" in item])
        prompt = call.call_args.args[0]
        self.assertIn("brief_check", prompt)                                # one call, with the editorial review
        self.assertEqual(call.call_count, 1)

    def test_the_current_script_is_flagged(self):
        report = review_of(script("current"))
        self.assertEqual(report["status"], "revision_required")
        self.assertEqual(sorted({item["scene_ids"][0] for item in types(report, "brief_paste")}), [3, 4])
        chronology = types(report, "chronology")
        self.assertTrue(any(item["scene_ids"] == [4] and "At supper" in item["claim"] for item in chronology))
        self.assertTrue(any(item["scene_ids"] == [4] and "the Line" in item["claim"] for item in chronology))
        self.assertTrue(any(item["scene_ids"] == [3] and "next morning" in item["claim"] for item in chronology))
        self.assertEqual([item["scene_ids"] for item in types(report, "screen_direction")], [[8]])
        self.assertEqual([item["scene_ids"] for item in types(report, "lecture")], [[9]])
        self.assertFalse(types(report, "brief_faithfulness"))               # the false positives are gone

    def test_the_pre_revision_script_is_flagged(self):
        report = review_of(script("pre_revision"))
        self.assertEqual(report["status"], "revision_required")
        self.assertTrue(types(report, "screen_direction"))
        self.assertTrue(types(report, "lecture"))
        self.assertTrue(types(report, "brief_faithfulness"))                 # the term and the thought are absent

    def test_wardrobe_markers_are_minor(self):
        minors = [item for item in review_of(script("current"))["minor_issues"] if item["issue_type"] == "signature_marker"]
        self.assertTrue(any("coat appears in an indoor scene" in item["claim"] and item["scene_ids"] == [5]
                            for item in minors))


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.edited = script("edited")
        self.report = review_of(self.edited)
        self.item = self.report["faithfulness_candidates"][0]["item"]

    def test_lexical_miss_is_only_a_minor(self):
        self.assertEqual(self.report["status"], "human_review_required")
        self.assertTrue(any("unverified" in item["claim"] for item in self.report["minor_issues"]))

    def test_present_false_is_major(self):
        merged = premise_brief.resolve_candidates(
            self.report, [{"item": self.item, "present": False, "scene_id": None, "evidence_quote": ""}], self.edited)
        self.assertEqual(merged["status"], "revision_required")
        self.assertTrue(any(self.item in item["claim"] for item in merged["major_issues"]))

    def test_a_quote_not_in_the_screenplay_is_discarded(self):
        merged = premise_brief.resolve_candidates(
            self.report, [{"item": self.item, "present": True, "scene_id": 1, "evidence_quote": "invented words"}],
            self.edited)
        self.assertEqual(merged["major_issues"], [])
        self.assertTrue(any(item.get("candidate") == self.item for item in merged["minor_issues"]))


class ClassifierTests(unittest.TestCase):
    def test_thought_town(self):
        kinds = {item["line"][:20]: item["kind"] for item in premise_brief.required_lines(AUTHOR)}
        self.assertEqual(kinds, {"I don't want to be h": "thought", "clean thinking": "term",
                                 "what a lovely evenin": "thought", "I've been coming her": "dialogue",
                                 "What do you actually": "dialogue", "There is a thought i": "narrated"})

    def test_the_only_one(self):
        brief = premise_brief.parse(path="briefs/the-only-one.md")
        kinds = {item["line"][:12]: item["kind"] for item in premise_brief.required_lines(brief)}
        self.assertEqual(kinds["That's the b"], "dialogue")
        self.assertEqual(kinds["That must ha"], "dialogue")
        self.assertEqual(kinds["I'm here whe"], "term")                      # no attribution: the lenient kind

    def test_envy_style_beat(self):            # Envy (episode 1) predates briefs: the same shapes, inline
        beat = 'Marcus says "You have everything", and the guests repeat "the perfect couple".'
        self.assertEqual(premise_brief.classify_quote(beat, "You have everything"), "dialogue")
        self.assertEqual(premise_brief.classify_quote("Daniel keeps a \"ledger of envy\" in his desk.", "ledger of envy"), "term")

    def test_kinds_pass_by_their_own_rule(self):
        self.assertTrue(premise_brief._quote_present("thought", "what a lovely evening", "'What a lovely evening.'"))
        self.assertFalse(premise_brief._quote_present("dialogue", "what a lovely evening", "'What a lovely evening.'"))
        self.assertTrue(premise_brief._quote_present("narrated", "no one will ever hear this", "No one will ever hear this."))
        self.assertTrue(premise_brief._quote_present("term", "clean thinking", "They call it clean thinking."))

    def test_style_items_and_stage_directions(self):
        self.assertEqual(premise_brief.style_constraints(AUTHOR), ["heard thoughts as quoted inner lines, quieter than speech"])
        self.assertTrue(premise_brief.is_stage_direction("Cut before he answers"))
        self.assertTrue(premise_brief.is_stage_direction("A long silence; he opens his mouth"))
        self.assertFalse(premise_brief.is_stage_direction("Nell asks quietly"))
        self.assertFalse(any(item["kind"] == "must_include" and "quieter" in item["item"]
                             for item in premise_brief.candidates(AUTHOR, outline=OUTLINE, script=script("edited"))))

    def test_people_filter(self):
        names = [person["name"] for person in premise_brief._people(AUTHOR)]
        self.assertEqual(names, ["Thomas Wren", "Nell Wren"])
        self.assertEqual(premise_brief._people({"other_characters": ["Villagers, never named", "June, his daughter, 41"]})[0]["name"],
                         "June")

    def test_the_convention_is_on_only_where_the_rule_is_about_thoughts(self):
        self.assertTrue(premise_brief.heard_thoughts_on(AUTHOR, "philosophical_thought_experiment"))
        self.assertTrue(premise_brief.heard_thoughts_on({"setting": "A town where anyone hears your thoughts."},
                                                        "philosophical_thought_experiment"))
        self.assertFalse(premise_brief.heard_thoughts_on({"setting": "A town where anyone hears your thoughts."},
                                                         "realistic_psychological_fiction"))
        self.assertFalse(premise_brief.heard_thoughts_on({"setting": "A quiet house."}, "philosophical_thought_experiment"))


class PasteTests(unittest.TestCase):
    def test_authorised_lines_are_exempt(self):
        line = "There is a thought in your head right now that no one will ever hear."
        self.assertEqual(premise_brief.paste_spans(line, AUTHOR), [])

    def test_guard_rejects_and_the_repair_is_told_why(self):
        pasted = "Children learn to think gently; there is no lying, no crime, and Thomas sleeps."
        with self.assertRaises(NarrativeValidationError) as refused:
            scriptwriter._reject_paste(pasted, BRIEF)
        self.assertIn("Dramatise, don't copy", str(refused.exception))
        scriptwriter._reject_paste("The children hum as they think.", BRIEF)

    def test_generated_scene_is_rewritten_once_then_kept(self):
        pasted = "Children learn to think gently; there is no lying, no crime, and Thomas sleeps."
        plans = [{"scene_id": "s03"}]
        clean = [{"scene_id": "s03", "narration": "He hums."}]
        with patch.object(scriptwriter, "_write_scene_batch", return_value=clean) as again:
            result = scriptwriter._rewrite_pasted_scenes([{"scene_id": "s03", "narration": pasted}], BRIEF, BIBLE, OUTLINE, plans, "")
        self.assertEqual(result, clean)
        self.assertEqual(again.call_count, 1)
        self.assertIn("instructions, not prose", again.call_args.args[5])
        still = [{"scene_id": "s03", "narration": pasted}]
        with patch.object(scriptwriter, "_write_scene_batch", return_value=still):
            self.assertEqual(scriptwriter._rewrite_pasted_scenes(still, BRIEF, BIBLE, OUTLINE, plans, ""), still)

    def test_revisions_get_the_brief_as_intent(self):
        block = premise_brief.intent_block(AUTHOR)
        self.assertIn("<intent beat=\"hidden_cost\">", block)
        self.assertIn('<required_line kind="dialogue">What do you actually think, Thomas?</required_line>', block)
        self.assertNotIn("beats", premise_brief.without_beats(BRIEF)["author_brief"])
        finding = {"issue_type": "brief_faithfulness", "evidence": ["the town lives by"], "claim": "x"}
        self.assertEqual(scriptwriter._finding_for_revision(finding)["evidence"], ["<intent>the town lives by</intent>"])


class LintTests(unittest.TestCase):
    def section(self, text, sid=1, setting="a lane"):
        return {"id": sid, "narration": text, "setting": setting, "outline_scene_id": f"s{sid:02d}"}

    def test_screen_directions_are_about_the_edit_not_the_story(self):
        found = prose_lint.screen_direction_findings([self.section(
            "And the story cuts immediately to black. We see the door.")])
        self.assertEqual(len(found), 2)
        fine = ("Her smile fades. He stares at the screen of his phone. An old camera sits on the shelf. "
                '"Cut to black," he joked, "we see everything."')
        self.assertEqual(prose_lint.screen_direction_findings([self.section(fine)]), [])

    def test_lecture_severity_and_exemption(self):
        sections = [self.section("We all want this.", 1), self.section("Plain.", 2), self.section("Plain.", 3),
                    self.section("Perhaps we never knew.", 4)]
        major, minor = prose_lint.lecture_findings(sections)
        self.assertEqual([item["scene_ids"] for item in major], [[4]])
        self.assertEqual([item["scene_ids"] for item in minor], [[1]])
        major, _ = prose_lint.lecture_findings(sections, ["Perhaps we never knew."])
        self.assertEqual(major, [])

    def test_time_markers_and_duplicates(self):
        self.assertEqual(prose_lint.time_markers("The next morning, on Tuesday, later."), ["the next morning", "tuesday", "later"])
        found = prose_lint.chronology_findings([self.section("The next morning he rose. He ate. Next morning he slept.")], None)
        self.assertEqual(found[0]["issue_type"], "chronology")
        self.assertEqual(prose_lint.chronology_findings([self.section("Next morning he rose. Later he slept.")], None), [])

    def test_exterior_keywords(self):
        self.assertIs(prose_lint.is_exterior("The open moorland at night"), True)
        self.assertIs(prose_lint.is_exterior("The kitchen of the Wren home"), False)
        self.assertIs(prose_lint.is_exterior("Outside the bakery along the stone lane"), True)
        self.assertIsNone(prose_lint.is_exterior("somewhere"))


class BudgetTests(unittest.TestCase):
    def test_brief_scenes_get_40_to_110_and_the_rest_is_redistributed(self):
        plan = json.loads(json.dumps(OUTLINE))
        plan["scenes"][-1]["pace"] = "brief"
        balanced = rebalance_scene_word_targets(plan, 1500)
        self.assertEqual(sum(scene["target_words"] for scene in balanced["scenes"]), 1500)
        self.assertLessEqual(balanced["scenes"][-1]["target_words"], 110)
        self.assertGreaterEqual(balanced["scenes"][-1]["target_words"], 40)
        self.assertGreater(balanced["scenes"][0]["target_words"], OUTLINE["scenes"][0]["target_words"])

    def test_an_abrupt_ending_excuses_the_short_last_scene_but_not_a_long_one(self):
        report = review_of(script("edited"))
        last = report["scene_budgets"][-1]
        self.assertEqual((last["pace"], last["status"], last["actual_words"]), ("brief", "within_target", 79))
        padded = script("edited")
        padded["sections"][-1]["narration"] = " ".join(["word"] * 150)
        budget = deterministic_review(padded, TARGET, OUTLINE, None, BRIEF)["scene_budgets"][-1]
        self.assertEqual(budget["status"], "over_target")

    def test_abrupt_detection_marks_only_when_the_brief_asks(self):
        self.assertTrue(premise_brief.abrupt_ending(AUTHOR))
        self.assertFalse(premise_brief.abrupt_ending({"beats": {"ending": "He lets her interrupt him."}}))

    def test_no_expansion_of_a_brief_scene(self):
        short = script("edited")                       # total in range: no expansion call at all
        with patch.object(scriptwriter, "generate", side_effect=AssertionError("no call")):
            revised, attempts = scriptwriter._expand_underlength_scenes(short, BRIEF, BIBLE, OUTLINE)
        self.assertEqual(attempts, [])
        self.assertIs(revised, short)
        under = script("current")                      # total below range: scenes 7, 8 yes; brief scene 9 never
        with patch.object(scriptwriter, "_expansion_pass", side_effect=lambda r, batch, *a: (r, {
                "scene_ids": [item["scene_id"] for item in batch], "kind": "x"})) as expanded:
            _, attempts = scriptwriter._expand_underlength_scenes(under, BRIEF, BIBLE, OUTLINE)
        self.assertEqual([attempt["scene_ids"] for attempt in attempts], [[7, 8]])
        self.assertEqual(expanded.call_count, 1)

    def test_a_scene_still_short_after_its_attempt_is_minor_with_the_reason(self):
        typed = script("edited")
        typed["narrative_type"] = "realistic_psychological_fiction"
        typed["sections"][1]["narration"] = " ".join(["word"] * 50)
        before = deterministic_review(typed, TARGET, OUTLINE, None, BRIEF)
        self.assertTrue(any(item["issue_type"] == "scene_word_budget" and item["scene_ids"] == [2]
                            for item in before["major_issues"]))
        after = deterministic_review(typed, TARGET, OUTLINE, [{"scene_ids": [2], "kind": "targeted_substantive_expansion"}], BRIEF)
        note = next(item for item in after["minor_issues"] if item.get("scene_ids") == [2])
        self.assertIn("one expansion attempt", note["claim"])

    def test_type_b_short_scene_is_fine_when_the_total_holds(self):
        typed = script("edited")
        typed["sections"][1]["narration"] = " ".join(["word"] * 100)
        report = deterministic_review(typed, TARGET, OUTLINE, None, BRIEF)
        self.assertTrue(report["deterministic_checks"]["total_word_budget"])
        self.assertFalse([item for item in report["major_issues"] if item["issue_type"] == "scene_word_budget"])


class ClampTests(unittest.TestCase):
    def test_score_is_clamped_and_recorded(self):
        report = review_of(script("current"))
        merged = merge_editorial_review(report, assessment(10), BIBLE)
        scores = merged["editorial_assessment"]["dimensions"]
        self.assertEqual(scores["chronological_continuity"]["score"], 6)
        self.assertEqual(scores["dialogue_and_subtext"]["score"], 6)
        self.assertEqual(scores["literary_style"]["score"], 6)
        self.assertEqual(scores["hook_strength"]["score"], 10)
        self.assertIn("chronological_continuity", {item["dimension"] for item in merged["score_clamps"]})

    def test_no_clamp_without_a_finding_and_nothing_mutated(self):
        original = assessment(10)
        clamped, record = clamp_scores(original, [])
        self.assertEqual(record, [])
        self.assertEqual(clamped, original)
        clamped, record = clamp_scores(original, [{"issue_type": "chronology", "claim": "x"}])
        self.assertEqual(original["dimensions"]["chronological_continuity"]["score"], 10)
        self.assertEqual(record[0]["from"], 10)

    def test_the_prompt_says_so_and_carries_the_checklist(self):
        report = review_of(script("current"))
        with patch.object(scriptwriter, "generate", return_value=json.dumps(assessment())) as call:
            scriptwriter._editorial_review(script("current"), BRIEF, BIBLE, OUTLINE, report)
        prompt = call.call_args.args[0]
        self.assertIn("cannot score above 6", prompt)
        self.assertIn("confirmed_findings", prompt)
        self.assertIn("checklist", prompt)
        self.assertIn("spent_reversal", prompt)
        self.assertIn("role_slip", prompt)


class QuoteTests(unittest.TestCase):
    def test_apostrophes_are_not_quotes(self):
        text = "Wickham's baker can't sleep. 'I don't want to be here,' he thinks. The Wrens' door opens."
        self.assertEqual(thought_lines(text), ["I don't want to be here,"])

    def test_double_quotes_hold_no_thoughts(self):
        self.assertEqual(thought_lines("\"I said 'no' to him,\" she says."), [])
        self.assertEqual(thought_lines("“It’s fine,” she says. ‘It isn’t.’"), ["It isn’t."])

    def test_token_flags(self):
        self.assertEqual(thought_token_flags("He thinks 'I can't go' and stops."),
                         [False, False, True, True, True, False, False])


if __name__ == "__main__":
    unittest.main()
