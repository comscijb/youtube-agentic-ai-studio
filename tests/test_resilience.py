"""Phase 3.2-A (2.2.2): overload backoff, proportionate budgets, re-review, checkpoints."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from google.genai import errors

import config
from agents import gemini_client as gc
from agents import scriptwriter
from agents.narrative import atomic_write_json, deterministic_review, validate_story_brief
from tests.test_gemini_client import FakeClient, FakeModels, model, response
from tests.test_narrative import bible, outline, scene_batch, screenplay_with_counts, story_brief
from tests.test_review_reliability import assessment

A, B = "gemini-3.6-flash", "gemini-3.8-flash"


def overloaded(retry_delay=None):
    error = {"code": 503, "message": "This model is currently experiencing high demand.",
             "status": "UNAVAILABLE"}
    if retry_delay:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                             "retryDelay": retry_delay}]
    return errors.APIError(503, {"error": error})


def rate_limited(retry_delay="32s"):
    return errors.APIError(429, {"error": {
        "code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]}})


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class OverloadTests(unittest.TestCase):
    def setUp(self):
        gc.reset_client()
        gc.clear_call_records()
        self.clock = FakeClock()
        self.waits = []

        def sleep(seconds):
            self.waits.append(seconds)
            self.clock.sleep(seconds)

        self.patches = [patch.object(config, "GEMINI_API_KEY", "unit-test-key"),
                        patch.object(gc, "_sleep", sleep), patch.object(gc, "_clock", self.clock),
                        patch.object(gc.random, "uniform", lambda a, b: 0.0)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        gc.reset_client()

    def run_generate(self, generation, available=(A, B)):
        models = FakeModels(available=[model(name) for name in available], generation=generation)
        output = io.StringIO()
        with patch.object(gc, "_get_client", return_value=FakeClient(models)), redirect_stdout(output):
            try:
                result = gc.generate("prompt", starting_model=A, stage="outline")
            except gc.GeminiGenerationError as exc:
                result = exc
        return result, models, output.getvalue()

    def test_overload_twice_then_success_on_the_same_model(self):
        result, models, output = self.run_generate([overloaded(), overloaded(), response("ok")])
        self.assertEqual(result, "ok")
        self.assertEqual([call["model"] for call in models.generate_calls], [A, A, A])
        self.assertEqual(self.waits, [10.0, 20.0])
        self.assertIn("waiting 10s before retry 1/4 (model overloaded)", output)
        self.assertIn("wait=20s reason=overloaded", output)
        summary = gc.usage_summary()
        self.assertEqual((summary["calls"], summary["retries"], summary["fallbacks"]), (3, 2, 0))
        self.assertEqual(summary["wait_seconds"], 30.0)

    def test_budget_exhausted_on_first_model_then_fallback_succeeds(self):
        result, models, _ = self.run_generate([overloaded()] * 5 + [response("fallback")])
        self.assertEqual(result, "fallback")
        self.assertEqual([call["model"] for call in models.generate_calls], [A] * 5 + [B])
        self.assertEqual(self.waits, [10.0, 20.0, 40.0, 80.0])
        self.assertEqual(gc.usage_summary()["fallbacks"], 1)

    def test_rate_limit_honours_retry_info(self):
        result, _, output = self.run_generate([rate_limited("32s"), response("ok")])
        self.assertEqual(result, "ok")
        self.assertEqual(self.waits, [32.0])
        self.assertIn("rate limited", output)
        self.assertEqual(gc.classify_error(overloaded("7s")).retry_after, 7.0)

    def test_wall_clock_cap_stops_waiting(self):
        with patch.object(config, "GEMINI_MAX_CALL_WALLCLOCK", 25):
            result, models, output = self.run_generate([overloaded()] * 10)
        self.assertIsInstance(result, gc.GeminiGenerationError)
        self.assertEqual(result.info.category, "overloaded")
        # A waits 10 s; its next 20 s wait would pass the 25 s cap, so the fallback B
        # gets the remaining time (10 s) and then the call gives up.
        self.assertEqual(self.waits, [10.0, 10.0])
        self.assertEqual([call["model"] for call in models.generate_calls], [A, A, B, B])
        self.assertIn("GEMINI_MAX_CALL_WALLCLOCK", output)
        self.assertEqual(gc.usage_summary()["failures"], 1)
        self.assertIn("1 failures", gc.usage_line())

    def test_sdk_no_longer_retries_429_or_503(self):
        created = []
        with patch.object(gc.genai, "Client", side_effect=lambda **kw: created.append(kw) or FakeClient(FakeModels())):
            gc._get_client()
        codes = created[0]["http_options"].retry_options.http_status_codes
        self.assertNotIn(503, codes)
        self.assertNotIn(429, codes)


class BudgetSeverityTests(unittest.TestCase):
    def test_slightly_long_scene_inside_good_total_is_minor(self):
        counts = [165, 173, 175, 192, 181, 196, 176, 158, 141]       # s01 = 117.9 %, total 104 %
        targets = [140, 175, 165, 180, 185, 195, 170, 155, 135]
        script = screenplay_with_counts(counts, targets)
        script["title"] = "He Waited Twenty Years"
        report = deterministic_review(script, 12, brief={"psychological_theme": "envy"})
        issue = next(item for item in report["minor_issues"] if item["issue_type"] == "scene_word_budget")
        self.assertEqual(issue["scene_ids"], [1])
        self.assertFalse(any(item["issue_type"] == "scene_word_budget" for item in report["major_issues"]))
        self.assertEqual(report["status"], "human_review_required")

    def test_far_over_or_under_target_stays_major(self):
        over = screenplay_with_counts([135, 100, 100], [100, 100, 100])     # total within, scene 135 %
        report = deterministic_review(over, 335 / 125)
        self.assertTrue(any(item["issue_type"] == "scene_word_budget" for item in report["major_issues"]))
        under = screenplay_with_counts([80, 110, 110], [100, 100, 100])
        report = deterministic_review(under, 300 / 125)
        self.assertEqual(next(item for item in report["major_issues"]
                              if item["issue_type"] == "scene_word_budget")["scene_ids"], [1])

    def test_long_scene_with_total_out_of_range_is_major(self):
        script = screenplay_with_counts([120, 120, 120], [100, 100, 100])   # total 120 %
        report = deterministic_review(script, 300 / 125)
        self.assertTrue(all(item["severity"] == "major" for item in report["major_issues"]
                            if item["issue_type"] == "scene_word_budget"))


class RereviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        script = screenplay_with_counts([80, 80, 80], [80, 80, 80])
        script.update(title="He Waited Twenty Years", target_duration_minutes=240 / 125)
        for name, value in (("script.json", script), ("story_brief.json", story_brief()),
                            ("character_bible.json", bible()), ("story_outline.json", {"scenes": []}),
                            ("narrative_review.json", {"status": "revision_required",
                                                       "editorial_review_stale": True,
                                                       "editorial_edits": [{"scene_id": "s01"}]})):
            atomic_write_json(self.output / name, value)
        atomic_write_json(self.output / "review_approval.json", {"approved_at": "x"})

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, *args):
        from scripts import rereview_output
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = rereview_output.main(["--output-dir", str(self.output), *args])
        return code, out.getvalue() + err.getvalue()

    def test_requires_confirm_live(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            from scripts import rereview_output
            rereview_output.main(["--output-dir", str(self.output)])

    def test_updates_review_clears_stale_and_invalidates_approval(self):
        with patch.object(scriptwriter, "_editorial_review", return_value=assessment([])):
            code, text = self.run_main("--confirm-live")
        self.assertEqual(code, 0, text)
        review = json.loads((self.output / "narrative_review.json").read_text(encoding="utf-8"))
        self.assertFalse(review["editorial_review_stale"])
        self.assertEqual(review["status"], "ready_for_human_review")
        self.assertEqual(review["editorial_edits"], [{"scene_id": "s01"}])
        self.assertFalse((self.output / "review_approval.json").exists())
        self.assertIn("Gemini usage:", text)

    def test_failure_leaves_files_untouched(self):
        before = (self.output / "narrative_review.json").read_text(encoding="utf-8")
        with patch.object(scriptwriter, "_editorial_review", side_effect=RuntimeError("503")):
            code, text = self.run_main("--confirm-live")
        self.assertEqual(code, 1)
        self.assertIn("Retry with: python -m scripts.rereview_output --confirm-live", text)
        self.assertEqual((self.output / "narrative_review.json").read_text(encoding="utf-8"), before)
        self.assertTrue((self.output / "review_approval.json").exists())


class CheckpointTests(unittest.TestCase):
    def run_script(self, temp, responses, force=False):
        with patch.object(scriptwriter, "generate", side_effect=responses) as call, \
                patch.object(config, "NARRATIVE_AUTO_REVISION_ENABLED", False), \
                redirect_stdout(io.StringIO()):
            try:
                result = scriptwriter.write_script(validate_story_brief(story_brief()), output_dir=temp,
                                                   target_duration_minutes=12, narrative_review=True,
                                                   force=force)
            except Exception as exc:     # noqa: BLE001 - the test inspects the failure
                result = exc
        return result, call

    def test_failed_run_resumes_from_checkpoints_then_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp:
            # Bible, outline and the scene batch succeed; the editorial review fails twice.
            first, call = self.run_script(temp, [json.dumps(bible()), json.dumps(outline()),
                                                 json.dumps(scene_batch()), "no", "no"])
            self.assertIsInstance(first, Exception)
            self.assertTrue(any(Path(temp, ".checkpoints").glob("scenes*.json")))
            # The resumed run only needs the editorial review.
            second, call = self.run_script(temp, [json.dumps(assessment([]))])
            self.assertEqual(call.call_count, 1)
            self.assertIn("skeptical senior fiction editor", call.call_args.args[0])
            self.assertIsInstance(second, dict)
            self.assertFalse(Path(temp, ".checkpoints").exists())

    def test_force_discards_checkpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            self.run_script(temp, [json.dumps(bible()), json.dumps(outline()),
                                   json.dumps(scene_batch()), "no", "no"])
            _, call = self.run_script(temp, [json.dumps(bible()), json.dumps(outline()),
                                             json.dumps(scene_batch()), json.dumps(assessment([]))],
                                      force=True)
            self.assertEqual(call.call_count, 4)


class PipelineResumeTests(unittest.TestCase):
    def test_resume_reuses_the_brief_and_prints_usage(self):
        import pipeline
        with patch.object(pipeline, "research_topic", side_effect=RuntimeError("stop")) as research, \
                patch.object(pipeline, "sync_publications_safely"), \
                tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(config, "IMAGES_DIR", str(Path(temp, "images"))), \
                patch.object(pipeline.projects, "resolve", return_value=Path(temp)):
            # --resume activates the active project: resolve it to the temp folder, never the real one
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(RuntimeError):
                pipeline.run(resume=True)
        self.assertTrue(research.call_args.kwargs["reuse_cached"])
        self.assertIn("Gemini usage:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
