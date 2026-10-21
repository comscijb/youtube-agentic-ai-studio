"""Phase 3.0-E: stock relevance, tone, text and identity filters."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import config
from tests.test_stock import FakeResponse, FakeSession, image_bytes
from video import stock_scoring as scoring
from video.stock import StockManager

NAMES = ("Julian Thorne", "Elena Vasquez", "Marcus Croft")


def text_image(size=(1280, 720)):
    """Three bands of glyph-like strokes on a dark background (no font dependency)."""
    image = Image.new("RGB", size, (25, 25, 25))
    draw = ImageDraw.Draw(image)
    for top in (150, 330, 510):
        for left in range(80, 1200, 24):
            draw.rectangle([left, top, left + 10, top + 60], fill=(235, 235, 235))
    return image


class QueryTests(unittest.TestCase):
    def test_long_descriptions_become_short_concrete_queries(self):
        description = "close up hands straightening linen napkin dark luxury dining table"
        queries = scoring.concise_queries(description, NAMES)
        self.assertTrue(2 <= len(queries) <= 3)
        for query in queries:
            self.assertLessEqual(len(query.split()), 5)
        self.assertIn("linen", queries[0])
        self.assertNotIn("close", " ".join(queries))

    def test_character_shot_drops_people_names_and_leading_verbs(self):
        description = "Julian adjusting woman silk shirt collar mirror reflection intimate lighting"
        self.assertTrue(scoring.is_character_shot(description, NAMES))
        queries = scoring.concise_queries(description, NAMES)
        joined = " ".join(queries)
        for word in ("julian", "woman", "adjusting"):
            self.assertNotIn(word, joined)
        self.assertTrue(queries[0].startswith("silk shirt collar"))

    def test_environment_shot_is_not_a_character_shot(self):
        self.assertFalse(scoring.is_character_shot(
            "minimalist loft kitchen dramatic side lighting", NAMES))

    def test_relevance_uses_simple_stemming(self):
        self.assertEqual(scoring.relevance("wine glass table", "wine, glasses, tables"), 1.0)
        self.assertAlmostEqual(scoring.relevance("linen napkin dining table", "ship, sea"), 0.0)
        self.assertEqual(scoring.relevance("", "anything"), 0.0)


class RejectionTests(unittest.TestCase):
    def test_text_and_cheerful_tags_are_rejected(self):
        self.assertIn("text/signage", scoring.tag_rejection("street, sign, parking"))
        self.assertIn("wallpaper", scoring.tag_rejection("wine, hd wallpaper"))
        self.assertIn("cheerful", scoring.tag_rejection("men, beers, cheers, toast"))
        self.assertIsNone(scoring.tag_rejection("candle, smoke, dark"))

    def test_people_only_rejected_in_character_shots_unless_identity_safe(self):
        self.assertIn("identifiable person",
                      scoring.tag_rejection("woman, portrait, kitchen", character_shot=True))
        self.assertIsNone(scoring.tag_rejection("woman, hands, kitchen", character_shot=True))
        self.assertIsNone(scoring.tag_rejection("man, silhouette, window", character_shot=True))
        self.assertIsNone(scoring.tag_rejection("woman, kitchen", character_shot=False))

    def test_tone_rules(self):
        bright = scoring.tone(Image.new("RGB", (200, 100), (240, 230, 200)))
        candle = scoring.tone(Image.new("RGB", (200, 100), (90, 40, 5)))
        cheerful = scoring.tone(Image.new("RGB", (200, 100), (250, 200, 0)))
        self.assertIn("too bright", scoring.tone_rejection(bright))
        self.assertIsNone(scoring.tone_rejection(candle))           # dark and saturated is fine
        self.assertIsNotNone(scoring.tone_rejection(cheerful))
        with patch.object(config, "STOCK_TONE_FILTER", False):
            self.assertIsNone(scoring.tone_rejection(bright))

    def test_text_heuristic(self):
        self.assertGreaterEqual(scoring.text_likelihood(text_image()), 0.4)
        self.assertIsNotNone(scoring.text_rejection(text_image()))
        noise = Image.effect_noise((1280, 720), 70).convert("RGB")
        gradient = Image.linear_gradient("L").resize((1280, 720)).convert("RGB")
        plain = Image.new("RGB", (1280, 720), (30, 30, 30))
        for image in (noise, gradient, plain):
            self.assertEqual(scoring.text_likelihood(image), 0.0)


def hit(asset_id, tags, url=None):
    return {"id": asset_id, "largeImageURL": url or f"https://cdn.example/{asset_id}.jpg",
            "pageURL": f"https://pixabay.com/photos/{asset_id}/", "user": "P",
            "imageWidth": 1280, "imageHeight": 720, "type": "photo", "tags": tags}


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


class ManagerIntegrationTests(unittest.TestCase):
    def run_acquire(self, hits, contents, query, names=NAMES):
        queries = []

        def handler(url, kwargs):
            if "pixabay.com/api" in url:
                queries.append(kwargs["params"]["q"])
                return FakeResponse(payload={"hits": hits})
            return FakeResponse(content=contents[url], headers={"Content-Type": "image/jpeg"})

        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "PIXABAY_API_KEY", "k"):
            manager = StockManager(tmp, session=FakeSession(handler), sleep_fn=lambda _: None,
                                   provider_name="pixabay", fallback_name="none",
                                   character_names=names, cache_enabled=False)
            with redirect_stdout(io.StringIO()):
                path = manager.acquire(query, "landscape", 3, 1)
            manager.write_manifest()
            manifest = json.loads(Path(tmp, "asset_manifest.json").read_text(encoding="utf-8"))
        return path, manifest, queries

    def test_rejects_text_bright_and_irrelevant_then_records_scores(self):
        hits = [
            hit("1", "linen, napkin, sign, text"),
            hit("2", "linen, napkin, table"),                     # bright -> rejected after download
            hit("3", "ship, sea, waves"),                          # irrelevant
            hit("4", "linen, napkin, dining, table, dark"),
        ]
        contents = {
            "https://cdn.example/1.jpg": image_bytes(),
            "https://cdn.example/2.jpg": png(Image.new("RGB", (1280, 720), (245, 245, 240))),
            "https://cdn.example/3.jpg": image_bytes(),
            "https://cdn.example/4.jpg": image_bytes(color=(35, 30, 28)),
        }
        path, manifest, queries = self.run_acquire(
            hits, contents, "close up hands straightening linen napkin dark luxury dining table")
        self.assertTrue(path.endswith("section_03_img01.jpg"))
        asset = manifest["assets"][0]
        self.assertEqual(asset["asset_id"], "4")
        selection = asset["selection"]
        self.assertGreaterEqual(selection["relevance"], 0.4)
        self.assertEqual(selection["rejected_candidates"], 3)
        self.assertIn("luminance", selection)
        reasons = " | ".join(item["reason"] for item in manifest["rejected_candidates"])
        for expected in ("text/signage", "too bright", "relevance"):
            self.assertIn(expected, reasons)
        self.assertTrue(all(len(query.split()) <= 5 for query in queries))

    def test_character_shot_never_uses_identifiable_people(self):
        hits = [hit("5", "woman, portrait, silk, shirt, mirror"),
                hit("6", "silk, shirt, collar, mirror, hands")]
        contents = {f"https://cdn.example/{i}.jpg": image_bytes() for i in (5, 6)}
        _, manifest, _ = self.run_acquire(
            hits, contents, "Julian adjusting woman silk shirt collar mirror reflection")
        self.assertEqual(manifest["assets"][0]["asset_id"], "6")
        self.assertTrue(manifest["assets"][0]["selection"]["character_shot"])
        self.assertIn("identifiable person", manifest["rejected_candidates"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
