"""Phase 2.1.1 generation reliability: deadlines, repairs, diagnostics, resume."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google.genai import errors

import config
from agents import gemini_client as gc
from agents import researcher, scriptwriter, structured
from agents.narrative import (
    NarrativeValidationError,
    atomic_write_json,
    stable_hash,
    validate_outline,
    validate_story_brief,
)
from scripts import regenerate_narrative_benchmark as benchmark
from tests.test_gemini_client import FakeClient, FakeModels, model, response
from tests.test_narrative import bible, outline, story_brief

A, B = "gemini-3.6-flash", "gemini-3.8-flash"


def deadline_error():
    return errors.APIError(504, {"error": {
        "message": "Deadline expired before operation could complete.",
        "status": "DEADLINE_EXCEEDED",
    }})


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def big_outline(scene_count=10, filler_words=400):
    """A long, fully valid outline: each scene carries a long unique summary."""
    template = outline()["scenes"][0]
    types = ["opening"] + ["development"] * (scene_count - 3) + ["climax", "resolution"]
    scenes = []
    for index in range(1, scene_count + 1):
        scene = deepcopy(template)
        scene.update({
            "scene_id": f"s{index:02}", "order": index, "title": f"Scene {index}",
            "section_type": types[index - 1],
            "narrative_function": f"Function {index}",
            "summary": f"UNIQUE-SUMMARY-{index:02} " + " ".join(["detail"] * filler_words),
            "decision": f"Decision {index}", "consequence": f"Consequence {index}",
        })
        scenes.append(scene)
    return {"scenes": scenes}


class DeadlineRetryTests(unittest.TestCase):
    def setUp(self):
        gc.reset_client()
        self.patches = [
            patch.object(config, "GEMINI_API_KEY", "unit-test-key"),
            patch.object(config, "GEMINI_TIMEOUT_SECONDS", 300),
            patch.object(config, "GEMINI_THINKING_LEVEL", ""),
            patch.object(gc, "_sleep", lambda seconds: None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        gc.reset_client()

    def run_generate(self, models, **kwargs):
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            return quiet(gc.generate, "prompt", starting_model=A, **kwargs)

    def test_deadline_retries_same_model_with_larger_deadline(self):
        models = FakeModels(available=[model(A), model(B)],
                            generation=[deadline_error(), response("done")])
        self.assertEqual(self.run_generate(models, stage="outline"), "done")
        calls = models.generate_calls
        self.assertEqual([call["model"] for call in calls], [A, A])
        options = [call["config"].http_options for call in calls]
        self.assertEqual([item.timeout for item in options], [300_000, 600_000])
        self.assertEqual([item.headers["X-Server-Timeout"] for item in options], ["300", "600"])
        records = gc.call_records()[-2:]
        self.assertEqual([record["outcome"] for record in records], ["deadline_exceeded", "success"])
        self.assertTrue(all(record["stage"] == "outline" for record in records))
        self.assertNotIn("prompt", {key for record in records for key in record} - {"prompt_chars"})

    def test_retry_deadline_is_capped(self):
        models = FakeModels(available=[model(A)], generation=[deadline_error(), response("ok")])
        self.run_generate(models, timeout_seconds=500)
        self.assertEqual(models.generate_calls[1]["config"].http_options.timeout, 600_000)

    def test_repeated_deadline_falls_back_and_lists_both_models(self):
        models = FakeModels(available=[model(A), model(B)],
                            generation=[deadline_error() for _ in range(4)])
        with self.assertRaises(gc.GeminiGenerationError) as caught:
            self.run_generate(models)
        self.assertEqual([call["model"] for call in models.generate_calls], [A, A, B, B])
        error = caught.exception
        self.assertEqual(error.info.category, "deadline_exceeded")
        self.assertEqual(error.attempted_models, (A, B))
        self.assertTrue(error.fallback_attempted)
        self.assertIn(f"attempted_models={A}, {B}", str(error))
        self.assertIn("Next action:", str(error))

    def test_client_timeout_is_short_and_sdk_does_not_retry_504(self):
        created = []
        with patch.object(gc.genai, "Client", side_effect=lambda **kwargs: created.append(kwargs)
                          or FakeClient(FakeModels())):
            gc._get_client()
        options = created[0]["http_options"]
        self.assertEqual(options.timeout, 30_000)
        self.assertNotIn(504, options.retry_options.http_status_codes)

    def test_stage_settings_reach_the_request(self):
        models = FakeModels(available=[model(A)], generation=[response("{}")])
        with patch.object(config, "GEMINI_THINKING_LEVEL", "low"):
            self.run_generate(models, temperature=0.2, response_mime_type="application/json",
                              max_output_tokens=4096)
        request = models.generate_calls[0]["config"]
        self.assertEqual(request.temperature, 0.2)
        self.assertEqual(request.response_mime_type, "application/json")
        self.assertEqual(request.max_output_tokens, 4096)
        self.assertEqual(request.thinking_config.thinking_level.name, "LOW")

    def test_thinking_budget_and_invalid_level(self):
        self.assertEqual(gc._thinking_config("2048").thinking_budget, 2048)
        self.assertIsNone(gc._thinking_config(""))
        with self.assertRaises(ValueError):
            gc._thinking_config("extreme")

    def test_deadline_classification(self):
        self.assertEqual(gc.classify_error(deadline_error()).category, "deadline_exceeded")
        import httpx
        info = gc.classify_error(httpx.ReadTimeout("slow"))
        self.assertEqual(info.category, "deadline_exceeded")
        self.assertTrue(info.fallback_allowed)


class RepairPromptTests(unittest.TestCase):
    CONTRACT = ("CONTRACT-START " + "rule " * 4000 + "CONTRACT-END")

    def test_parse_repair_sends_complete_contract_and_response(self):
        raw = "not json " * 3000
        with patch.object(scriptwriter, "generate", side_effect=[raw, '{"ok": true}']) as mocked:
            quiet(scriptwriter._generate_json, self.CONTRACT, "test",
                  context="CONTEXT-BLOCK " * 2000)
        repair = mocked.call_args_list[1].args[0]
        self.assertIn(self.CONTRACT, repair)
        self.assertIn(raw, repair)
        self.assertLess(repair.index("CONTRACT-START"), repair.index("CONTEXT-BLOCK"))
        self.assertEqual(mocked.call_args_list[1].kwargs["response_mime_type"], "application/json")

    def test_semantic_repair_sends_complete_contract_and_parseable_compact_json(self):
        invalid = big_outline(10, 300)
        del invalid["scenes"][4]["obstacle"]
        repaired = big_outline(10, 300)
        with patch.object(scriptwriter, "generate",
                          side_effect=[json.dumps(invalid), json.dumps(repaired)]) as mocked:
            quiet(scriptwriter._generate_json, self.CONTRACT, "story outline",
                  lambda value: validate_outline(value, bible(), True))
        repair = mocked.call_args_list[1].args[0]
        self.assertIn(self.CONTRACT, repair)
        self.assertIn("scene s05", repair)
        self.assertIn("obstacle", repair)
        embedded = repair.split("CURRENT VALID JSON:\n", 1)[1]
        self.assertEqual(json.loads(embedded), invalid)
        self.assertNotIn("\n  ", embedded)

    def test_oversized_repair_is_scene_scoped_and_preserves_valid_scenes(self):
        original = big_outline(12, 600)
        invalid = deepcopy(original)
        del invalid["scenes"][5]["obstacle"]
        fixed = deepcopy(original["scenes"][5])
        fixed["obstacle"] = "A newly specific obstacle."
        document_size = len(json.dumps(invalid, separators=(",", ":")))
        with patch.object(config, "GEMINI_REPAIR_MAX_CHARS", document_size // 2), \
             patch.object(scriptwriter, "generate",
                          side_effect=[json.dumps(invalid), json.dumps({"scenes": [fixed]})]) as mocked:
            result = quiet(scriptwriter._generate_json, "OUTLINE CONTRACT", "story outline",
                           lambda value: validate_outline(value, bible(), True),
                           scene_scope=structured.SceneScope(
                               issues=lambda value: scriptwriter.outline_scene_issues(
                                   value, bible(), True)))
        repair = mocked.call_args_list[1].args[0]
        self.assertIn("OUTLINE CONTRACT", repair)
        self.assertIn("UNIQUE-SUMMARY-06", repair)
        self.assertIn("UNIQUE-SUMMARY-05", repair)  # read-only neighbour
        self.assertIn("UNIQUE-SUMMARY-07", repair)  # read-only neighbour
        self.assertNotIn("UNIQUE-SUMMARY-01", repair)
        self.assertNotIn("UNIQUE-SUMMARY-12", repair)
        self.assertLessEqual(len(repair), document_size // 2)
        self.assertEqual(result["scenes"][5]["obstacle"], "A newly specific obstacle.")
        for index, scene in enumerate(result["scenes"]):
            if index != 5:
                expected = validate_outline(original, bible(), True)["scenes"][index]
                self.assertEqual(json.dumps(scene, sort_keys=True),
                                 json.dumps(expected, sort_keys=True))

    def test_scene_scoped_repair_ignores_changes_to_neighbours(self):
        original = big_outline(12, 600)
        invalid = deepcopy(original)
        del invalid["scenes"][5]["obstacle"]
        fixed = deepcopy(original["scenes"][5])
        tampered = deepcopy(original["scenes"][4])
        tampered["summary"] = "REWRITTEN"
        size = len(json.dumps(invalid, separators=(",", ":")))
        with patch.object(config, "GEMINI_REPAIR_MAX_CHARS", size // 2), \
             patch.object(scriptwriter, "generate",
                          side_effect=[json.dumps(invalid), json.dumps({"scenes": [tampered, fixed]})]):
            with self.assertRaises(NarrativeValidationError) as caught:
                quiet(scriptwriter._generate_json, "C", "story outline",
                      lambda value: validate_outline(value, bible(), True),
                      scene_scope=structured.SceneScope(
                          issues=lambda value: scriptwriter.outline_scene_issues(
                              value, bible(), True)),
                      run_dir=None)
        self.assertIn("exactly 1 scenes", str(caught.exception))

    def test_outline_validation_names_scene_ids_and_fields(self):
        invalid = outline()
        del invalid["scenes"][1]["decision"]
        invalid["scenes"][2]["characters_present"] = ["ghost"]
        with self.assertRaises(NarrativeValidationError) as caught:
            validate_outline(invalid, bible(), True)
        message = str(caught.exception)
        self.assertIn("scene s2: missing nonempty fields: decision", message)
        self.assertIn("scene s3: characters_present has unknown character IDs: ghost", message)


class DebugArtifactTests(unittest.TestCase):
    def test_failed_repair_writes_debug_files_without_keys(self):
        leaked = "AIzaSyD" + "x" * 32
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(config, "GEMINI_API_KEY", "configured-secret-value"), \
             patch.object(scriptwriter, "generate", side_effect=[
                 f"not json {leaked} configured-secret-value",
                 "still not json api_key=abc123 token: zzz",
             ]):
            with self.assertRaises(NarrativeValidationError):
                quiet(scriptwriter._generate_json, "C", "story outline", run_dir=temp)
            files = sorted(Path(temp, "debug").glob("*.txt"))
            contents = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertEqual([path.name for path in files], ["story-outline-1.txt",
                                                         "story-outline-repair-1.txt"])
        self.assertIn("not json", contents)
        self.assertIn("contained no complete JSON object", contents)
        for secret in (leaked, "configured-secret-value", "abc123", "zzz"):
            self.assertNotIn(secret, contents)

    def test_generation_error_is_recorded_for_the_stage(self):
        info = gc.GeminiErrorInfo("deadline_exceeded", 504, True, True, "Retry.", "Slow.")
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(scriptwriter, "generate",
                          side_effect=gc.GeminiGenerationError(info, model=A)):
            with self.assertRaises(gc.GeminiGenerationError):
                scriptwriter._generate_json("C", "story outline", stage="outline", run_dir=temp)
            text = Path(temp, "debug", "outline-1.txt").read_text(encoding="utf-8")
        self.assertIn("generation-error", text)
        self.assertIn("deadline_exceeded", text)

    def test_write_script_uses_run_directory_for_debug_output(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(scriptwriter, "generate", side_effect=["no", "no"]):
            with self.assertRaises(NarrativeValidationError):
                quiet(scriptwriter.write_script, validate_story_brief(story_brief()),
                      output_dir=temp, target_duration_minutes=12, narrative_review=False)
            self.assertTrue(Path(temp, "debug", "character-bible-1.txt").exists())


class ResumeTests(unittest.TestCase):
    def test_resume_reuses_cached_brief_and_bible(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            payload = json.dumps(story_brief())
            with patch.object(researcher, "generate", return_value=payload):
                first = quiet(researcher.research_topic, "channel",
                              topic_override="The Man Who Needed Everyone to Envy Him",
                              narrative_type="realistic_psychological_fiction",
                              target_duration_minutes=12)
            cached_bible = bible()
            cached_bible["source_hash"] = stable_hash(validate_story_brief(dict(first)))
            atomic_write_json(Path(temp, "character_bible.json"), cached_bible)

            with patch.object(researcher, "generate") as research_call, \
                 patch.object(scriptwriter, "generate") as script_call, \
                 patch.object(benchmark, "write_script") as write:
                quiet(benchmark.main, ["--confirm-live", "--minutes", "12", "--resume", temp])
                research_call.assert_not_called()
                brief = write.call_args.args[0]
                self.assertFalse(write.call_args.kwargs["force"])
                bible_value = scriptwriter._character_bible(
                    validate_story_brief(dict(brief)), Path(temp), force=False)
                script_call.assert_not_called()
        self.assertEqual(brief, first)
        self.assertEqual(bible_value["characters"][0]["character_id"], "eli")

    def test_resume_ignores_brief_from_a_different_request(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            with patch.object(researcher, "generate", return_value=json.dumps(story_brief())):
                quiet(researcher.research_topic, "channel", topic_override="Premise one",
                      target_duration_minutes=12)
            with patch.object(researcher, "generate",
                              return_value=json.dumps(story_brief())) as again:
                quiet(researcher.research_topic, "channel", topic_override="Premise two",
                      target_duration_minutes=12, reuse_cached=True)
            again.assert_called_once()

    def test_resume_requires_existing_directory(self):
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                benchmark.main(["--confirm-live", "--resume", "does/not/exist"])

    def test_timing_table_aggregates_by_stage(self):
        rows = benchmark.timing_rows([
            {"stage": "outline", "model": A, "prompt_chars": 100, "elapsed_seconds": 30,
             "outcome": "deadline_exceeded"},
            {"stage": "outline", "model": A, "prompt_chars": 100, "elapsed_seconds": 50,
             "outcome": "success"},
            {"stage": "scenes s01-s03", "model": B, "prompt_chars": 90, "elapsed_seconds": 20,
             "outcome": "success"},
        ])
        self.assertEqual([(row["stage"], row["calls"], row["seconds"]) for row in rows],
                         [("outline", 2, 80.0), ("scenes s01-s03", 1, 20.0)])
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark.print_timing_table([], 1.0)
        self.assertIn("no Gemini calls", output.getvalue())


if __name__ == "__main__":
    unittest.main()
