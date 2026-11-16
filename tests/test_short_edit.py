"""Phase 4.3-D: the 9:16 edit — first frame, view cap, captions in the safe area, CTA card, The Line's loop."""
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import config
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import short_edit as edit
from video import shorts_render as base


def picture(path: Path, colour=(120, 90, 60), detail=True, size=(1280, 720)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, colour)
    if detail:
        draw = ImageDraw.Draw(image)
        for x in range(0, size[0], 40):
            draw.line([(x, 0), (x + 200, size[1])], fill=(230, 220, 200), width=3)
    image.save(path)
    return str(path)


def words(count=20, gap=0.4, start=0.3):
    return [{"text": f"word{index}", "start": round(start + index * gap, 3), "end": round(start + index * gap + 0.3, 3),
             "sentence_end": index % 6 == 5} for index in range(count)]


class FirstFrameTests(unittest.TestCase):
    def test_never_black_and_prefers_the_face(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            black = {"image": picture(folder / "black.jpg", (2, 2, 2), detail=False)}
            flat = {"image": picture(folder / "flat.jpg", (128, 128, 128), detail=False)}
            scene = {"image": picture(folder / "scene.jpg")}
            face = {"image": picture(folder / "face.jpg", (90, 70, 60))}
            detections = {face["image"]: {"persons": [{"box": [0.3, 0.1, 0.7, 0.9], "face": {"box": [0.4, 0.15, 0.6, 0.45]}}]}}
            candidates = [black, flat, scene, face]
            self.assertEqual(edit.first_image(candidates, lambda path: detections.get(path)), 3)
            self.assertEqual(edit.first_image([black, flat, scene]), 2)
            self.assertFalse(edit.usable(Image.open(black["image"])))
            self.assertFalse(edit.usable(Image.open(flat["image"])))


class ScheduleTests(unittest.TestCase):
    def sources(self, count=3):
        return [{"views": [{"framing": "full"}, {"framing": "medium"}]} for _ in range(count)]

    def test_no_view_over_three_seconds(self):
        for duration in (12.0, 23.7, 41.0):
            pieces = edit.schedule(duration + 1.8, words(int(duration / 0.4)), self.sources(), 0, card_start=duration)
            self.assertLessEqual(edit.longest_hold(pieces), edit.MAX_HOLD)
            self.assertEqual(pieces[0]["start"], 0.0)
            self.assertAlmostEqual(pieces[-1]["end"], duration)
            for before, after in zip(pieces, pieces[1:]):
                self.assertNotEqual((before["source"], before["view"]), (after["source"], after["view"]))

    def test_cuts_sit_on_word_starts(self):
        spoken = words(30, gap=0.45)
        onsets = {word["start"] for word in spoken}
        cuts = edit.cut_times(0.0, 13.0, spoken)
        self.assertTrue(cuts)
        self.assertTrue(all(cut in onsets for cut in cuts[:-1]))
        gaps = [b - a for a, b in zip([0.0] + cuts, cuts + [13.0])]
        self.assertLessEqual(max(gaps), edit.MAX_HOLD)

    def test_the_first_piece_is_the_first_image(self):
        pieces = edit.schedule(10.0, words(), self.sources(), 2, card_start=8.0)
        self.assertEqual(pieces[0]["source"], 2)
        self.assertEqual(pieces[0]["view"], "full")

    def test_loop_ends_on_the_first_view(self):
        pieces = edit.schedule(15.0, words(), self.sources(), 1, card_start=None, loop=True)
        self.assertTrue(pieces[-1]["loop_return"])
        self.assertEqual(pieces[-1]["source"], 1)
        self.assertAlmostEqual(pieces[-1]["end"] - pieces[-1]["start"], edit.LOOP_SECONDS)


class LoopTests(unittest.TestCase):
    def test_last_frame_matches_the_first(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            candidates = [{"shot_id": f"s{n}", "image": picture(folder / f"{n}.jpg", (40 * n + 60, 80, 120)), "upscaled": ""}
                          for n in range(3)]
            sources = edit.prepare_sources(candidates, {}, None)
            pieces = edit.schedule(14.0, words(), sources, 0, card_start=None, loop=True)
            self.assertLess(edit.loop_difference(sources, pieces, 14.0), 0.01)
            moving = edit.frame_at(sources, pieces, 0.0), edit.frame_at(sources, pieces, pieces[0]["end"] - 0.01)
            self.assertGreater(np.abs(np.asarray(moving[0], dtype=float) - np.asarray(moving[1], dtype=float)).mean(), 0.5)


class CaptionTests(unittest.TestCase):
    def test_one_to_three_words_and_the_current_word(self):
        spoken = words(14)
        events = edit.caption_events(spoken, {2}, end_limit=99.0)
        self.assertTrue(all(1 <= len(event["words"]) <= 3 for event in events))
        self.assertTrue(all(sum(word["current"] for word in event["words"]) == 1 for event in events))
        self.assertEqual(len(events), len(spoken))
        self.assertTrue(any(word["key"] for event in events for word in event["words"]))

    def test_key_words(self):
        spoken = [{"text": "The", "start": 0, "end": 0.1}, {"text": "baker", "start": 0.2, "end": 0.3},
                  {"text": "Tomas", "start": 0.4, "end": 0.5}, {"text": "remembered", "start": 0.6, "end": 0.7}]
        keys = edit.key_words({"on_screen_hook": "The baker"}, spoken)
        self.assertEqual(keys, {1, 2, 3})

    def test_captions_stay_in_the_safe_area(self):
        font = base._font(edit.CAPTION_SIZE)
        spoken = [{"text": text, "start": index * 0.3, "end": index * 0.3 + 0.25}
                  for index, text in enumerate("Everyone remembered it and nobody ever forgave the baker afterwards".split())]
        for event in edit.caption_events(spoken, set(), end_limit=99.0):
            line = " ".join(word["text"] for word in event["words"])
            self.assertTrue(edit.caption_box_ok(line, font), line)
        margins = base.caption_margins()
        self.assertGreaterEqual(margins["margin_bottom"], base.H * base.SAFE_BOTTOM)

    def test_ass_has_gold_and_highlight(self):
        with tempfile.TemporaryDirectory() as temp:
            events = edit.caption_events(words(4), {1}, end_limit=9.0)
            text = Path(edit.write_captions(events, Path(temp) / "c.ass", None)).read_text(encoding="utf-8")
        gold = config.COLORS["highlight"]
        self.assertIn(f"&H00{gold[2]:02X}{gold[1]:02X}{gold[0]:02X}", text)
        self.assertIn("\\fscx110", text)
        self.assertIn("Alignment" , text)


class CardTests(unittest.TestCase):
    def test_cta_text(self):
        self.assertEqual(edit.cta_lines({"format": "hook"}), ["Full story on the channel ↑"])
        self.assertEqual(edit.cta_lines({"format": "dilemma", "question": "What would you do?"}),
                         ["What would you do?", "Full story on the channel ↑"])
        self.assertNotIn("Part 2", " ".join(edit.cta_lines({"format": "countdown", "question": "Part 2?"})))

    def test_thumbnail_background_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            fallback = picture(Path(temp) / "a.jpg")
            card = edit.thumbnail_background(temp, fallback)
            self.assertEqual(card.size, (edit.W, edit.H))


def tone(path: Path, seconds: float, rate=44100):
    t = np.arange(int(seconds * rate)) / rate
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.sin(2 * np.pi * 330 * t) * 8000).astype(np.int16).tobytes())


