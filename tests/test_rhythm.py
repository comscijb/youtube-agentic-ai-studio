"""Phase 4.2-A: derived views, word-aligned cuts, per-beat pace, the upscaler probe (ComfyUI mocked)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image, ImageDraw

from agents.narrative import atomic_write_json
from video import cut_plan, rhythm, upscale


def picture():
    image = Image.new("RGB", (1280, 720), (40, 40, 45))
    draw = ImageDraw.Draw(image)
    draw.rectangle((520, 150, 760, 700), fill=(150, 120, 100))       # a figure
    draw.ellipse((590, 160, 690, 280), fill=(210, 180, 160))         # its face
    for x in range(900, 1200, 12):                                    # a busy object on the right
        draw.line((x, 400, x + 30, 600), fill=(230, 230, 230), width=2)
    return image


PERSON = [{"box": [520 / 1280, 150 / 720, 760 / 1280, 700 / 720], "face": {"box": [590 / 1280, 160 / 720, 690 / 1280, 280 / 720]}}]


class CropTests(unittest.TestCase):
    def test_full_medium_and_detail_from_the_detector_boxes(self):
        views = rhythm.views_for(picture(), PERSON, upscaled=True)
        self.assertEqual([view["kind"] for view in views], ["full", "medium", "detail"])
        face = PERSON[0]["face"]["box"]
        for view in views[1:]:
            box = view["box"]
            self.assertAlmostEqual(box[2] - box[0], box[3] - box[1], places=3)        # 16:9 kept
            self.assertFalse(rhythm.cuts_face(box, [face]))
            self.assertTrue(rhythm.eye_line_ok(box, [face]))

    def test_without_an_upscaler_crops_stay_at_least_sixty_percent(self):
        for view in rhythm.views_for(picture(), PERSON, upscaled=False)[1:]:
            self.assertGreaterEqual(view["box"][2] - view["box"][0], 0.6 - 1e-6)

    def test_no_face_cut_and_empty_texture_rejected(self):
        self.assertTrue(rhythm.cuts_face([0.5, 0.0, 0.9, 0.4], [[0.45, 0.1, 0.55, 0.3]]))
        self.assertFalse(rhythm.cuts_face([0.4, 0.0, 0.8, 0.4], [[0.45, 0.1, 0.55, 0.3]]))
        flat = Image.new("RGB", (1280, 720), (50, 50, 50))
        self.assertEqual([view["kind"] for view in rhythm.views_for(flat, [], upscaled=True)], ["full"])


class SplitTests(unittest.TestCase):
    WORDS = [{"start": float(t), "end": t + 0.4, "sentence_end": t % 4 == 3} for t in range(0, 30)]

    def test_cuts_land_on_word_boundaries_and_meet_the_beat(self):
        candidates = rhythm.boundaries(self.WORDS)
        views = [{"kind": "full", "box": [0, 0, 1, 1]}, {"kind": "medium", "box": [0.2, 0.2, 0.8, 0.8]}]
        parts = rhythm.split(0.0, 20.0, views, "development", candidates)
        starts = {word["start"] for word in self.WORDS}
        self.assertTrue(all(part["start"] in starts or part["start"] == 0.0 for part in parts))
        self.assertTrue(all(part["end"] - part["start"] <= 5.0 + rhythm.SNAP_SECONDS for part in parts))
        self.assertEqual(parts[0]["kind"], "full")
        self.assertEqual(parts[-1]["end"], 20.0)
        tense = rhythm.split(0.0, 20.0, views, "tension", candidates)
        self.assertGreater(len(tense), len(parts))
        self.assertEqual(tense[2]["motion_variant"], 1)                         # a reused view moves differently

    def test_sentence_starts_and_attention_events_are_preferred(self):
        candidates = rhythm.boundaries(self.WORDS, events={9.0})
        self.assertIn((4.0, True), candidates)                                  # after a sentence end
        self.assertIn((9.0, True), candidates)                                  # an attention event
        self.assertEqual(rhythm._snap(9.3, candidates, 0, 20), 9.0)

    def test_report_against_targets_and_the_final_hold(self):
        rows = [{"title": "A", "beat": "development", "views": [{"start": 0, "end": 4}, {"start": 4, "end": 12}]},
                {"title": "End", "beat": "reflection", "final": True, "views": [{"start": 0, "end": 9}]}]
        found = rhythm.report(rows)
        self.assertFalse(found["scenes"][0]["ok"])                              # an 8 s hold
        self.assertTrue(found["scenes"][1]["ok"])                               # the final image may hold
        self.assertEqual(found["max"], 9)


class UpscaleTests(unittest.TestCase):
    def test_probe_lists_models_or_install_options(self):
        client = MagicMock()
        client.object_info.return_value = {"ImageUpscaleWithModel": {}, "UpscaleModelLoader": {"input": {"required": {
            "model_name": ["COMBO", {"options": ["RealESRGAN_x4plus_anime_6B.pth"]}]}}}}
        found = upscale.probe(client)
        self.assertEqual((found["available"], found["chosen"]), (True, "RealESRGAN_x4plus_anime_6B.pth"))
        client.object_info.return_value = {"ImageUpscaleWithModel": {}}
        empty = upscale.probe(client)
        self.assertFalse(empty["available"])
        self.assertTrue(any("BSD-3" in item["license"] for item in empty["install_options"]))

    def test_ensure_caches_at_twice_the_size(self):
        client = MagicMock()
        client.queue.return_value = "p1"
        client.wait.return_value = {"4": {"images": [{"filename": "x.png"}]}}
        import io
        buffer = io.BytesIO()
        Image.new("RGB", (512, 288)).save(buffer, "PNG")
        client.fetch.return_value = buffer.getvalue()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp, "s.png")
            Image.new("RGB", (128, 72)).save(source)
            found = upscale.ensure(temp, "s01_01", source, client=client, model="m.pth")
            with Image.open(found) as image:
                self.assertEqual(image.size, (256, 144))
            self.assertEqual(upscale.ensure(temp, "s01_01", source, client=client, model="m.pth"), found)
            self.assertEqual(client.queue.call_count, 1)


class ExpandTests(unittest.TestCase):
    def test_a_section_becomes_views_with_unique_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp, "a.png")
            picture().save(image)
            atomic_write_json(Path(temp, "narration_words.json"), {"words": [{"start": float(t), "end": t + 0.4, "sentence_end": t % 5 == 4}
                                                                             for t in range(0, 24)]})
            sections = [{"id": 1, "section_type": "climax", "title": "Turn"}]
            schedule = {1: [{"shot_id": "s01_01", "image": str(image), "shot_type": "medium", "start": 0.0, "end": 24.0,
                             "index": 0, "transition": "cut"}]}
            plan = cut_plan.expand(temp, sections, [24.0], schedule, upscale_probe={"available": False}, detect=lambda *_: PERSON)
        views = plan["schedule"][1]
        self.assertGreaterEqual(len(views), 6)
        self.assertEqual(len({view["shot_id"] for view in views}), len(views))
        self.assertLessEqual(plan["report"]["max"], 7.0)
        self.assertTrue(all(view["transition"] == "cut" for view in views))        # tension cuts hard


if __name__ == "__main__":
    unittest.main()
