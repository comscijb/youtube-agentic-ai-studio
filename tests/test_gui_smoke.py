"""Phase 3.9.1-D: the GUI in a real browser against the fixture project — one visible tab at a time, no
page or console errors, the Vozes panel has play buttons. Skipped when Playwright or Chromium is
missing (pip install playwright && python -m playwright install chromium). No live calls: Gemini is
disabled and the voice samples are fake files."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:                                    # noqa: BLE001 — not installed, or no browser binary
        return False


@unittest.skipUnless(chromium_available(), "Playwright/Chromium not installed")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        from scripts import gui_audit, voice_audition
        cls.temp = tempfile.TemporaryDirectory()
        temp = Path(cls.temp.name)
        samples = temp / "voices"
        samples.mkdir()
        name = voice_audition.sample_name("edge", "en-GB-RyanNeural", "x")
        (samples / name).write_bytes(b"ID3")
        (samples / "results.json").write_text(json.dumps({"text": "x", "rows": [
            {"backend": "edge", "voice": "en-GB-RyanNeural", "file": name, "rtf": 0.2, "coverage": 1.0}]}),
            encoding="utf-8")
        cls.audition = patch.object(gui, "_audition_dir", return_value=samples)
        cls.audition.start()
        cls.server, cls.url, cls.stack = gui_audit.serve(temp)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for item in reversed(cls.stack):
            item.stop()
        cls.audition.stop()
        cls.temp.cleanup()

    def test_tabs_errors_and_voices(self):
        from playwright.sync_api import sync_playwright
        problems = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda exc: problems.append(f"pageerror: {exc}"))
            page.on("console", lambda msg: msg.type == "error" and problems.append(f"console.error: {msg.text}"))
            page.goto(self.url)
            page.wait_for_timeout(1200)
            page.evaluate("closeHome()")
            tabs = page.eval_on_selector_all(".tab[data-tab]", "items => items.map(item => item.dataset.tab)")
            self.assertEqual(set(tabs), {"project", "generate", "review", "settings", "performance"})
            for tab in tabs:
                page.click(f".tab[data-tab='{tab}']")
                page.wait_for_timeout(400)
                visible = page.eval_on_selector_all(
                    ".tab-content", "items => items.filter(item => item.offsetParent !== null).map(item => item.id)")
                self.assertEqual(visible, [f"tab-{tab}"], tab)
            page.evaluate("openVoices()")
            page.wait_for_function("document.querySelectorAll('#voices-list audio').length > 0", timeout=5000)
            players = page.eval_on_selector_all("#voices-list audio",
                                                "items => items.map(item => [item.getAttribute('preload'), item.getAttribute('src')])")
            browser.close()
        self.assertTrue(players)
        self.assertTrue(all(preload == "none" and src.startswith("/api/voices/sample/") for preload, src in players))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
