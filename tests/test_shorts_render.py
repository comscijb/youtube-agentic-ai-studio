"""Phase 3.7-B.2: Shorts rendering — 9:16 reframing, blur-fill, safe captions, exact audio slice, loudness."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from agents.narrative import atomic_write_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import shorts_render as render
from video import tape

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:                                    # noqa: BLE001
    FFMPEG = shutil.which("ffmpeg")


def person(box, face=None):
    return {"box": box, "face": {"box": face} if face else None}


class ReframeTests(unittest.TestCase):
    SOURCE = (1920, 1080)

    def test_window_centres_on_the_subject_and_stays_inside(self):
        window = round(1080 * 1080 / 1920)
        plan = render.crop_plan([[0.45, 0.2, 0.55, 0.5]], self.SOURCE)
        self.assertEqual((plan["mode"], plan["width"]), ("crop", window))
        self.assertAlmostEqual(plan["x"] + window / 2, 0.5 * 1920, delta=1)
        edge = render.crop_plan([[0.0, 0.2, 0.08, 0.5]], self.SOURCE)
        self.assertEqual(edge["x"], 0)
        self.assertEqual(render.crop_plan([], self.SOURCE)["x"], (1920 - window) // 2)

    def test_subjects_too_wide_use_the_blur_fill(self):
        self.assertEqual(render.crop_plan([[0.1, 0.2, 0.2, 0.5], [0.7, 0.2, 0.8, 0.5]], self.SOURCE), {"mode": "fill"})
        self.assertEqual(render.crop_plan([[0.4, 0.2, 0.5, 0.5]], (1080, 1920)), {"mode": "fill"})   # already vertical

    def test_main_subjects_ignore_small_background_figures(self):
        detection = {"persons": [person([0.3, 0.1, 0.6, 1.0], [0.4, 0.15, 0.5, 0.35]),
                                 person([0.85, 0.4, 0.88, 0.6])]}
        self.assertEqual(render.main_subjects(detection), [[0.4, 0.15, 0.5, 0.35]])
        self.assertEqual(render.main_subjects(None), [])
        two = {"persons": [person([0.1, 0.1, 0.4, 1.0]), person([0.6, 0.1, 0.9, 1.0], [0.7, 0.1, 0.8, 0.3])]}
        self.assertEqual(len(render.main_subjects(two)), 2)                # bodies when a face is missing

    def test_pan_stays_inside_the_image(self):
        plan = {"mode": "crop", "x": 1300, "width": 608}
        self.assertEqual(render.pan_offset(plan, 1920, 1, 1.0), 1312)       # clamped at the right edge
        self.assertEqual(render.pan_offset(plan, 1920, 0, 0.7), 1300)
        self.assertLess(render.pan_offset(plan, 1920, -1, 1.0), 1300)

    def test_blur_fill_has_no_bars(self):
        image = Image.new("RGB", (1920, 1080), (200, 180, 160))
        frame = render.fill_frame(image)
        self.assertEqual(frame.size, (1080, 1920))
        self.assertGreater(sum(frame.getpixel((540, 5))), 150)                # blurred copy, not a black bar

    def test_short_shots_are_not_split_further(self):
        shot_list = {"shots": [{"shot_id": "a", "start": 0, "end": 11, "image_path": "a.jpg"},
                               {"shot_id": "b", "start": 11, "end": 12.5, "image_path": "b.jpg"},
                               {"shot_id": "c", "start": 12.5, "end": 30, "image_path": "c.jpg"}]}
        pieces = render.shot_pieces(shot_list, "/tmp", 10.0, 25.0)
        self.assertEqual([(item["shot_id"], item["start"], item["end"]) for item in pieces],
                         [("c", 0.0, 15.0)])                               # 1 s of "a" and 1.5 s of "b" absorbed
        pieces = render.shot_pieces(shot_list, "/tmp", 0.0, 30.0)
        self.assertEqual([item["shot_id"] for item in pieces], ["a", "c"])
        self.assertEqual(pieces[0]["end"], 12.5)


class CaptionTests(unittest.TestCase):
    def test_cues_have_2_to_4_words_and_stay_in_the_safe_area(self):
        words = [{"text": word, "start": index * 0.4, "end": index * 0.4 + 0.35, "sentence_end": word.endswith(".")}
                 for index, word in enumerate("He thanked the voice for the best conversation he had had in years.".split())]
        cues = render.caption_cues(words, 6.0)
        self.assertTrue(all(len(cue["text"].split()) <= 4 for cue in cues))
        margins = render.caption_margins()
        left, top, right, bottom = render.caption_box(760, 2 * render.CAPTION_SIZE, margins)
        self.assertLessEqual(right, render.W * (1 - render.SAFE_RIGHT))
        self.assertLessEqual(bottom, render.H * (1 - render.SAFE_BOTTOM))
        self.assertGreater(top, render.H / 2)                                 # lower-middle third
        with tempfile.TemporaryDirectory() as temp:
            text = Path(tape.write_ass(cues, Path(temp, "s.ass"), (1080, 1920), font_path=None, font_size=104,
                                       box=False, **margins)).read_text(encoding="utf-8")
        style = next(line for line in text.splitlines() if line.startswith("Style:")).split(",")
        self.assertEqual((style[15], style[19], style[20], style[21]), ("1", "60", str(margins["margin_right"]),
                                                                        str(margins["margin_bottom"])))

    def test_filter_follows_the_episode_film_setting(self):
        self.assertTrue(render.video_filter("off", "C:\\x\\a.ass", None).startswith("subtitles="))
        with patch.object(tape, "dust_layer", return_value=None):
            graph = render.video_filter("grain", "C:\\x\\a.ass", None)
        self.assertIn("blend=all_mode=grainmerge", graph)
        self.assertLess(graph.index("[base][film]blend"), graph.index("subtitles"))


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class AudioAndRenderTests(unittest.TestCase):
    def tone(self, path, seconds=12):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={seconds}", "-af", "volume=0.3", str(path)], check=True)

    def duration(self, path):
        from video.music import media_duration
        return media_duration(str(path))

    def test_audio_slice_is_exact_and_normalised(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp, "mix.mp3")
            self.tone(source)
            report = render.audio_slice(source, Path(temp, "cut.mp3"), 2.0, 9.5)
            seconds = self.duration(Path(temp, "cut.mp3"))
        self.assertAlmostEqual(seconds, 7.5, delta=0.08)
        self.assertAlmostEqual(report["output_integrated_lufs"], -14.0, delta=1.0)
        self.assertLessEqual(report["output_true_peak_dbtp"], -1.0)

    def test_a_late_slice_is_not_faded_to_silence(self):
        # Regression (2026-11-05): with output seeking the fade-out sat on the source timeline, so a slice
        # starting after its own length was silent and loudnorm failed with measured_I=-inf.
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp, "mix.mp3")
            self.tone(source, 30)
            report = render.audio_slice(source, Path(temp, "late.mp3"), 20.0, 26.0)
            seconds = self.duration(Path(temp, "late.mp3"))
        self.assertAlmostEqual(seconds, 6.0, delta=0.08)
        self.assertAlmostEqual(report["output_integrated_lufs"], -14.0, delta=1.0)

    def test_a_tiny_short_renders_at_1080x1920(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.tone(folder / "narration_mixed.mp3", 8)
            (folder / "images").mkdir()
            for name, colour in (("a", (90, 70, 60)), ("b", (60, 70, 90))):
                image = Image.new("RGB", (1920, 1080), colour)
                ImageDraw.Draw(image).rectangle([800, 200, 1100, 1000], fill=(200, 190, 170))
                image.save(folder / "images" / f"{name}.jpg")
            shot_list = {"shots": [{"shot_id": "a", "start": 0, "end": 4, "image_path": "images/a.jpg"},
                                   {"shot_id": "b", "start": 4, "end": 8, "image_path": "images/b.jpg"}]}
            words = [{"text": word, "start": index * 0.5, "end": index * 0.5 + 0.4, "sentence_end": word.endswith(".")}
                     for index, word in enumerate("He said thank you to the voice. It said nothing back at all.".split())]
            segment = {"id": "short_01", "start": 1.0, "end": 7.0, "hook_text": "The best conversation",
                       "end_text": "Full story on the channel"}
            record = render.render_short(folder, segment, words=words, shot_list=shot_list, detector=None,
                                         progress=lambda _: None)
            probe = subprocess.run([FFMPEG, "-i", str(folder / record["video"])], capture_output=True, text=True).stderr
            srt = (folder / record["srt"]).read_text(encoding="utf-8")
            seconds = self.duration(folder / record["video"])
        self.assertIn("1080x1920", probe)
        self.assertAlmostEqual(seconds, 6.0, delta=0.15)
        self.assertEqual(record["shots"], ["a", "b"])
        self.assertIn("00:00:00", srt)
        self.assertNotIn("He said", srt)                                       # words before 1 s are cut away
        self.assertAlmostEqual(record["loudness"]["output_integrated_lufs"], -14.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
