"""Phase 3.9.4-D: images edited outside come back at any size, checked and versioned."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from uploader import editorial_record
from video import external_images as external
from video.qa import fixes

SHOT = {"shot_id": "s01_01", "image_path": "images/shots/s01_01.jpg", "seed": 1}


def picture(size, colour=(80, 70, 60)):
    rng = np.random.default_rng(2)
    noise = rng.integers(-8, 8, (size[1], size[0], 3))
    return Image.fromarray(np.clip(np.array(colour) + noise, 0, 255).astype(np.uint8))


class FitTests(unittest.TestCase):
    def test_exact_16x9_is_resized(self):
        fitted, info = external.fit_16x9(picture((2048, 1152)))
        self.assertEqual(fitted.size, (1920, 1080))
        self.assertEqual(info["fit"], "center")
        self.assertFalse(info["upscaled"])

    def test_3x2_and_square_ask_first(self):
        for size in ((1536, 1024), (1024, 1024)):
            with self.assertRaises(external.NeedsChoice) as caught:
                external.fit_16x9(picture(size))
            self.assertEqual(caught.exception.size, size)

    def test_within_8_percent_is_cropped_without_asking(self):
        fitted, info = external.fit_16x9(picture((1672, 1000)))        # 5,9 % away
        self.assertEqual(fitted.size, (1920, 1080))
        self.assertEqual(info["fit"], "center")
        self.assertTrue(info["upscaled"])
        self.assertEqual(info["window"][1] + info["window"][3], 1000)  # centred

    def test_crop_position_slides_along_the_free_axis(self):
        self.assertEqual(external.crop_window((1024, 1024), 0), (0, 0, 1024, 576))
        self.assertEqual(external.crop_window((1024, 1024), 1), (0, 448, 1024, 1024))
        self.assertEqual(external.crop_window((2400, 1000), 0)[0], 0)
        self.assertEqual(external.crop_window((2400, 1000), 1)[2], 2400)

    def test_crop_mode_and_blur_mode(self):
        image = picture((1024, 1024))
        ImageDraw.Draw(image).rectangle((0, 0, 1023, 400), fill=(255, 0, 0))
        top, info = external.fit_16x9(image, "crop", 0)
        self.assertEqual((top.size, info["fit"]), ((1920, 1080), "crop"))
        self.assertGreater(top.getpixel((960, 200))[0], 200)
        blurred, info = external.fit_16x9(picture((1536, 1024)), "blur")
        self.assertEqual((blurred.size, info["fit"]), ((1920, 1080), "blur"))
        whole = external.blur_fill(Image.new("RGB", (1024, 1024), (255, 255, 255)))
        self.assertEqual(whole.getpixel((960, 540)), (255, 255, 255))     # the image itself is whole
        self.assertLess(whole.getpixel((10, 540))[0], 255)                # the sides are the darkened fill


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        (self.output / "images/shots").mkdir(parents=True)
        picture((1920, 1080)).save(self.output / SHOT["image_path"], quality=95)
        self.shot = json.loads(json.dumps(SHOT))

    def tearDown(self):
        self.temp.cleanup()

    def file(self, size=(2048, 1152), name="edited.png", image=None):
        path = self.output / name
        (image or picture(size, (150, 150, 150))).save(path)
        return path

    def run_import(self, **kwargs):
        path = self.file()
        return external.import_image(self.output, self.shot, path, "edited.png", anchors=[], **kwargs)

    def test_a_new_version_with_the_original_kept(self):
        before = (self.output / SHOT["image_path"]).read_bytes()
        path = self.file()
        action = external.import_image(self.output, self.shot, path, "edited.png", anchors=[])
        self.assertEqual(self.shot["source"], "external")
        self.assertEqual(action["replaced_version"], 1)
        history = self.shot["qa"]["history"]
        self.assertEqual((self.output / history[0]["path"]).read_bytes(), before)
        self.assertEqual((self.output / action["original"]).read_bytes(), path.read_bytes())
        self.assertIn("history/", action["original"])
        with Image.open(self.output / SHOT["image_path"]) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertGreater(image.convert("RGB").getpixel((5, 5))[0], 120)
        manifest = load_json(self.output / "asset_manifest.json")
        self.assertEqual(manifest["generated_assets"][0]["source"], "external")

    def test_nothing_changes_when_a_choice_is_needed(self):
        before = (self.output / SHOT["image_path"]).read_bytes()
        with self.assertRaises(external.NeedsChoice):
            external.import_image(self.output, self.shot, self.file((1024, 1024)), "edited.png", anchors=[])
        self.assertEqual((self.output / SHOT["image_path"]).read_bytes(), before)
        self.assertNotIn("source", self.shot)
        self.assertNotIn("qa", self.shot)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            external.import_image(self.output, self.shot, self.file(), "edited.gif", anchors=[])
        junk = self.output / "junk.png"
        junk.write_bytes(b"nope")
        with self.assertRaises(ValueError):
            external.import_image(self.output, self.shot, junk, "junk.png", anchors=[])

    def test_margin_is_flagged_on_import(self):
        framed = picture((1920, 1080), (60, 60, 60))
        ImageDraw.Draw(framed).rectangle((0, 0, 1919, 40), fill=(245, 240, 230))
        ImageDraw.Draw(framed).rectangle((0, 0, 40, 1079), fill=(245, 240, 230))
        action = external.import_image(self.output, self.shot, self.file(image=framed), "edited.png", anchors=[])
        self.assertNotEqual(action["checks"]["margin"], "clean")
        self.assertEqual(self.shot["image_flags"], [external.borders.LABEL])

    def test_people_count_from_the_detector(self):
        detector = type("D", (), {"detect": staticmethod(lambda path: {"persons": [{"box": [0, 0, 1, 1]}]})})()
        self.assertEqual(self.run_import(detector=detector)["checks"]["people"], 1)

    def test_a_later_edit_drops_the_flag_and_restoring_brings_it_back(self):
        self.run_import()
        image = self.output / SHOT["image_path"]
        saved = fixes.save_version(self.output, self.shot, image)
        self.assertEqual(saved["source"], "external")
        self.assertNotIn("source", self.shot)
        self.assertIsNone(load_json(self.output / "asset_manifest.json")["generated_assets"][0]["source"])
        fixes.restore_version(self.output, self.shot, image, saved["version"])
        self.assertEqual(self.shot["source"], "external")

    def test_export_full_and_crop(self):
        full = external.export_image(self.output, self.shot)
        self.assertEqual(full.relative_to(self.output).as_posix(), "exports/s01_01.png")
        with Image.open(full) as image:
            self.assertEqual(image.size, (1920, 1080))
        crop = external.export_image(self.output, self.shot, [.4, .4, .5, .5])
        with Image.open(crop) as image:
            self.assertLess(image.width, 1920)


class RouteTests(unittest.TestCase):
    def test_import_routes_mark_the_video_stale_and_ask_first(self):
        import config
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch("video.qa.fixes._detector", return_value=None), patch.object(gui, "_open_folder") as opened, \
                patch("video.qa.copy_check.boards", return_value=[]):
            output = Path(temp)
            (output / "images/shots").mkdir(parents=True)
            picture((1920, 1080)).save(output / SHOT["image_path"], quality=95)
            atomic_write_json(output / "shot_list.json", {"shots": [json.loads(json.dumps(SHOT))]})
            atomic_write_json(output / "visual_bible.json", {})
            (output / "final_video.mp4").write_bytes(b"x")
            client = gui.app.test_client()

            def send(size, **form):
                buffer = io.BytesIO()
                picture(size).save(buffer, "PNG")
                buffer.seek(0)
                return client.post("/api/imagegen/shot/s01_01/import", data={"file": (buffer, "edited.png"), **form},
                                   content_type="multipart/form-data")
            asked = send((1536, 1024))
            self.assertEqual(asked.status_code, 409)
            self.assertEqual(asked.get_json()["width"], 1536)
            self.assertTrue((output / "final_video.mp4").exists())
            done = send((1536, 1024), mode="blur")
            self.assertTrue(done.get_json()["ok"])
            self.assertFalse((output / "final_video.mp4").exists())
            shot = load_json(output / "shot_list.json")["shots"][0]
            self.assertEqual(shot["source"], "external")
            exported = client.post("/api/imagegen/shot/s01_01/export", json={}).get_json()
            self.assertEqual(exported["path"], "exports/s01_01.png")
            opened.assert_called_once()


class RecordTests(unittest.TestCase):
    def test_editorial_record_lists_imported_shots(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            atomic_write_json(output / "shot_list.json", {"shots": [
                {"shot_id": "a", "source": "external"}, {"shot_id": "b"}]})
            record = editorial_record.build(output)
        self.assertEqual(record["shots_imported"], ["a"])
        self.assertIn("1 edited outside", editorial_record.summary(record))


if __name__ == "__main__":
    unittest.main()
