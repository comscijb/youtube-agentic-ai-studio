"""Phase 3.8-D: continuous film look — looks, no cut-tied windows, flicker guard, cached dust, captions last."""
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
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
    def test_default_is_super8_and_old_values_are_mapped(self):
        self.assertEqual(config.VIDEO_FILM_EFFECT if config.VIDEO_FILM_EFFECT not in tape.ALIASES
                         else tape.ALIASES[config.VIDEO_FILM_EFFECT], tape.look())
        with patch.object(config, "VIDEO_FILM_EFFECT", None), patch.object(config, "VIDEO_TAPE_EFFECT", None):
            self.assertEqual(tape.look(), "super8")
        tape._warned.clear()
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(tape.look("subtle"), "super8")
            self.assertEqual(tape.look("medium"), "super8_strong")
            self.assertEqual(tape.look("subtle"), "super8")
        self.assertEqual(out.getvalue().count("from the tape effect"), 2)                # one warning per old value
        self.assertEqual(tape.look("loud"), "off")
        self.assertEqual(tape.intensity, tape.look)
        for removed in ("glitch_windows", "write_scanlines", "LEVELS", "GLITCH_SECONDS"):
            self.assertFalse(hasattr(tape, removed), removed)
        self.assertFalse(hasattr(config, "VIDEO_TAPE_GLITCH_TITLE"))

    def test_flicker_and_dust_stay_within_the_photosensitivity_guard(self):
        for name, spec in tape.LOOKS.items():
            with self.subTest(look=name):
                report = tape.check_safety(spec, fps=30)
                self.assertLessEqual(report["max_luma_step"], tape.MAX_LUMA_STEP)
                self.assertLessEqual(report["flicker_amplitude"], 0.03)            # ±2-3 % at most
        self.assertGreaterEqual(tape.flicker_amplitude(tape.LOOKS["super8"]), 0.02)
        self.assertEqual(tape.flicker_amplitude(tape.LOOKS["grain"]), 0)
        hz = [frequency for _, frequency in tape.LOOKS["super8"]["flicker"]]
        self.assertTrue(all(2 <= value <= 6 for value in hz))
        loud = dict(tape.LOOKS["super8"], flicker=((0.2, 6.0),))
        with self.assertRaises(tape.TapeSafetyError):
            tape.check_safety(loud, fps=30)


class GraphTests(unittest.TestCase):
    def graph(self, look, **kwargs):
        return tape.filter_graph(look, (1920, 1080), 30, subtitles="C:\\out\\caps.ass", dust="C:\\film\\d.mkv",
                                 offset=3.5, **kwargs)

    def test_each_look_and_nothing_tied_to_cuts(self):
        grain, super8, strong = (self.graph(name) for name in ("grain", "super8", "super8_strong"))
        for graph in (grain, super8, strong):
            self.assertNotIn("enable=", graph)                          # no windows on cut times
            self.assertNotIn("drawgrid", graph)                         # no scanlines (that is video)
            self.assertNotIn("chromashift", graph)
            self.assertIn("noise=c0s=", graph)
            self.assertIn("blend=all_mode=grainmerge", graph)
            self.assertIn("[base][film]blend=all_mode=grainmerge", graph)          # one full-size blend
            self.assertNotIn("vignette=", graph)                                     # baked into the layer
        self.assertNotIn("eq=brightness", grain)                        # grain: no flicker, dust or weave
        self.assertNotIn("movie=", tape.filter_graph("grain", (1920, 1080), 30))
        self.assertNotIn("crop=", grain)
        for graph in (super8, strong):
            self.assertIn("eq=brightness='0.0", graph)
            self.assertIn("seek_point=3.5", graph)
            self.assertIn("lutyuv=y='val+0.0", graph)                   # lifted blacks + warm tint, one LUT
            self.assertNotIn("colorbalance", graph)
            self.assertRegex(graph, r"crop=w=iw-\d:h=ih-\d")            # gate weave
        self.assertIn("noise=c0s=28", strong)
        self.assertIn("s=960x540", super8)                              # grain at half resolution, upscaled

    def test_captions_come_after_the_effect(self):
        graph = self.graph("super8")
        self.assertEqual(graph.rsplit(",", 1)[-1], "subtitles=filename='C\\:/out/caps.ass'")
        self.assertLess(graph.index("[base][film]blend"), graph.index("subtitles"))

    def test_shorts_use_the_same_look_at_9_16(self):
        from video import shorts_render as render
        with patch.object(tape, "dust_layer", return_value=Path("C:/film/portrait.mkv")) as dust:
            graph = render.video_filter("super8", "C:\\x\\a.ass", None, key="short_01")
        self.assertEqual(dust.call_args.args, ("super8", (1080, 1920), render.FPS))
        self.assertIn("s=540x960", graph)
        self.assertNotIn("enable=", graph)
        self.assertLess(graph.index("[base][film]blend"), graph.index("subtitles"))
        self.assertTrue(render.video_filter("off", "C:\\x\\a.ass", None).startswith("subtitles="))

    def test_ass_captions_match_the_pill(self):
        with tempfile.TemporaryDirectory() as temp:
            path = tape.write_ass([{"start": 1.25, "end": 3.5, "text": "He {smiled}\nat them."}], Path(temp, "c.ass"),
                                  (1920, 1080), font_path=None, font_size=36, margin_bottom=90)
            text = Path(path).read_text(encoding="utf-8")
        style = next(line for line in text.splitlines() if line.startswith("Style: Caption"))
        fields = style.split(",")
        self.assertEqual((fields[2], fields[15], fields[18], fields[21]), ("36", "3", "2", "90"))
        self.assertIn("Dialogue: 0,0:00:01.25,0:00:03.50,Caption,,0,0,0,,He (smiled) at them.", text)


