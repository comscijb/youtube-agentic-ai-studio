import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import config
from agents import researcher, scriptwriter
from agents.narrative import (
    EDITORIAL_DIMENSIONS,
    NarrativeValidationError,
    deterministic_review,
    invalidate_downstream,
    invalidate_scene_outputs,
    merge_editorial_review,
    normalize_character_bible,
    normalize_outline,
    normalize_script,
    rebalance_scene_word_targets,
    stable_hash,
    validate_character_bible,
    validate_outline,
    validate_script,
    validate_story_brief,
)
from video.timeline import (
    build_scene_timeline,
    proportional_scene_durations,
    validate_timeline,
    write_shifted_srt,
)


def story_brief(narrative_type="realistic_psychological_fiction"):
    return {
        "premise_candidates": [{
            "premise": "A night guard discovers his replacement is his younger self.",
            "narrative_type": narrative_type,
            "originality_score": 9, "emotional_power_score": 8,
            "dramatic_potential_score": 9, "visual_feasibility_score": 8,
            "audience_access_score": 8, "weighted_total": 8.55,
        }],
        "selected_candidate_index": 0,
        "video_title": "The Man Who Replaced Himself",
        "narrative_type": narrative_type,
        "psychological_theme": "Regret and alternative lives",
        "psychological_angle": "Trying to undo regret by controlling a younger self",
        "logline": "A night guard discovers his replacement is his younger self.",
        "main_character_concept": "A guarded middle-aged night watchman",
        "central_dramatic_question": "Can he stop the choice that made him lonely?",
        "protagonist_desire": "To correct his past",
        "underlying_emotional_need": "To accept responsibility",
        "principal_conflict": "His younger self distrusts him",
        "central_contradiction": "He seeks freedom by controlling another self",
        "stakes": "Both versions may lose their lives",
        "potential_turning_point": "The younger man repeats the decisive mistake",
        "intended_emotional_experience": "Recognition, dread, and quiet release",
        "philosophical_question": "Can regret exist without the fantasy of control?",
        "ending_direction": "Bittersweet acceptance",
        "intended_audience": "Adults drawn to intimate speculative drama",
        "thumbnail_concept": "Two ages of one man divided by a security monitor",
        "originality_assessment": "A personal time loop centered on accountability",
        "production_feasibility": "Night interiors, silhouettes, clocks and monitors",
        "tags": ["Private Gravity", "Psychological Fiction"],
        "thumbnail_text_candidates": ["Not again."],
    }


def bible():
    return {"characters": [{
        "character_id": "eli", "name": "Eli", "role": "protagonist", "age": "47",
        "background": "A night guard", "personality": "Reserved and observant",
        "core_desire": "Change his past", "underlying_need": "Accept responsibility",
        "central_fear": "A wasted life", "psychological_wound": "An abandoned relationship",
        "internal_contradiction": "Controls others to feel free", "behavioral_patterns": "Checks every lock twice",
        "relationships": [], "initial_state": "Numb", "development_arc": "Control to acceptance",
        "final_state": "Present", "visual_identity": "Grey coat and old watch",
        "distinctive_details": ["worn watch"],
    }], "speculative_rules": ["The monitor connects one night across twenty years"]}


def outline():
    functions = ["opening setup", "decisive confrontation", "resolution aftermath"]
    types = ["opening", "climax", "resolution"]
    return {"scenes": [{
        "scene_id": f"s{i}", "order": i, "title": f"Scene {i}",
        "section_type": types[i - 1],
        "narrative_function": functions[i - 1], "summary": "A consequence changes Eli's choice.",
        "setting": "Empty office at night", "characters_present": ["eli"],
        "initial_dramatic_situation": "Eli faces a choice he has avoided.",
        "character_objective": "Force an answer before dawn.",
        "obstacle": "The other Eli refuses his premise.",
        "dramatic_action": "Eli tests the monitor and confronts his counterpart.",
        "decision": f"Eli chooses disclosure over control in phase {i}.",
        "consequence": f"The choice removes escape route {i}.",
        "new_information": "The monitor reflects consequences, not alternatives.",
        "chronology_marker": f"Hour {i} before dawn",
        "continuity_updates": [f"Eli learns fact {i}"],
        "dramatic_function": "Escalate the confrontation", "emotional_turn": "Certainty becomes doubt",
        "causal_link": "His action causes the next scene", "target_words": 80,
        "visual_direction": "Cold monitor light and negative space",
        "image_queries": ["empty office night", "worn wristwatch close up", "man silhouette monitor"],
    } for i in range(1, 4)]}


