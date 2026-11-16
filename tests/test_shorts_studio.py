"""Phase 4.3-G: the Shorts studio in the GUI (format cards, edits, settings, the formats panel); no Gemini, no TTS."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_short_formats import BRIEF, OUTLINE, SCRIPT, WPM, script_for, verdicts_ok
from video import short_formats


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        atomic_write_json(self.folder / "script.json", SCRIPT)
        atomic_write_json(self.folder / "story_brief.json", BRIEF)
        atomic_write_json(self.folder / "story_outline.json", OUTLINE)
        self.patches = [patch.object(short_formats, "shorts_wpm", return_value=WPM), patch.object(config, "OUTPUT_DIR", self.temp.name)]
        for item in self.patches:
            item.start()

        def generator(prompt, *args, **kwargs):
            import json
            return json.dumps({"shorts": [script_for("hook"), script_for("dilemma")]})
        short_formats.build(self.folder, generator=generator, simulator=verdicts_ok, formats=["hook", "dilemma"])
        import gui
        self.gui = gui
        self.client = gui.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_cards_and_edits(self):
        with patch.dict(self.gui._state, {"script": SCRIPT, "research": BRIEF}):
            data = self.client.get("/api/shorts").get_json()
            self.assertEqual(data["mode"], "formats")
            hook, dilemma = data["plan"]["segments"]
            self.assertEqual((hook["label"], hook["status"], hook["range"]), ("Gancho", "ready", [20, 35]))
            self.assertEqual(dilemma["pinned_comment"], "What would you do?")
            self.assertEqual(hook["pinned_comment"], "The full story is linked above.")
            saved = self.client.post("/api/shorts/save", json={"segments": [{"id": "short_01", "script": "Once upon a time. " + hook["script"]}]}).get_json()
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["plan"]["segments"][0]["status"], "review")
        self.assertTrue(load_json(self.folder / "shorts" / "shorts_plan.json")["segments"][0]["swipe"]["stale"])

    def test_page_has_the_studio_and_the_settings(self):
        page = self.client.get("/").get_data(as_text=True)
        for text in ("Propor roteiros (formatos)", "Gerar outra versão", "Prévia da voz", "📱 Shorts", "Recortes do episódio",
                     "Formatos de Shorts", "Short original (história própria)"):
            self.assertIn(text, page)

    def test_settings_and_formats_panel(self):
        with patch.object(config, "SHORTS_FORMATS", dict(short_formats.DEFAULT_WEIGHTS)), patch.object(config, "SHORTS_PER_EPISODE", 4):
            current = self.client.get("/api/shorts-settings").get_json()
            self.assertEqual(current["per_episode"], 4)
            bad = self.client.post("/api/shorts-settings", json={"per_episode": 12})
            self.assertEqual(bad.status_code, 400)
            good = self.client.post("/api/shorts-settings", json={"formats": {"pov": 0}}).get_json()
            self.assertEqual(good["formats"]["pov"], 0.0)
            panel = self.client.get("/api/short-formats").get_json()
            self.assertIn("rows", panel)
            refused = self.client.post("/api/short-formats")
            self.assertEqual(refused.status_code, 400)               # no format has 3 Shorts yet

    def test_voice_preview_is_served_only_for_a_real_file(self):
        self.assertEqual(self.client.get("/api/shorts/voice?id=short_01").status_code, 404)
        folder = self.folder / "shorts" / "voice"
        folder.mkdir(parents=True)
        (folder / "short_01.wav").write_bytes(b"RIFF0000WAVE")
        self.assertEqual(self.client.get("/api/shorts/voice?id=short_01").status_code, 200)
        self.assertEqual(self.client.get("/api/shorts/voice?id=../x").status_code, 404)


if __name__ == "__main__":
    unittest.main()