class GuiSettingTests(unittest.TestCase):
    def test_film_effect_selector_round_trip(self):
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(gui, "ROOT", temp), \
                patch.object(config, "VIDEO_FILM_EFFECT", "super8"), patch.object(config, "VIDEO_TAPE_EFFECT", "super8"):
            Path(temp, "config.py").write_text("RENDER_PRESET = 'veryfast'\n", encoding="utf-8")
            client = gui.app.test_client()
            self.assertEqual(client.get("/api/settings").get_json()["film_effect"], "super8")
            self.assertEqual(client.post("/api/save-settings", json={"film_effect": "loud"}).status_code, 400)
            self.assertTrue(client.post("/api/save-settings", json={"film_effect": "grain"}).get_json()["ok"])
            self.assertEqual(tape.look(), "grain")
            self.assertIn("VIDEO_FILM_EFFECT=grain", Path(temp, ".env").read_text(encoding="utf-8"))
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        for label in ("Efeito de filme", "Desligado", "Grão de cinema", "Super 8 forte"):
            self.assertIn(label, page)


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class DustLayerTests(unittest.TestCase):
    def test_overlay_is_rendered_once_and_reused(self):
        first = tape.dust_layer("super8", (640, 360), 25)
        stamp = first.stat().st_mtime_ns
        again = tape.dust_layer("super8", (640, 360), 25)
        self.assertEqual((again, again.stat().st_mtime_ns), (first, stamp))
        self.assertEqual(first.parent, Path(config.FILM_ASSETS_DIR))
        self.assertIn("320x180", first.name)
        grain = tape.dust_layer("grain", (640, 360), 25)                            # vignette only, one frame
        self.assertLess(grain.stat().st_size, first.stat().st_size)
        self.assertNotEqual(tape.start_offset("a/final_video.mp4"), tape.start_offset("b/final_video.mp4"))
        self.assertTrue(0 <= tape.start_offset("x") < tape.DUST_SECONDS)

    def test_vignette_darkens_only_towards_the_corners(self):
        offsets = tape.vignette_offsets((96, 54), 40)
        self.assertEqual(offsets[27, 48], 0)                                        # centre untouched
        self.assertLess(offsets[0, 0], -30)                                         # corners darker
        self.assertTrue((offsets <= 0).all())
        frames = list(tape.dust_frames((96, 54), 25, None, vignette=40))
        self.assertEqual(len(frames), 1)                                            # static: one looped frame

    def test_dust_is_sparse_and_neutral(self):
        frames = list(tape.dust_frames((96, 54), 25, tape.LOOKS["super8"]["dust"]))       # no vignette here
        self.assertEqual(len(frames), tape.DUST_SECONDS * 25)
        luma = 96 * 54
        marked = sum(1 for frame in frames if any(value != 128 for value in frame[:luma]))
        self.assertLess(marked, len(frames) / 4)                                    # mostly clean frames
        self.assertTrue(all(value == 128 for value in frames[0][luma:]))            # neutral chroma


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class RenderTests(unittest.TestCase):
    """Runs the real graph on a synthetic clip with a hard cut: it encodes, and stays within the flash rules."""

    def test_graph_encodes_and_keeps_frame_to_frame_luminance_steady(self):
        size, fps, seconds = (320, 180), 25, 3
        with tempfile.TemporaryDirectory() as temp:
            params, report = tape.ffmpeg_params("super8_strong", size=size, fps=fps, output_dir=temp,
                                                captions=[{"start": 0.2, "end": 2.5, "text": "Tonight it didn't."}],
                                                font_size=14, margin_bottom=16, key="clip")
            source = (f"color=c=0x404040:size={size[0]}x{size[1]}:rate={fps}:duration=1.5[a];"
                      f"color=c=0x505050:size={size[0]}x{size[1]}:rate={fps}:duration=1.5[b];[a][b]concat")
            done = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", source, *params,
                                   "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace")[-500:])
        self.assertEqual((report["look"], report["captions"], report["dust"]), ("super8_strong", True, True))
        frame = size[0] * size[1]
        means = [sum(done.stdout[index:index + frame]) / frame / 255
                 for index in range(0, len(done.stdout) - frame + 1, frame)]
        self.assertGreaterEqual(len(means), fps * seconds - 1)
        steps = [abs(after - before) for before, after in zip(means, means[1:])]
        cut = round(1.5 * fps)
        self.assertLessEqual(max(steps[:cut - 1] + steps[cut:]), 0.10)           # no flash apart from the cut itself


if __name__ == "__main__":
    unittest.main()