def scene_batch():
    narration = " ".join(["Eli watched the silent monitor as the old clock divided his life again."] * 5)
    return {"scenes": [{
        "scene_id": f"s{i}", "title": f"Scene {i}",
        "section_type": ["opening", "climax", "resolution"][i - 1],
        "narrative_function": ["opening setup", "decisive confrontation", "resolution aftermath"][i - 1],
        "narration": narration, "setting": "Empty office", "characters_present": ["eli"],
        "dramatic_function": "Escalation", "emotional_tone": "Unease",
        "visual_direction": "A slow push through shadow", "image_query": "empty office at night",
        "image_query_2": "worn watch close up", "image_query_3": "man silhouette monitor",
        "caption_text": "", "on_screen_text": "",
    } for i in range(1, 4)]}


def screenplay_with_counts(counts, targets=None, video_type="normal"):
    targets = targets or counts
    types = ["opening"] + ["development"] * max(0, len(counts) - 3) + ["climax", "resolution"]
    sections = []
    for index, (actual, target) in enumerate(zip(counts, targets), 1):
        sections.append({
            "id": index, "outline_scene_id": f"s{index:02}", "title": f"Scene {index}",
            "section_type": types[index - 1], "narrative_function": f"Function {index}",
            "narration": " ".join(f"word{n}" for n in range(actual)),
            "target_words": target, "setting": "A specific room",
            "characters_present": ["eli"], "dramatic_function": "A choice changes the situation",
            "emotional_tone": "Controlled unease", "visual_direction": "A precise visual action",
            "image_query": "room dramatic action", "image_query_2": "watch close up",
            "image_query_3": "character silhouette", "bullet_points": [],
        })
    return validate_script({
        "title": "Measured Story", "description": "Original fiction", "video_type": video_type,
        "narrative_type": "realistic_psychological_fiction", "psychological_theme": "Regret",
        "planned_words_per_minute": 125, "sections": sections,
    }, validate_character_bible(bible()))


def editorial_assessment(findings=None, score=8):
    return {
        "dimensions": {
            name: {"score": score, "evidence": ["Scene 1 contains observable evidence."]}
            for name in EDITORIAL_DIMENSIONS
        },
        "findings": findings or [], "strengths": [], "revision_priorities": [],
    }


