import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from google.genai import errors

import config
from agents import gemini_client as gc


class FakeModels:
    def __init__(self, available=None, generation=None, list_error=None):
        self.available = available or []
        self.generation = list(generation or [])
        self.list_error = list_error
        self.generate_calls = []

    def list(self):
        if self.list_error:
            raise self.list_error
        return self.available

    def generate_content(self, **kwargs):
        self.generate_calls.append(kwargs)
        if not self.generation:
            return SimpleNamespace(text="OK", candidates=[], prompt_feedback=None)
        result = self.generation.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    def __init__(self, models):
        self.models = models
        self.closed = False

    def close(self):
        self.closed = True


def model(name, actions=("generateContent",)):
    return SimpleNamespace(name=f"models/{name}", supported_actions=list(actions))


def response(text):
    return SimpleNamespace(text=text, candidates=[], prompt_feedback=None)


def api_error(code, message="provider message"):
    statuses = {
        400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED",
        404: "NOT_FOUND", 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 503: "UNAVAILABLE",
    }
    return errors.APIError(code, {"error": {"message": message, "status": statuses[code]}})


class GeminiClientTests(unittest.TestCase):
    def setUp(self):
        gc.reset_client()
        self.key_patch = patch.object(config, "GEMINI_API_KEY", "unit-test-key")
        self.key_patch.start()

    def tearDown(self):
        self.key_patch.stop()
        gc.reset_client()

    def test_valid_model_selection(self):
        chain = gc.build_chain(
            "gemini-3.6-flash",
            ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"],
        )
        self.assertEqual(chain[0], "gemini-3.6-flash")
        self.assertNotIn("gemini-2.0-flash", chain)

    def test_discovery_keeps_only_accessible_text_generation_models(self):
        fake = FakeClient(FakeModels(available=[
            model("gemini-3.8-flash"),
            model("gemini-3.8-flash-tts"),
            model("gemini-3.6-flash", actions=("countTokens",)),
            model("gemini-2.0-flash"),
            model("gemini-3.5-flash-lite"),
        ]))
        self.assertEqual(
            gc.discover_models(fake, force=True),
            ["gemini-3.8-flash", "gemini-3.5-flash-lite"],
        )

    def test_unavailable_model_falls_back(self):
        models = FakeModels(
            available=[model("gemini-3.8-flash"), model("gemini-3.6-flash")],
            generation=[api_error(404), response("fallback text")],
        )
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            self.assertEqual(gc.generate("prompt"), "fallback text")
        self.assertEqual([call["model"] for call in models.generate_calls],
                         ["gemini-3.8-flash", "gemini-3.6-flash"])

    def test_invalid_api_key_fails_without_fallback(self):
        models = FakeModels(
            available=[model("gemini-3.8-flash"), model("gemini-3.6-flash")],
            generation=[api_error(401)],
        )
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            with self.assertRaises(gc.GeminiGenerationError) as caught:
                gc.generate("prompt")
        self.assertEqual(caught.exception.info.category, "invalid_credentials")
        self.assertEqual(len(models.generate_calls), 1)
        self.assertFalse(caught.exception.fallback_attempted)

    def test_missing_credentials(self):
        with patch.object(config, "GEMINI_API_KEY", ""):
            with self.assertRaises(gc.GeminiGenerationError) as caught:
                gc._get_client()
        self.assertEqual(caught.exception.info.category, "missing_credentials")

    def test_api_key_change_recreates_client(self):
        created = []

        def factory(**kwargs):
            client = FakeClient(FakeModels())
            created.append((kwargs, client))
            return client

        with patch.object(gc.genai, "Client", side_effect=factory):
            first = gc._get_client()
            config.GEMINI_API_KEY = "unit-test-key-changed"
            second = gc._get_client()
        self.assertIsNot(first, second)
        self.assertTrue(first.closed)
        self.assertEqual(len(created), 2)
        self.assertNotIn("unit-test-key", repr(created[0][0]["http_options"]))

    def test_structured_http_classification(self):
        expected = {
            400: ("invalid_request", False),
            401: ("invalid_credentials", False),
            403: ("permission_denied", False),
            404: ("model_unavailable", True),
            429: ("quota_exhausted", True),
            500: ("temporary_server", True),
            503: ("temporary_server", True),
        }
        for code, (category, fallback) in expected.items():
            with self.subTest(code=code):
                info = gc.classify_error(api_error(code))
                self.assertEqual(info.category, category)
                self.assertEqual(info.fallback_allowed, fallback)

    def test_transient_failures_have_bounded_fallback(self):
        models = FakeModels(
            available=[model(name) for name in gc.MODEL_CANDIDATES],
            generation=[api_error(503), api_error(503), response("must not be reached")],
        )
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            with self.assertRaises(gc.GeminiGenerationError):
                gc.generate("prompt")
        self.assertEqual(len(models.generate_calls), 2)

    def test_empty_response_uses_verified_fallback(self):
        models = FakeModels(
            available=[model("gemini-3.8-flash"), model("gemini-3.6-flash")],
            generation=[response(""), response("text")],
        )
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            self.assertEqual(gc.generate("prompt"), "text")

    def test_blocked_response_does_not_cycle_models(self):
        blocked = SimpleNamespace(
            text="", candidates=[],
            prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name="SAFETY")),
        )
        models = FakeModels(
            available=[model("gemini-3.8-flash"), model("gemini-3.6-flash")],
            generation=[blocked, response("must not be reached")],
        )
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            with self.assertRaises(gc.GeminiGenerationError) as caught:
                gc.generate("prompt")
        self.assertEqual(caught.exception.info.category, "blocked_response")
        self.assertEqual(len(models.generate_calls), 1)

    def test_model_discovery_failure_is_actionable(self):
        fake = FakeClient(FakeModels(list_error=api_error(503)))
        with self.assertRaises(gc.GeminiGenerationError) as caught:
            gc.discover_models(fake, force=True)
        self.assertEqual(caught.exception.info.category, "temporary_server")

    def test_standalone_diagnostic_contract(self):
        fake = FakeClient(FakeModels(
            available=[model("gemini-3.8-flash")],
            generation=[response("OK")],
        ))
        with patch.object(gc, "_get_client", return_value=fake):
            result = gc.diagnostic_snapshot(run_generation=True)
        self.assertTrue(result["connectivity"])
        self.assertEqual(result["selected_model"], "gemini-3.8-flash")
        self.assertEqual(result["generation"], "success")

    def test_total_provider_unavailability(self):
        models = FakeModels(
            available=[model(name) for name in gc.MODEL_CANDIDATES],
            generation=[api_error(404), api_error(404), api_error(404)],
        )
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            with self.assertRaises(gc.GeminiGenerationError) as caught:
                gc.generate("prompt")
        self.assertEqual(len(caught.exception.attempted_models), 3)
        self.assertTrue(caught.exception.fallback_attempted)

    def test_credential_redaction(self):
        secret = config.GEMINI_API_KEY
        models = FakeModels(
            available=[model("gemini-3.8-flash")],
            generation=[api_error(401, f"bad key={secret} url=https://x.test?key={secret}")],
        )
        with patch.object(gc, "_get_client", return_value=FakeClient(models)):
            with self.assertRaises(gc.GeminiGenerationError) as caught:
                gc.generate("prompt")
        self.assertNotIn(secret, str(caught.exception))

    def test_researcher_and_scriptwriter_keep_text_contract(self):
        from agents import researcher, scriptwriter
        research_json = json.dumps({
            "topic": "T", "video_title": "Title", "key_points": ["A"], "tags": ["tag"]
        })
        with patch.object(researcher, "generate", return_value=research_json):
            with redirect_stdout(io.StringIO()):
                research = researcher.research_topic("channel", topic_override="T")
        script_json = json.dumps({
            "title": "Title", "sections": [
                {"id": i, "title": f"S{i}", "narration": "Text", "image_query": "image"}
                for i in range(1, 5)
            ]
        })
        with patch.object(scriptwriter, "generate", return_value=script_json):
            with redirect_stdout(io.StringIO()):
                script = scriptwriter.write_script(research)
        self.assertEqual(research["topic"], "T")
        self.assertEqual(len(script["sections"]), 4)


