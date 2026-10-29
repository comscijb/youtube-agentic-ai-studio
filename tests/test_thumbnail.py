"""Phase 3.0-F: thumbnail uses the validated thumbnail_text and a human focal point."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
from video import thumbnail

SCRIPT = {"title": "The Man Who Needed Everyone to Envy Him", "thumbnail_text": "Are they watching?",
          "psychological_theme": "envy"}


def write_images(root: Path) -> dict:
    images = root / "images"
    images.mkdir()
    paths = {}
    for sid, color in ((1, (40, 40, 40)), (2, (60, 50, 40)), (3, (20, 20, 30))):
        path = images / f"section_{sid:02d}_img01.jpg"
        Image.new("RGB", (1280, 720), color).save(path)
        paths[sid] = [str(path)]
    (root / "asset_manifest.json").write_text(json.dumps({"assets": [
        {"downloaded_filename": "section_01_img01.jpg", "tags": "bedroom, architecture, modern"},
        {"downloaded_filename": "section_02_img01.jpg", "tags": "couple, smiling, happy, man, woman"},
        {"downloaded_filename": "section_03_img01.jpg", "tags": "mirror, man, darkness, sadness"},
    ]}), encoding="utf-8")
    return paths


class ThumbnailTests(unittest.TestCase):
    def render(self, script, brief=None):
        drawn = []
        original = thumbnail._shadow

        def capture(draw, xy, text, font, fill, radius=5):
            drawn.append((text, fill))
            return original(draw, xy, text, font, fill, radius)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = write_images(root)
            with patch.object(thumbnail, "_shadow", side_effect=capture), \
                    redirect_stdout(io.StringIO()) as output:
                out = thumbnail.make_thumbnail(script, paths, temp, brief)
                with Image.open(out) as image:
                    size = image.size
            background = thumbnail.choose_background(paths, temp)
        return drawn, size, Path(background).name, output.getvalue()

    def test_uses_thumbnail_text_not_title(self):
        drawn, size, _, _ = self.render(SCRIPT)
        words = [text for text, _ in drawn]
        self.assertEqual(size, (1280, 720))
        self.assertEqual(words[:3], ["ARE", "THEY", "WATCHING?"])
        self.assertNotIn("ENVY", words)
        self.assertNotIn(config.CHANNEL_NAME.upper(), words)          # no wordmark since Phase 3.6

    def test_no_arbitrary_highlight_only_the_key_word(self):
        drawn, _, _, _ = self.render(SCRIPT)
        fills = {text: fill for text, fill in drawn}
        self.assertTrue(all(fills[word] == config.COLORS["white"]
                            for word in ("ARE", "THEY", "WATCHING?")))
        drawn, _, _, _ = self.render(SCRIPT, {"thumbnail_highlight_word": "watching",
                                              "psychological_theme": "envy"})
        fills = {text: fill for text, fill in drawn}
        self.assertEqual(fills["WATCHING?"], config.COLORS["highlight"])
        self.assertEqual(fills["ARE"], config.COLORS["white"])

    def test_background_prefers_human_emotional_shot(self):
        _, _, background, _ = self.render(SCRIPT)
        self.assertEqual(background, "section_03_img01.jpg")     # not the smiling couple

    def test_brief_text_used_when_script_has_none(self):
        script = dict(SCRIPT, thumbnail_text="")
        drawn, _, _, _ = self.render(script, {"thumbnail_text": "Now what?"})
        self.assertEqual([text for text, _ in drawn][:2], ["NOW", "WHAT?"])

    def test_invalid_thumbnail_text_is_reported(self):
        _, _, _, output = self.render(dict(SCRIPT, thumbnail_text="Envy is everywhere tonight again"))
        self.assertIn("Thumbnail text", output)

    def test_dedicated_generated_thumbnail_image_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = write_images(Path(temp))
            Image.new("RGB", (1280, 720), (10, 10, 10)).save(Path(temp, "thumbnail_bg.jpg"))
            self.assertEqual(Path(thumbnail.choose_background(paths, temp)).name, "thumbnail_bg.jpg")

    def test_fallback_without_manifest_or_images(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(thumbnail.choose_background({}, temp))
            with redirect_stdout(io.StringIO()):
                out = thumbnail.make_thumbnail(SCRIPT, {}, temp)
            self.assertTrue(Path(out).exists())

    def test_gui_delegates_to_the_new_builder(self):
        import gui
        with patch("video.thumbnail.make_thumbnail", return_value="x.jpg") as builder, \
                patch.dict(gui._state, {"research": {"thumbnail_text": "Now what?"}}):
            self.assertEqual(gui._make_thumbnail(SCRIPT, {}, "out"), "x.jpg")
        self.assertEqual(builder.call_args.args[3], {"thumbnail_text": "Now what?"})


if __name__ == "__main__":
    unittest.main()