class NarrativeContractTests(unittest.TestCase):
    @patch("agents.researcher._score_titles", new=lambda brief: None)   # scoring has its own tests
    def test_automatic_premise_generation_uses_psychological_theme(self):
        payload = story_brief()
        payload["premise_candidates"] = [
            dict(payload["premise_candidates"][0], premise=f"Premise {index}")
            for index in range(4)
        ]
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(config, "OUTPUT_DIR", temp), \
             patch.object(researcher, "generate", return_value=json.dumps(payload)) as generated:
            result = researcher.research_topic(
                "channel", focus_angle="Loneliness and belonging", narrative_type="auto"
            )
        self.assertFalse(result["exact_premise_override"])
        self.assertEqual(len(result["premise_candidates"]), 4)
        self.assertIn("Loneliness and belonging", generated.call_args.args[0])

    def test_exact_premise_and_explicit_type_are_authoritative(self):
        payload = story_brief("dark_human_parable")
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(config, "OUTPUT_DIR", temp), \
             patch.object(researcher, "generate", return_value=json.dumps(payload)):
            result = researcher.research_topic(
                "channel", topic_override="My exact premise",
                narrative_type="philosophical_thought_experiment", target_duration_minutes=15,
            )
        self.assertEqual(result["topic"], "My exact premise")
        self.assertEqual(result["requested_premise"], "My exact premise")
        self.assertEqual(result["narrative_type"], "philosophical_thought_experiment")

    def test_all_narrative_types_validate(self):
        for narrative_type in (
            "realistic_psychological_fiction", "philosophical_thought_experiment",
            "dark_human_parable",
        ):
            self.assertEqual(validate_story_brief(story_brief(narrative_type))["schema_version"], 2)

    def test_character_bible_accepts_canonical_behavioral_pattern_string(self):
        result = validate_character_bible(bible())
        self.assertEqual(result["characters"][0]["behavioral_patterns"], "Checks every lock twice")

    def test_character_bible_normalizes_behavioral_pattern_array(self):
        value = bible()
        value["characters"][0]["behavioral_patterns"] = [
            " Avoids difficult conversations ",
            "Checks his phone for social approval",
        ]
        result = validate_character_bible(value)
        self.assertEqual(
            result["characters"][0]["behavioral_patterns"],
            "Avoids difficult conversations; Checks his phone for social approval",
        )
        # Normalization returns a copy and does not rewrite the caller's data.
        self.assertIsInstance(value["characters"][0]["behavioral_patterns"], list)

    def test_behavior_array_normalization_avoids_repair_api_call(self):
        value = bible()
        value["characters"][0]["behavioral_patterns"] = ["Avoids conflict", "Counts exits"]
        with patch.object(scriptwriter, "generate", return_value=json.dumps(value)) as generated:
            result = scriptwriter._generate_json(
                "character contract", "character bible", validate_character_bible
            )
        self.assertEqual(generated.call_count, 1)
        self.assertEqual(result["characters"][0]["behavioral_patterns"],
                         "Avoids conflict; Counts exits")

    def test_character_bible_normalization_trims_without_inventing_content(self):
        value = bible()
        value["characters"][0]["name"] = "  Eli  "
        value["characters"][0]["age"] = 47
        normalized = normalize_character_bible(value)
        self.assertEqual(normalized["characters"][0]["name"], "Eli")
        self.assertEqual(normalized["characters"][0]["age"], "47")
        self.assertEqual(value["characters"][0]["name"], "  Eli  ")

    def test_character_bible_rejects_missing_empty_and_null_behavioral_patterns(self):
        cases = []
        missing = bible()
        del missing["characters"][0]["behavioral_patterns"]
        cases.append((missing, "missing field", "missing"))
        empty_string = bible()
        empty_string["characters"][0]["behavioral_patterns"] = "   "
        cases.append((empty_string, "empty required field", "str"))
        empty_list = bible()
        empty_list["characters"][0]["behavioral_patterns"] = []
        cases.append((empty_list, "empty required field", "empty list"))
        null_value = bible()
        null_value["characters"][0]["behavioral_patterns"] = None
        cases.append((null_value, "incorrect field type", "null"))

        for value, problem, received in cases:
            with self.subTest(problem=problem, received=received):
                with self.assertRaises(NarrativeValidationError) as caught:
                    validate_character_bible(value)
                message = str(caught.exception)
                self.assertIn("character[0].behavioral_patterns", message)
                self.assertIn(f"Received: {received}", message)
                self.assertIn(f"Problem: {problem}", message)

    def test_character_bible_rejects_invalid_behavior_array_elements(self):
        value = bible()
        value["characters"][0]["behavioral_patterns"] = ["Avoids conflict", 42]
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_character_bible(value)
        self.assertIn("Received: list[int, str]", str(caught.exception))
        self.assertIn("invalid array element type", str(caught.exception))

    def test_targeted_semantic_repair_preserves_valid_character_data(self):
        invalid = bible()
        del invalid["characters"][0]["behavioral_patterns"]
        repaired = bible()
        repaired["characters"][0]["behavioral_patterns"] = [
            "Avoids eye contact", "Checks every lock twice"
        ]
        with patch.object(
            scriptwriter, "generate",
            side_effect=[json.dumps(invalid), json.dumps(repaired)],
        ) as generated:
            result = scriptwriter._generate_json(
                "character contract", "character bible", validate_character_bible
            )
        self.assertEqual(generated.call_count, 2)
        repair_prompt = generated.call_args_list[1].args[0]
        self.assertIn("CURRENT VALID JSON", repair_prompt)
        self.assertIn("character[0].behavioral_patterns", repair_prompt)
        self.assertNotIn("ORIGINAL CONTRACT", repair_prompt)
        self.assertEqual(result["characters"][0]["name"], invalid["characters"][0]["name"])
        self.assertEqual(result["characters"][0]["core_desire"], invalid["characters"][0]["core_desire"])
        self.assertEqual(
            result["characters"][0]["behavioral_patterns"],
            "Avoids eye contact; Checks every lock twice",
        )

    def test_failed_targeted_repair_raises_clear_bounded_error(self):
        invalid = bible()
        invalid["characters"][0]["behavioral_patterns"] = None
        with patch.object(
            scriptwriter, "generate",
            side_effect=[json.dumps(invalid), json.dumps(invalid)],
        ) as generated:
            with self.assertRaises(NarrativeValidationError) as caught:
                scriptwriter._generate_json(
                    "character contract", "character bible", validate_character_bible
                )
        self.assertEqual(generated.call_count, 2)
        self.assertIn("repair failed after one bounded attempt", str(caught.exception))
        self.assertIn("character[0].behavioral_patterns", str(caught.exception))

    def test_cached_character_bible_array_is_normalized_without_gemini_call(self):
        brief = validate_story_brief(story_brief())
        source_hash = stable_hash(brief)
        cached = bible()
        cached["characters"][0]["behavioral_patterns"] = [
            "Avoids confrontation", "Counts exits"
        ]
        cached["source_hash"] = source_hash
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "character_bible.json")
            path.write_text(json.dumps(cached), encoding="utf-8")
            with patch.object(scriptwriter, "generate") as generated:
                result = scriptwriter._character_bible(brief, Path(temp), force=False)
            generated.assert_not_called()
            persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["characters"][0]["behavioral_patterns"],
                         "Avoids confrontation; Counts exits")
        self.assertIsInstance(persisted["characters"][0]["behavioral_patterns"], str)

    def test_bible_outline_and_script_contracts(self):
        b = validate_character_bible(bible())
        plan = validate_outline(outline(), b)
        sections = scene_batch()["scenes"]
        for index, scene in enumerate(sections, 1):
            scene["id"] = index
        script = validate_script({
            "title": "Title", "description": "Fiction", "video_type": "normal",
            "narrative_type": "realistic_psychological_fiction",
            "psychological_theme": "Regret", "sections": sections,
        }, b)
        self.assertEqual(plan["scenes"][-1]["order"], 3)
        self.assertTrue(all(not scene["bullet_points"] for scene in script["sections"]))
        self.assertEqual(deterministic_review(script, 12)["scene_count"], 3)

    def test_duplicate_character_ids_and_bad_chronology_fail(self):
        bad_bible = bible()
        bad_bible["characters"].append(dict(bad_bible["characters"][0]))
        with self.assertRaises(NarrativeValidationError):
            validate_character_bible(bad_bible)
        bad_outline = outline()
        bad_outline["scenes"][1]["order"] = 1
        with self.assertRaises(NarrativeValidationError):
            validate_outline(bad_outline, validate_character_bible(bible()))
        bad_rules = bible()
        bad_rules["speculative_rules"] = ["One rule", "one rule"]
        with self.assertRaises(NarrativeValidationError):
            validate_character_bible(bad_rules)

    def test_character_bible_diagnostics_distinguish_identity_role_and_rules(self):
        invalid_id = bible()
        invalid_id["characters"][0]["character_id"] = "Eli Smith"
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_character_bible(invalid_id)
        self.assertIn("invalid character ID", str(caught.exception))

        no_protagonist = bible()
        no_protagonist["characters"][0]["role"] = "supporting"
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_character_bible(no_protagonist)
        self.assertIn("missing or ambiguous protagonist", str(caught.exception))

        invalid_rules = bible()
        invalid_rules["speculative_rules"] = None
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_character_bible(invalid_rules)
        self.assertIn("speculative_rules", str(caught.exception))
        self.assertIn("Received: null", str(caught.exception))

    def test_character_bible_rejects_ambiguous_collection_types(self):
        invalid = bible()
        invalid["characters"][0]["relationships"] = {"eli": "self"}
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_character_bible(invalid)
        self.assertIn("character[0].relationships", str(caught.exception))
        self.assertIn("Expected: array[string]", str(caught.exception))

    def test_write_script_creates_intermediate_artifacts(self):
        responses = [json.dumps(bible()), json.dumps(outline()), json.dumps(scene_batch())]
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(scriptwriter, "generate", side_effect=responses), \
             patch.object(config, "NARRATIVE_AUTO_REVISION_ENABLED", False):
            script = scriptwriter.write_script(
                validate_story_brief(story_brief()), output_dir=temp,
                target_duration_minutes=12, narrative_review=False,
            )
            for name in ("character_bible.json", "story_outline.json", "script.json", "narrative_review.json"):
                self.assertTrue(Path(temp, name).exists(), name)
        self.assertEqual(script["content_mode"], "cinematic_fiction")

    def test_phase_21_benchmark_996_of_1500_is_blocking_with_scene_deficits(self):
        fixture = json.loads(Path(
            "tests/fixtures/architecture_of_envy_benchmark.json"
        ).read_text(encoding="utf-8"))
        counts = [scene["actual_words"] for scene in fixture["scenes"]]
        targets = [scene["target_words"] for scene in fixture["scenes"]]
        script = screenplay_with_counts(counts, targets)
        review = deterministic_review(script, 12)
        self.assertEqual(review["word_count"], 996)
        self.assertEqual(review["status"], "revision_required")
        self.assertTrue(any(issue["issue_type"] == "total_word_budget"
                            for issue in review["blocking_issues"]))
        self.assertEqual(len([item for item in review["scene_budgets"]
                              if item["status"] == "under_target"]), 8)

    def test_duration_acceptance_overlength_and_shorts_use_separate_contracts(self):
        accepted = deterministic_review(screenplay_with_counts([450, 600, 450]), 12)
        self.assertTrue(accepted["deterministic_checks"]["total_word_budget"])
        over = deterministic_review(screenplay_with_counts([650, 650, 650]), 12)
        self.assertTrue(any(issue["issue_type"] == "total_word_budget"
                            for issue in over["blocking_issues"]))
        short = deterministic_review(
            screenplay_with_counts([25, 35, 30], [30, 40, 40], "shorts"), .85
        )
        self.assertEqual(short["accepted_total_ratio"], {"minimum": .75, "maximum": 1.3})

    def test_rebalance_preserves_weights_and_hits_exact_total(self):
        plan = outline()
        plan["scenes"][0]["target_words"] = 50
        plan["scenes"][1]["target_words"] = 100
        plan["scenes"][2]["target_words"] = 150
        result = rebalance_scene_word_targets(plan, 1200)
        values = [scene["target_words"] for scene in result["scenes"]]
        self.assertEqual(sum(values), 1200)
        self.assertLess(values[0], values[1])
        self.assertLess(values[1], values[2])

    def test_schema_v2_migration_separates_type_from_free_text(self):
        legacy_outline = outline()
        for scene in legacy_outline["scenes"]:
            scene.pop("section_type")
        migrated = normalize_outline(legacy_outline)
        self.assertEqual(migrated["scenes"][0]["section_type"], "opening")
        self.assertEqual(migrated["scenes"][0]["narrative_function"], "opening setup")
        legacy_script = screenplay_with_counts([100, 100, 100])
        legacy_script.pop("narrative_contract_version", None)
        legacy_script["sections"][0].pop("narrative_function")
        legacy_script["sections"][0]["section_type"] = "Opening with a moral dilemma"
        migrated_script = normalize_script(legacy_script)
        self.assertEqual(migrated_script["sections"][0]["section_type"], "opening")
        self.assertEqual(migrated_script["sections"][0]["narrative_function"],
                         "Opening with a moral dilemma")

    def test_contract_v3_rejects_unknown_canonical_type(self):
        value = outline()
        value["narrative_contract_version"] = 3
        value["scenes"][1]["section_type"] = "montage"
        with self.assertRaises(NarrativeValidationError):
            validate_outline(value, validate_character_bible(bible()), True)

    def test_editorial_scores_cannot_override_blocker_and_evidence_is_required(self):
        deterministic = deterministic_review(screenplay_with_counts([100, 100, 100]), 12)
        merged = merge_editorial_review(deterministic, editorial_assessment(score=10))
        self.assertEqual(merged["status"], "revision_required")
        finding = {
            "severity": "blocking", "issue_type": "continuity_identity", "scene_ids": [],
            "claim": "Age changed", "evidence": [], "why_it_matters": "Breaks identity",
            "recommendation": "Correct the reference", "certainty": "confirmed",
        }
        with self.assertRaises(NarrativeValidationError):
            merge_editorial_review(deterministic, editorial_assessment([finding]))

    def test_targeted_expansion_changes_only_selected_scenes(self):
        script = screenplay_with_counts([50, 100, 100], [100, 100, 100])
        plan = outline()
        for scene, planned in zip(script["sections"], plan["scenes"]):
            scene["outline_scene_id"] = planned["scene_id"]
            scene["section_type"] = planned["section_type"]
            scene["narrative_function"] = planned["narrative_function"]
            scene["target_words"] = planned["target_words"]
        original_second = script["sections"][1]
        replacement = dict(script["sections"][0])
        replacement["narration"] = " ".join(f"expanded{n}" for n in range(90))
        with patch.object(scriptwriter, "generate", return_value=json.dumps({"scenes": [replacement]})), \
             patch.object(config, "NARRATIVE_EXPANSION_BATCH_SIZE", 1), \
             patch.object(config, "NARRATIVE_EXPANSION_MAX_CALLS", 1):
            revised, attempts = scriptwriter._expand_underlength_scenes(
                script, story_brief(), bible(), plan
            )
        self.assertEqual(revised["sections"][0]["narration"], replacement["narration"])
        self.assertEqual(revised["sections"][1], original_second)
        self.assertTrue(attempts[0]["success"])

    def test_failed_expansion_is_bounded_and_reported_honestly(self):
        script = screenplay_with_counts([50, 100, 100], [100, 100, 100])
        inadequate = dict(script["sections"][0])
        with patch.object(
            scriptwriter, "generate",
            side_effect=[json.dumps({"scenes": [inadequate]}),
                         json.dumps({"scenes": [inadequate]})],
        ) as generated, patch.object(config, "NARRATIVE_EXPANSION_BATCH_SIZE", 1), \
             patch.object(config, "NARRATIVE_EXPANSION_MAX_CALLS", 1):
            revised, attempts = scriptwriter._expand_underlength_scenes(
                script, story_brief(), bible(), outline()
            )
        self.assertEqual(generated.call_count, 2)
        self.assertFalse(attempts[0]["success"])
        self.assertEqual(revised["sections"], script["sections"])

    def test_confirmed_continuity_finding_is_evidence_backed_and_routed(self):
        finding = {
            "severity": "blocking", "issue_type": "continuity_age", "scene_ids": [3],
            "claim": "Julian is called younger although he is older than Arthur.",
            "evidence": ["Bible: Arthur 38, Julian 39", "Scene 3: younger man"],
            "why_it_matters": "It contradicts fixed character identity.",
            "recommendation": "Replace younger with a non-age descriptor.",
            "certainty": "confirmed",
        }
        result = merge_editorial_review(
            deterministic_review(screenplay_with_counts([500, 500, 500]), 12),
            editorial_assessment([finding]),
        )
        self.assertEqual(result["status"], "revision_required")
        self.assertEqual(result["continuity_findings"][0]["scene_ids"], [3])

    def test_scene_regeneration_preserves_other_scenes_and_marks_review_stale(self):
        plan = validate_outline(outline(), validate_character_bible(bible()), True)
        script = screenplay_with_counts([100, 100, 100], [80, 80, 80])
        script["target_duration_minutes"] = 12
        for scene, planned in zip(script["sections"], plan["scenes"]):
            scene.update({
                "outline_scene_id": planned["scene_id"],
                "section_type": planned["section_type"],
                "narrative_function": planned["narrative_function"],
                "target_words": planned["target_words"],
            })
        original_third = dict(script["sections"][2])
        replacement = dict(script["sections"][1])
        replacement["scene_id"] = plan["scenes"][1]["scene_id"]
        replacement["narration"] = " ".join(f"revised{n}" for n in range(90))
        with tempfile.TemporaryDirectory() as temp, patch.object(
            scriptwriter, "_write_scene_batch", return_value=[replacement]
        ):
            revised = scriptwriter.regenerate_scene(
                script, validate_story_brief(story_brief()), bible(), plan, 2, temp
            )
            review = json.loads(Path(temp, "narrative_review.json").read_text(encoding="utf-8"))
        self.assertEqual(revised["sections"][2], original_third)
        self.assertTrue(review["editorial_review_stale"])
        self.assertFalse(review["editorial_validation_complete"])
        self.assertEqual(review["repair_attempts"][0]["scene_ids"], [2])

    def test_malformed_generation_has_one_repair(self):
        with patch.object(scriptwriter, "generate", side_effect=["not json", '{"ok": true}']) as mocked:
            result = scriptwriter._generate_json("prompt", "test")
        self.assertTrue(result["ok"])
        self.assertEqual(mocked.call_count, 2)

    def test_incomplete_scene_batch_is_not_silently_accepted(self):
        with patch.object(scriptwriter, "generate", side_effect=['{"scenes": []}', '{"scenes": []}']) as mocked:
            with self.assertRaises(NarrativeValidationError):
                scriptwriter._write_scene_batch(story_brief(), bible(), outline(), outline()["scenes"][:1], "")
        self.assertEqual(mocked.call_count, 2)

    def test_downstream_invalidation_is_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("story_brief.json", "script.json", "narration.mp3", "final_video.mp4"):
                (root / name).write_text("x", encoding="utf-8")
            invalidate_downstream("narration", temp)
            self.assertTrue((root / "script.json").exists())
            self.assertFalse((root / "narration.mp3").exists())

    def test_single_scene_invalidation_preserves_other_scene_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            images = Path(temp, "images")
            images.mkdir()
            target = images / "section_02_img1.jpg"
            other = images / "section_03_img1.jpg"
            target.write_text("x", encoding="utf-8")
            other.write_text("x", encoding="utf-8")
            Path(temp, "narration.mp3").write_text("x", encoding="utf-8")
            invalidate_scene_outputs(2, temp)
            self.assertFalse(target.exists())
            self.assertTrue(other.exists())
            self.assertFalse(Path(temp, "narration.mp3").exists())


