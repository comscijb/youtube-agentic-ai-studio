import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import config
from agents import researcher, scriptwriter
from agents.narrative import (
    NarrativeValidationError,
    deterministic_review,
    invalidate_downstream,
    invalidate_scene_outputs,
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
    functions = ["opening setup", "confrontation", "resolution aftermath"]
    return {"scenes": [{
        "scene_id": f"s{i}", "order": i, "title": f"Scene {i}",
        "narrative_function": functions[i - 1], "summary": "A consequence changes Eli's choice.",
        "setting": "Empty office at night", "characters_present": ["eli"],
        "dramatic_function": "Escalate the confrontation", "emotional_turn": "Certainty becomes doubt",
        "causal_link": "His action causes the next scene", "target_words": 80,
        "visual_direction": "Cold monitor light and negative space",
        "image_queries": ["empty office night", "worn wristwatch close up", "man silhouette monitor"],
    } for i in range(1, 4)]}


def scene_batch():
    narration = " ".join(["Eli watched the silent monitor as the old clock divided his life again."] * 5)
    return {"scenes": [{
        "scene_id": f"s{i}", "title": f"Scene {i}", "section_type": "scene",
        "narration": narration, "setting": "Empty office", "characters_present": ["eli"],
        "dramatic_function": "Escalation", "emotional_tone": "Unease",
        "visual_direction": "A slow push through shadow", "image_query": "empty office at night",
        "image_query_2": "worn watch close up", "image_query_3": "man silhouette monitor",
        "caption_text": "", "on_screen_text": "",
    } for i in range(1, 4)]}


class NarrativeContractTests(unittest.TestCase):
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

    def test_write_script_creates_intermediate_artifacts(self):
        responses = [json.dumps(bible()), json.dumps(outline()), json.dumps(scene_batch())]
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(scriptwriter, "generate", side_effect=responses):
            script = scriptwriter.write_script(
                validate_story_brief(story_brief()), output_dir=temp,
                target_duration_minutes=12, narrative_review=False,
            )
            for name in ("character_bible.json", "story_outline.json", "script.json", "narrative_review.json"):
                self.assertTrue(Path(temp, name).exists(), name)
        self.assertEqual(script["content_mode"], "cinematic_fiction")

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


if __name__ == "__main__":
    unittest.main()
