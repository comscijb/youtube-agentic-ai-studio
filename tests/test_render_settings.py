"""Phase 3.4-F: pacing, constant-quality encode and the final true-peak check (ffmpeg mocked)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from video import creator, music


def loudnorm_stderr(i: float, tp: float) -> str:
    return "[Parsed_loudnorm_0]\n" + json.dumps({
        "input_i": str(i), "input_tp": str(tp), "input_lra": "5.0", "input_thresh": "-24.0",
        "target_offset": "0.1", "output_i": str(i), "output_tp": str(tp)})


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual((config.IMAGEGEN_SHOT_MIN_SECONDS, config.IMAGEGEN_SHOT_MAX_SECONDS), (6.0, 9.0))
        self.assertEqual(config.IMAGEGEN_MAX_UNIQUE_SHOTS, int(os.getenv("IMAGEGEN_MAX_UNIQUE_SHOTS", "75")))
        self.assertEqual((config.LOUDNESS_TRUE_PEAK, config.FINAL_TRUE_PEAK_MAX), (-1.5, -1.0))

    def test_encode_uses_constant_quality_and_logs_size_duration_bitrate(self):
        settings = creator.encode_settings()
        self.assertEqual(settings["preset"], "veryfast")
        self.assertEqual(settings["ffmpeg_params"][:2], ["-crf", "20"])
        self.assertIn("+faststart", settings["ffmpeg_params"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "final_video.mp4")
            path.write_bytes(b"\0" * (6_000_000 // 8 * 10))           # 6 Mbps for 10 s
            self.assertIn("6.0 Mbps", creator.render_summary(str(path), 10))
            self.assertIn("0m 10s", creator.render_summary(str(path), 10))


class TruePeakTests(unittest.TestCase):
    def run_check(self, measurements):
        calls = []
        measured = iter(measurements)

        def fake_run(args):
            calls.append(args)
            if args[-1] == "-":                                       # an analysis pass
                return SimpleNamespace(returncode=0, stderr=loudnorm_stderr(-14.0, -0.9))
            Path(args[-1]).write_bytes(b"remuxed")
            return SimpleNamespace(returncode=0, stderr=loudnorm_stderr(-14.0, -1.5))

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "final_video.mp4")
            path.write_bytes(b"original")
            with patch.object(music, "_run", side_effect=fake_run), \
                    patch.object(music, "measure_loudness", side_effect=lambda _: next(measured)):
                result = music.limit_final_true_peak(str(path))
            content = path.read_bytes()
            leftovers = list(Path(temp).glob("*.limited.mp4"))
        return result, calls, content, leftovers

    def test_a_peak_within_the_limit_is_left_alone(self):
        result, calls, content, _ = self.run_check([{"integrated_lufs": -14.0, "true_peak_dbtp": -1.4}])
        self.assertEqual(result["action"], "none")
        self.assertEqual(calls, [])
        self.assertEqual(content, b"original")

    def test_a_hot_peak_is_limited_and_remuxed_without_reencoding_the_video(self):
        result, calls, content, leftovers = self.run_check([
            {"integrated_lufs": -14.0, "true_peak_dbtp": -0.9},
            {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}])
        self.assertEqual(result["action"], "limited_and_remuxed")
        self.assertEqual((result["before_true_peak_dbtp"], result["true_peak_dbtp"]), (-0.9, -1.5))
        remux = calls[-1]
        self.assertEqual(remux[remux.index("-c:v") + 1], "copy")
        self.assertIn("TP=-1.5", remux[remux.index("-af") + 1])
        self.assertEqual(content, b"remuxed")
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
