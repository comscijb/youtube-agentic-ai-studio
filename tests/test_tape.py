"""Phase 3.6-F: tape (VHS) effect — filter graph, glitch timing, ASS captions, photosensitivity guard."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import tape

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:                                    # noqa: BLE001
    FFMPEG = shutil.which("ffmpeg")


class SettingsTests(unittest.TestCase):
    def test_default_is_subtle_and_unknown_is_off(self):
        self.assertEqual(config.VIDEO_TAPE_EFFECT, "subtle")
        self.assertEqual(tape.intensity(), "subtle")
        self.assertEqual(tape.intensity("medium"), "medium")
        self.assertEqual(tape.intensity("loud"), "off")


class GlitchTests(unittest.TestCase):
    def test_glitches_only_on_cuts_and_never_too_close(self):
        windows = tape.glitch_windows([12.0, 12.5, 30.0, 61.0], 60.0)
        self.assertEqual(windows, [(11.9, 12.2), (29.9, 30.2)])          # 12.5 too close; 61 after the end
        self.assertEqual(tape.glitch_windows([5.0], 20.0, title_card=True)[0], (0.2, 0.5))
        self.assertTrue(all(end - start <= tape.GLITCH_SECONDS + 1e-9 for start, end in windows))

    def test_photosensitivity_guard(self):
        for name, level in tape.LEVELS.items():
            report = tape.check_safety(level, tape.glitch_windows([3.0, 9.0], 20.0), fps=30)
            self.assertLessEqual(report["max_luma_step"], 0.10, name)
        loud = dict(tape.LEVELS["medium"], wobble=0.9)
        with self.assertRaises(tape.TapeSafetyError):
            tape.check_safety(loud, [], fps=30)
        bursts = [(0.0, 0.1), (0.2, 0.3), (0.4, 0.5), (0.6, 0.7)]
        with self.assertRaises(tape.TapeSafetyError) as caught:
            tape.check_safety(tape.LEVELS["subtle"], bursts, fps=30)
        self.assertIn("4 glitches within one second", str(caught.exception))


class GraphTests(unittest.TestCase):
    def test_graph_order_glitch_enable_and_captions_last(self):
        graph = tape.filter_graph("subtle", (1920, 1080), 30, [(11.9, 12.2)], subtitles="C:\\out\\caps.ass",
                                  fonts_dir="C:\\fonts", scanlines="C:\\out\\scan.png")
        for part in ("noise=c0s=5", "chromashift=cbh=1", "vignette", "movie='C\\:/out/scan.png'",
                     "enable='between(t,11.9,12.2)'", "drawbox=", "overlay=x=12"):
            self.assertIn(part, graph)
        self.assertTrue(graph.endswith("subtitles=filename='C\\:/out/caps.ass':fontsdir='C\\:/fonts'"))
        self.assertLess(graph.index("vignette"), graph.index("subtitles"))
        self.assertIn("drawgrid", tape.filter_graph("medium", (1920, 1080), 30, []))     # no PNG: drawgrid

    def test_ass_captions_match_the_pill(self):
        with tempfile.TemporaryDirectory() as temp:
            path = tape.write_ass([{"start": 1.25, "end": 3.5, "text": "He {smiled}\nat them."}], Path(temp, "c.ass"),
                                  (1920, 1080), font_path=None, font_size=36, margin_bottom=90)
            text = Path(path).read_text(encoding="utf-8")
        self.assertIn("PlayResX: 1920", text)
        style = next(line for line in text.splitlines() if line.startswith("Style: Caption"))
        fields = style.split(",")
        self.assertEqual((fields[2], fields[15], fields[18], fields[21]), ("36", "3", "2", "90"))  # size, box, bottom, margin
        self.assertIn("Dialogue: 0,0:00:01.25,0:00:03.50,Caption,,0,0,0,,He (smiled) at them.", text)

    def test_params_write_their_files(self):
        with tempfile.TemporaryDirectory() as temp:
            params, report = tape.ffmpeg_params("medium", size=(640, 360), fps=25, cuts=[2.0], duration=4.0,
                                                captions=[{"start": 0.0, "end": 1.0, "text": "Hi."}], output_dir=temp)
            self.assertTrue(Path(temp, "tape_captions.ass").is_file())
            self.assertTrue(Path(temp, "tape_scanlines.png").is_file())
        self.assertEqual(params[0], "-vf")
        self.assertEqual((report["glitches"], report["captions"], report["intensity"]), (1, True, "medium"))


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class RenderTests(unittest.TestCase):
    """Runs the real graph on a synthetic clip: it must encode, and stay within the flash rules."""

    def test_graph_encodes_and_keeps_frame_to_frame_luminance_steady(self):
        size, fps, seconds = (320, 180), 25, 3
        with tempfile.TemporaryDirectory() as temp:
            params, _ = tape.ffmpeg_params("medium", size=size, fps=fps, cuts=[1.0, 2.2], duration=seconds,
                                           captions=[{"start": 0.2, "end": 2.5, "text": "Tonight it didn't."}],
                                           output_dir=temp, font_size=14, margin_bottom=16)
            source = f"color=c=0x404040:size={size[0]}x{size[1]}:rate={fps}:duration={seconds}"
            done = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", source, *params,
                                   "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace")[-500:])
        frame = size[0] * size[1]
        means = [sum(done.stdout[index:index + frame]) / frame / 255
                 for index in range(0, len(done.stdout) - frame + 1, frame)]
        self.assertGreaterEqual(len(means), fps * seconds - 1)
        steps = [abs(after - before) for before, after in zip(means, means[1:])]
        self.assertLessEqual(max(steps), 0.10)                     # no flash between consecutive frames


if __name__ == "__main__":
    unittest.main()
