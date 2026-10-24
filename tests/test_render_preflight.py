"""Phase 2.2.1 section 6: offline render preflight (environment mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from scripts import render_preflight as preflight


def scene(scene_id, words, queries=3):
    value = {"id": scene_id, "narration": " ".join(["word"] * words)}
    for key in ("image_query", "image_query_2", "image_query_3")[:queries]:
        value[key] = "rainy window"
    return value


GOOD_VERSIONS = {"moviepy": "1.0.3", "numpy": "1.26.4", "edge-tts": "7.2.8"}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.script = root / "output" / "script.json"
        self.script.parent.mkdir()
        self.script.write_text(json.dumps({"title": "T", "sections": [
            scene(1, 100), scene(2, 200), scene(3, 50, queries=0)]}), encoding="utf-8")
        self.font = root / "font.ttf"
        self.font.write_bytes(b"ttf")
        self.music = root / "music"
        self.music.mkdir()
        (self.music / "track.mp3").write_bytes(b"mp3")
        (self.music / "tracks.json").write_text(json.dumps([
            {"file": "track.mp3", "moods": ["dark"], "license": "CC0"}]), encoding="utf-8")
        self.patches = [
            patch.object(config, "NARRATION_WPM", 120),
            patch.object(config, "VOICE_RATE", "+0%"),
            patch.object(config, "BROLL_INTERVAL", 10.0),
            patch.object(config, "VOICE_ID", "en-US-AndrewNeural"),
            patch.object(config, "FONT_PATHS", {"bold": [str(self.font)]}),
            patch.object(config, "MUSIC_ENABLED", True),
            patch.object(config, "MUSIC_LIBRARY_DIR", str(self.music)),
            patch.object(config, "PIXABAY_API_KEY", "secret-pixabay-value"),
            patch.object(preflight, "package_version", side_effect=GOOD_VERSIONS.get),
            patch.object(preflight, "importable", return_value=True),
            patch.object(preflight.shutil, "which", return_value="/usr/bin/ffmpeg"),
            patch.object(preflight.shutil, "disk_usage",
                         return_value=SimpleNamespace(free=50 * 1024 ** 3)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_preflight(self, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            code = preflight.run(self.script, **kwargs)
        return code, output.getvalue()

    def statuses(self, text):
        return {line.split("]")[1].split()[0]: line.split("]")[0].strip(" [")
                for line in text.splitlines() if line.strip().startswith("[")}

    def test_scene_estimates_and_long_image_flags(self):
        rows = preflight.scene_rows({"sections": [scene(1, 100), scene(2, 200)]}, wpm=120,
                                    voice_rate="-5%", interval=10, long_image_seconds=20)
        self.assertEqual(rows[0]["seconds"], round(100 / (120 * .95) * 60, 1))
        self.assertEqual(rows[0]["images"], 3)
        self.assertEqual(rows[0]["slots"], 6)
        self.assertEqual(rows[0]["status"], "PASS")       # 52.6 s / 3 = 17.5 s per image
        self.assertEqual(rows[1]["status"], "WARN")       # 105 s / 3 = 35 s per image
        self.assertEqual(preflight.rate_factor("+10%"), 1.1)
        self.assertEqual(preflight.rate_factor("bogus"), 1.0)

    def test_scene_without_image_queries_fails(self):
        code, text = self.run_preflight()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(text)["image"], "FAIL")
        self.assertIn("no image queries: 3", text)

    def test_all_good_passes_and_never_prints_key_values(self):
        self.script.write_text(json.dumps({"title": "T", "sections": [scene(1, 100)]}),
                               encoding="utf-8")
        code, text = self.run_preflight()
        self.assertEqual(code, 0, text)
        self.assertIn("ready to render", text)
        self.assertNotIn("secret-pixabay-value", text)
        statuses = self.statuses(text)
        for name in ("moviepy", "numpy", "ffmpeg", "fonts", "music", "edge-tts", "voice", "stock"):
            self.assertEqual(statuses[name], "PASS", name)

    def test_wrong_toolchain_versions_fail(self):
        versions = {"moviepy": "2.1.1", "numpy": "2.0.0", "edge-tts": "7.2.8"}
        with patch.object(preflight, "package_version", side_effect=versions.get):
            code, text = self.run_preflight()
        statuses = self.statuses(text)
        self.assertEqual((statuses["moviepy"], statuses["numpy"]), ("FAIL", "FAIL"))
        self.assertEqual(code, 1)

    def test_missing_ffmpeg_edge_and_disk(self):
        with patch.object(preflight.shutil, "which", return_value=None), \
             patch.object(preflight, "importable", return_value=False), \
             patch.object(preflight.shutil, "disk_usage",
                          return_value=SimpleNamespace(free=100 * 1024 ** 2)):
            _, text = self.run_preflight()
        statuses = self.statuses(text)
        self.assertEqual((statuses["ffmpeg"], statuses["edge-tts"], statuses["disk"]),
                         ("FAIL", "FAIL", "FAIL"))

    def test_warnings_for_music_voice_fonts_and_keys(self):
        for track in self.music.glob("*"):
            track.unlink()
        with patch.object(config, "VOICE_ID", "en-US-GuyNeural"), \
             patch.object(config, "FONT_PATHS", {"bold": ["missing.ttf"]}), \
             patch.object(config, "PIXABAY_API_KEY", ""), \
             patch.object(config, "STOCK_ENABLED", True):
            _, text = self.run_preflight()
        statuses = self.statuses(text)
        for name in ("music", "voice", "fonts", "stock"):
            self.assertEqual(statuses[name], "WARN", name)

    def test_missing_script_fails(self):
        self.script.unlink()
        code, text = self.run_preflight()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(text)["script"], "FAIL")

    def test_shot_list_progress_and_gpu_estimate(self):
        output = self.script.parent
        self.assertEqual(preflight.imagegen_check(output)[1], "PASS")          # no shot list
        (output / "shot_list.json").write_text(json.dumps({"shots": [
            {"shot_id": "a", "reuse_of": "", "image_path": "a.jpg"},
            {"shot_id": "b", "reuse_of": ""}, {"shot_id": "c", "reuse_of": "a"}]}), encoding="utf-8")
        (output / "a.jpg").write_bytes(b"jpg")
        calibration = output / "cal.json"
        calibration.write_text(json.dumps({"modes": {"ref": {"seconds_per_image": 60}}}), encoding="utf-8")
        with patch.object(config, "IMAGEGEN_CALIBRATION_FILE", str(calibration), create=True):
            name, status, detail = preflight.imagegen_check(output)
        self.assertEqual(status, "WARN")
        self.assertIn("1/2 unique shots generated", detail)
        self.assertIn("≈ 1 min of GPU time left", detail)

    def test_live_comfyui_check(self):
        from video.imagegen_comfyui import ComfyClient
        with patch.object(ComfyClient, "available", return_value=False):
            self.assertEqual(preflight.comfyui_check()[1], "WARN")
        with patch.object(ComfyClient, "available", return_value=True), \
                patch.object(ComfyClient, "vram_used_mb", return_value=2048.0):
            self.assertIn("2048 MB", preflight.comfyui_check()[2])

    def test_word_synced_voices_read_from_narrator_source(self):
        self.assertIn("en-US-AndrewNeural", preflight.word_synced_voices())


if __name__ == "__main__":
    unittest.main()