class RenderTests(unittest.TestCase):
    def test_small_render(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "VIDEO_FILM_EFFECT", "off"):
            folder = Path(temp)
            for n in range(2):
                picture(folder / "images" / "shots" / f"s0{n}.jpg", (60 + 50 * n, 90, 110))
            shot_list = {"shots": [{"shot_id": f"s0{n}", "section_id": 2, "image_path": f"images/shots/s0{n}.jpg",
                                    "start": 0, "end": 5} for n in range(2)]}
            (folder / "shorts").mkdir()
            tone(folder / "shorts" / "short_01.wav", 3.3)
            item = {"id": "short_01", "format": "hook", "on_screen_hook": "They heard", "source_scene_ids": ["2"]}
            voice = {"duration": 2.0, "words": words(4), "cta_start": None}
            audio = {"audio": str(folder / "shorts" / "short_01.wav"), "plan": {"duration": 3.3, "missing": [], "music": []},
                     "loudness": {}}
            record = edit.render(folder, item, voice, audio, shot_list=shot_list, progress=lambda message: None)
            self.assertTrue((folder / record["video"]).is_file())
            self.assertLessEqual(record["longest_hold"], edit.MAX_HOLD)
            self.assertTrue((folder / "shorts" / "short_01.ass").is_file())


if __name__ == "__main__":
    unittest.main()
