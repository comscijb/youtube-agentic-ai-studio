"""Phase 3.3-C: stock imagery is off by default and nothing reaches Pixabay/Openverse."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json
from tests.fake_comfy import FakeComfy
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_generation import SHOTS
from tests.test_shot_planning import VISUAL_BIBLE
from video import shot_generation as gen
from video import stock
from video.imagegen_comfyui import ComfyClient, ImageGenerator


class NoNetwork:
    """A requests session that fails the test on any call."""

    def __getattr__(self, name):
        raise AssertionError(f"stock made a network call ({name}) while STOCK_ENABLED is false")


class StockDisabledTests(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(config.STOCK_ENABLED)
        self.assertFalse(stock.enabled())

    def test_manager_and_downloader_never_query_providers(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            manager = stock.StockManager(temp, session=NoNetwork())
            self.assertEqual(manager.provider_order(), [])
            self.assertIsNone(manager.acquire("a dark loft at night", "landscape", 1, 1))
            with patch.object(stock, "StockManager", side_effect=AssertionError("no manager when disabled")):
                image_map = stock.download_images({"sections": [{"id": 1, "image_query": "loft"},
                                                                {"id": 2, "image_query": "rain"}]}, temp)
        self.assertEqual(image_map, {1: None, 2: None})

    def test_failed_shots_never_fall_back_to_stock_in_either_style(self):
        for style in ("cinematic_photo", "dark_illustration"):
            with self.subTest(style=style), tempfile.TemporaryDirectory() as temp, \
                    patch.object(config, "VISUAL_STYLE", style), \
                    patch.object(gen, "stock_fallback", side_effect=AssertionError("stock must not be used")), \
                    patch.object(gen, "missing_approvals", return_value=[]), redirect_stdout(io.StringIO()):
                atomic_write_json(Path(temp, "visual_bible.json"), json.loads(json.dumps(VISUAL_BIBLE)))
                atomic_write_json(Path(temp, "shot_list.json"), {"shots": json.loads(json.dumps(SHOTS))})
                generator = ImageGenerator(temp, ComfyClient("http://127.0.0.1:8188",
                                                             session=FakeComfy(fail_runs=99), sleep=lambda _: None))
                summary = gen.generate_shots(temp, generator, only="s01_02")
            self.assertEqual(summary["fallback"], 0)
            self.assertEqual(len(summary["failed"]) + len(summary["held"]), 1)

    def test_stock_allowed_only_for_photo_or_character_free_shots_when_enabled(self):
        with patch.object(config, "STOCK_ENABLED", True):
            with patch.object(config, "VISUAL_STYLE", "dark_illustration"):
                self.assertFalse(gen.stock_allowed({"characters": ["julian_vance"]}))
                self.assertTrue(gen.stock_allowed({"characters": []}))
            with patch.object(config, "VISUAL_STYLE", "cinematic_photo"):
                self.assertTrue(gen.stock_allowed({"characters": ["julian_vance"]}))
        self.assertFalse(gen.stock_allowed({"characters": []}))

    def test_preflight_needs_no_stock_keys(self):
        from scripts import render_preflight
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "PIXABAY_API_KEY", ""):
            checks = render_preflight.environment_checks(Path(temp))
        _, status, detail = next(item for item in checks if item[0] == "stock keys")
        self.assertEqual(status, "PASS")
        self.assertIn("disabled", detail)


class GuiStockTests(unittest.TestCase):
    def test_settings_checkbox_round_trip(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(gui, "ROOT", temp), \
                patch.object(config, "STOCK_ENABLED", False):
            Path(temp, "config.py").write_text("STOCK_PROVIDER = 'auto'\n", encoding="utf-8")
            client = gui.app.test_client()
            self.assertFalse(client.get("/api/settings").get_json()["stock_enabled"])
            self.assertTrue(client.post("/api/save-settings", json={"stock_enabled": True}).get_json()["ok"])
            self.assertTrue(config.STOCK_ENABLED)
            self.assertIn("STOCK_ENABLED=true", Path(temp, ".env").read_text(encoding="utf-8"))
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        self.assertIn("Usar banco de imagens (Pixabay/Openverse) como fallback", page)

    def test_images_step_runs_shot_generation_not_stock(self):
        import gui
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "narration.mp3").write_bytes(b"audio")
            with patch.object(config, "OUTPUT_DIR", temp), patch.object(config, "IMAGES_DIR", str(Path(temp, "images"))), \
                    patch.object(gui, "_state", dict(gui._state, research={"x": 1}, script={"sections": []})), \
                    patch("video.stock.download_images", side_effect=AssertionError("no stock download")), \
                    patch.object(gen, "ensure_visual_bible", return_value=VISUAL_BIBLE) as bible, \
                    patch.object(gen, "ensure_shot_list"), \
                    patch.object(gen, "plan_and_render", side_effect=gen.ApprovalRequired("Approve julian_vance")) as shots, \
                    patch("video.imagegen_comfyui.ImageGenerator"), redirect_stdout(io.StringIO()):
                gui._run_pipeline(["images", "video"])
        self.assertTrue(shots.called)                  # the legacy "images" step is an alias of "shots"
        logs = []
        while not gui._log_queue.empty():
            logs.append(gui._log_queue.get())
        self.assertTrue(any("The 'images' step is now 'shots'" in message for _, message in logs))


if __name__ == "__main__":
    unittest.main()