class GeminiGuiTests(unittest.TestCase):
    def test_model_discovery_route(self):
        import gui
        with (patch("agents.gemini_client.discover_models", return_value=["gemini-3.8-flash"]),
              patch("agents.gemini_client.build_chain", return_value=["gemini-3.8-flash"])):
            response_obj = gui.app.test_client().get("/api/gemini/models?refresh=1")
        self.assertEqual(response_obj.status_code, 200)
        self.assertEqual(response_obj.get_json()["selected_model"], "gemini-3.8-flash")

    def test_gui_key_change_resets_client_without_exposing_key(self):
        import gui
        secret = "new-gemini-secret"
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.py").write_text("GEMINI_MODEL = 'gemini-3.8-flash'\n", encoding="utf-8")
            with (patch.object(gui, "ROOT", tmp),
                  patch.object(config, "GEMINI_API_KEY", "old-key"),
                  patch("agents.gemini_client.reset_client") as reset):
                response_obj = gui.app.test_client().post(
                    "/api/save-settings", json={"gemini_key": secret}
                )
                self.assertEqual(response_obj.status_code, 200)
                reset.assert_called_once()
                settings_body = gui.app.test_client().get("/api/settings").get_data(as_text=True)
                self.assertNotIn(secret, settings_body)


@unittest.skipUnless(
    os.getenv("RUN_GEMINI_SMOKE_TEST") == "1" and os.getenv("GEMINI_API_KEY"),
    "Set RUN_GEMINI_SMOKE_TEST=1 and GEMINI_API_KEY for the opt-in live smoke test",
)
class GeminiIntegrationSmokeTest(unittest.TestCase):
    def test_live_discovery_and_minimal_generation(self):
        gc.reset_client()
        result = gc.diagnostic_snapshot(run_generation=True)
        self.assertTrue(result["connectivity"], result.get("error"))
        self.assertEqual(result["generation"], "success", result.get("error"))


if __name__ == "__main__":
    unittest.main()