class TimelineTests(unittest.TestCase):
    def test_exact_cumulative_timeline_has_no_transition_drift(self):
        sections = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
        timeline = build_scene_timeline(sections, [3.25, 4.75])
        validate_timeline(timeline, audio_duration=8.0)
        self.assertEqual(timeline["scenes"][1]["start"], 3.25)
        self.assertEqual(timeline["total_duration"], 8.0)

    def test_proportional_fallback_sums_to_audio(self):
        durations = proportional_scene_durations(
            [{"narration": "one"}, {"narration": "one two three"}], 10.0
        )
        self.assertAlmostEqual(sum(durations), 10.0)

    def test_scene_srts_are_shifted_globally(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second, output = (Path(temp, name) for name in ("a.srt", "b.srt", "out.srt"))
            cue = "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
            first.write_text(cue, encoding="utf-8")
            second.write_text(cue.replace("Hello", "Again"), encoding="utf-8")
            self.assertEqual(write_shifted_srt(output, [(str(first), 0), (str(second), 2.5)]), 2)
            self.assertIn("00:00:02,500 --> 00:00:03,500", output.read_text(encoding="utf-8"))

    def test_cinematic_clip_skips_educational_overlay_renderers(self):
        from video import creator
        section = {"section_type": "content", "title": "Hidden title", "narration": "Narration"}
        with patch.object(creator, "_render_content") as content, \
             patch.object(creator, "_draw_progress_bar") as progress, \
             patch.object(creator, "_draw_lower_third") as lower:
            clip = creator._make_clip(
                section, .5, [], 1, 1, captions=[], section_start_t=0, cinematic=True
            )
            frame = clip.get_frame(.2)
            clip.close()
        self.assertEqual(frame.shape[2], 3)
        content.assert_not_called()
        progress.assert_not_called()
        lower.assert_not_called()


class NarrativeGuiTests(unittest.TestCase):
    def test_run_route_rejects_out_of_range_duration_and_bad_type(self):
        import gui
        client = gui.app.test_client()
        response = client.post("/api/run", json={"video_type": "normal", "target_duration": 8})
        self.assertEqual(response.status_code, 400)
        response = client.post("/api/run", json={
            "video_type": "normal", "target_duration": 15,
            "narrative_type": "science_explainer",
        })
        self.assertEqual(response.status_code, 400)

    def test_description_is_fiction_packaging_not_learning_list(self):
        import gui
        brief = validate_story_brief(story_brief())
        sections = [{"title": "Opening", "narration": "story"}] * 3
        description = gui._build_description(
            {"description": brief["description"], "sections": sections, "tags": brief["tags"]},
            brief,
        )
        self.assertIn("original work of fiction", description)
        self.assertNotIn("WHAT YOU'LL LEARN", description)


@unittest.skipUnless(
    os.getenv("RUN_PRIVATE_GRAVITY_SMOKE_TEST") == "1",
    "Set RUN_PRIVATE_GRAVITY_SMOKE_TEST=1 for the opt-in live story-to-Short screenplay smoke test",
)
class PrivateGravityIntegrationSmokeTest(unittest.TestCase):
    def test_live_story_to_short_screenplay(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            brief = researcher.research_topic(
                config.CHANNEL_DESCRIPTION,
                topic_override="A lonely locksmith discovers one key opens a memory he deliberately erased.",
                narrative_type="philosophical_thought_experiment",
                target_duration_minutes=1,
            )
            script = scriptwriter.write_script(
                brief, video_type="shorts", output_dir=temp, narrative_review=False,
            )
        self.assertEqual(script["schema_version"], 2)
        self.assertEqual(script["content_mode"], "cinematic_fiction")
        self.assertGreaterEqual(len(script["sections"]), 3)


@unittest.skipUnless(
    os.getenv("RUN_PRIVATE_GRAVITY_LONGFORM_SMOKE_TEST") == "1",
    "Opt-in live long-form smoke: typically 7-10 Gemini calls; narrative artifacts only",
)
class PrivateGravityLongFormSmokeTest(unittest.TestCase):
    def test_live_12_minute_narrative_only_pipeline(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            brief = researcher.research_topic(
                config.CHANNEL_DESCRIPTION,
                topic_override="A respected architect discovers his rival has been protecting him.",
                narrative_type="realistic_psychological_fiction",
                target_duration_minutes=12,
            )
            script = scriptwriter.write_script(
                brief, video_type="normal", output_dir=temp,
                target_duration_minutes=12, narrative_review=True, force=True,
            )
            review = json.loads(Path(temp, "narrative_review.json").read_text(encoding="utf-8"))
        self.assertEqual(script["narrative_contract_version"], 3)
        self.assertIn(review["status"], {
            "revision_required", "human_review_required", "ready_for_human_review",
        })
        self.assertTrue(review["scene_budgets"])


if __name__ == "__main__":
    unittest.main()
