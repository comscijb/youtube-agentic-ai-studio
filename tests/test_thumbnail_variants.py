"""Phase 3.6-A: thumbnail concepts, grading, very large text, checks, mobile preview, variants (no live calls)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import config
from agents import packaging
from agents.narrative import NarrativeValidationError, atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from video import thumbnails

BRIEF = {"psychological_theme": "envy", "logline": "A man needs to be envied.", "video_title": "x"}
TITLE = "The Man Who Turned His Wife's Grief Into a Show"
CONCEPTS = {"concepts": [
    {"composition": "big_face", "emotion": "forced smile", "focal_element": "his face", "text": "SMILE FOR THEM",
     "text_side": "left", "curiosity": "Why is he smiling at a funeral?"},
    {"composition": "small_vs_presence", "emotion": "dread", "focal_element": "a crowd of silhouettes",
     "text": "NOBODY LOOKED", "text_side": "right", "curiosity": "Who is he waiting for?"},
    {"composition": "two_person", "emotion": "contempt", "focal_element": "two faces turned away",
     "text": "SHE KNEW", "text_side": "left", "curiosity": "What does she know?", "other_character": "elena_vance"},
]}


def person(box, face=None):
    return {"box": box, "score": 0.9, "face": {"box": face, "score": 0.9} if face else None}


def busy_background(size=(1280, 720)):
    """A bright, busy frame: the worst case for text contrast."""
    image = Image.new("RGB", size, (200, 190, 170))
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 16):
        draw.line([(x, 0), (x, size[1])], fill=(240, 235, 220) if x % 32 else (120, 110, 100), width=6)
    return image


class ConceptTests(unittest.TestCase):
    def test_valid_concepts_pass(self):
        value = packaging.validate_concepts(json.loads(json.dumps(CONCEPTS)), BRIEF, TITLE, ["elena_vance"])
        self.assertEqual([item["composition"] for item in value["concepts"]], ["big_face", "small_vs_presence", "two_person"])

    def test_rules_reject_repeats_long_text_theme_and_title_echo(self):
        bad = json.loads(json.dumps(CONCEPTS))
        bad["concepts"][1]["composition"] = "big_face"
        bad["concepts"][0]["text"] = "THEY ALL WATCHED HIM CRY"                  # 5 words
        bad["concepts"][2]["text"] = "HIS WIFE'S GRIEF"                          # repeats the title
        with self.assertRaises(NarrativeValidationError) as caught:
            packaging.validate_concepts(bad, BRIEF, TITLE, ["elena_vance"])
        message = str(caught.exception)
        for part in ("used twice", "5 words", "repeats the title"):
            self.assertIn(part, message)
        envy = json.loads(json.dumps(CONCEPTS))
        envy["concepts"][0]["text"] = "PURE ENVY"
        with self.assertRaises(NarrativeValidationError):
            packaging.validate_concepts(envy, BRIEF, TITLE, ["elena_vance"])
        self.assertIn("too long", "; ".join(packaging.thumbnail_text_problems("UNMISTAKABLY", BRIEF)))

    def test_concepts_are_generated_once_and_cached(self):
        calls = []

        def generate(prompt, *args, **kwargs):
            calls.append(prompt)
            return json.dumps(CONCEPTS)

        script = {"title": TITLE}
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            first = packaging.generate_thumbnail_concepts(temp, brief=BRIEF, script=script, vb=VISUAL_BIBLE,
                                                          generator=generate)
            again = packaging.generate_thumbnail_concepts(temp, brief=BRIEF, script=script, vb=VISUAL_BIBLE,
                                                          generator=generate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["concepts"], again["concepts"])
        self.assertIn("must NOT repeat the title", calls[0])


class LayoutTests(unittest.TestCase):
    def test_text_is_very_large_and_readable_on_a_busy_background(self):
        layout = thumbnails.layout_text(busy_background(), "NOBODY LOOKED", "left")
        self.assertGreaterEqual(layout["cap_height"], thumbnails.CAP_HEIGHT - 0.005)
        self.assertGreaterEqual(layout["contrast"], thumbnails.MIN_CONTRAST)
        self.assertLess(layout["box"][2], 0.7)

    def test_text_moves_off_a_face(self):
        face = [0.05, 0.2, 0.4, 0.8]
        layout = thumbnails.layout_text(busy_background(), "SHE KNEW", "left", avoid=[face])
        self.assertEqual(layout["side"], "right")
        self.assertTrue(thumbnails.checks(layout, {"persons": [person([0.0, 0.1, 0.45, 1.0], face)]})["text_clear_of_faces"])

    def test_contrast_ratio_matches_wcag(self):
        self.assertAlmostEqual(thumbnails.contrast_ratio((255, 255, 255), (0, 0, 0)), 21.0, places=1)
        self.assertAlmostEqual(thumbnails.contrast_ratio((119, 119, 119), (255, 255, 255)), 4.48, places=1)

    def test_head_cropped_at_the_top_is_flagged(self):
        self.assertFalse(thumbnails.head_in_frame({"persons": [person([0.5, 0.0, 0.9, 1.0], [0.6, 0.0, 0.8, 0.3])]}))
        self.assertTrue(thumbnails.head_in_frame({"persons": [person([0.5, 0.1, 0.9, 1.0], [0.6, 0.12, 0.8, 0.4])]}))
        self.assertIsNone(thumbnails.head_in_frame({"persons": []}))

    def test_grade_keeps_size_and_adds_contrast(self):
        flat = Image.new("RGB", (640, 360), (110, 110, 110))
        ImageDraw.Draw(flat).rectangle([280, 120, 360, 300], fill=(150, 140, 130))
        graded = thumbnails.grade(flat, [0.43, 0.33, 0.57, 0.83], "crimson")
        self.assertEqual(graded.size, flat.size)
        corner, centre = graded.getpixel((5, 5)), graded.getpixel((320, 200))
        self.assertLess(sum(corner), sum(flat.getpixel((5, 5))))       # darker clean background
        self.assertGreater(centre[0] - centre[2], 20)                  # warm/crimson accent on the focal element


class FakeGenerator:
    def __init__(self):
        self.jobs = []

    def generate_with_retry(self, job, new_seeds=1, fallback=None):
        self.jobs.append(job)
        busy_background().save(job.output_path, "JPEG")
        return {"seed": job.seed}


class FakeDetector:
    def detect(self, path):
        return {"persons": [person([0.55, 0.12, 0.95, 1.0], [0.65, 0.15, 0.85, 0.5])]}


class VariantTests(unittest.TestCase):
    def test_three_variants_with_previews_and_selection(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            output = Path(temp)
            atomic_write_json(output / "visual_bible.json", json.loads(json.dumps(VISUAL_BIBLE)))
            atomic_write_json(output / "story_brief.json", BRIEF)
            atomic_write_json(output / "script.json", {"title": TITLE})
            generator = FakeGenerator()
            with patch("agents.visual_bible.approved_reference", return_value="character_refs/julian_vance.png"):
                index = thumbnails.generate_variants(output, generator, concepts=json.loads(json.dumps(CONCEPTS)),
                                                     detector=FakeDetector(), progress=lambda _: None)
            self.assertEqual(len(index["variants"]), 3)
            for variant in index["variants"]:
                with Image.open(output / variant["path"]) as image:
                    self.assertEqual(image.size, (1280, 720))
                with Image.open(output / variant["preview"]) as preview:
                    self.assertEqual(preview.size, (168, 94))
                self.assertGreaterEqual(variant["checks"]["contrast"], 4.5)
            self.assertIn("elena_vance", [ref for ref in generator.jobs[2].prompt.split() if "elena" in ref] or
                          ["elena_vance"])
            self.assertEqual(index["selected"], 1)
            self.assertTrue((output / "thumbnail.jpg").is_file())
            thumbnails.select(output, 2)
            self.assertEqual((output / "thumbnail.jpg").read_bytes(), (output / "thumbnails/variant_2.jpg").read_bytes())
            self.assertEqual(load_json(output / "thumbnails/thumbnails.json")["selected"], 2)
            with self.assertRaises(FileNotFoundError):
                thumbnails.select(output, 9)

    def test_no_wordmark_by_default_and_an_optional_corner_mark(self):
        self.assertFalse(config.THUMBNAIL_LOGO)
        image = Image.new("RGB", (1280, 720), (0, 0, 0))
        marked = thumbnails.logo_mark(image.copy())
        self.assertIsNotNone(marked.crop((1100, 650, 1280, 720)).getbbox())
        self.assertIsNone(marked.crop((0, 0, 1000, 600)).getbbox())


class PartialRestoreTests(unittest.TestCase):
    def test_restores_only_the_needed_files(self):
        from studio import archive, projects
        with tempfile.TemporaryDirectory() as temp:
            root, target = Path(temp, "projects"), Path(temp, "archive", "ep")
            stub = root / "ep"
            stub.mkdir(parents=True)
            atomic_write_json(stub / "project.json", {"slug": "ep", "status": "archived"})
            files = []
            for original, archived, data in (("visual_bible.json", "text/visual_bible.json", b"{}"),
                                             ("character_refs/julian_vance.png", "images/character_refs/julian_vance.png", b"png"),
                                             ("final_video.mp4", "video/final_video.mp4", b"video")):
                (target / archived).parent.mkdir(parents=True, exist_ok=True)
                (target / archived).write_bytes(data)
                files.append({"original": str(Path("projects", "ep", original)), "archive": archived, "action": "move",
                              "sha256": archive.sha256(target / archived)})
            atomic_write_json(target / archive.MANIFEST, {"source": str(Path("projects", "ep")), "files": files})
            with patch.object(projects, "root", return_value=root):
                archive.restore_project("ep", archive_path=target, only=["visual_bible.json", "character_refs"],
                                        log=lambda _: None)
            self.assertTrue((stub / "visual_bible.json").is_file())
            self.assertTrue((stub / "character_refs/julian_vance.png").is_file())
            self.assertFalse((stub / "final_video.mp4").exists())
            data = load_json(stub / "project.json")
        self.assertEqual(data["status"], "archived")
        self.assertEqual(data["partially_restored"], ["character_refs", "visual_bible.json"])


class GuiTests(unittest.TestCase):
    def test_variants_endpoint_and_select(self):
        import gui
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "thumbnails").mkdir()
            busy_background().save(output / "thumbnails/variant_1.jpg")
            busy_background().save(output / "thumbnails/variant_2.jpg")
            atomic_write_json(output / "thumbnails/thumbnails.json", {"selected": 1, "variants": [
                {"number": 1, "path": "thumbnails/variant_1.jpg"}, {"number": 2, "path": "thumbnails/variant_2.jpg"}]})
            with patch.object(config, "OUTPUT_DIR", str(output)), patch.dict(gui._state, {"thumb_path": None}):
                client = gui.app.test_client()
                self.assertEqual(len(client.get("/api/thumbnails").get_json()["variants"]), 2)
                self.assertTrue(client.post("/api/thumbnails/select", json={"number": 2}).get_json()["ok"])
                self.assertEqual(gui._state["thumb_path"], str(output / "thumbnail.jpg"))
                self.assertEqual(client.post("/api/thumbnails/select", json={"number": 7}).status_code, 400)
                self.assertEqual(gui._make_thumbnail({}, {}, str(output)), str(output / "thumbnail.jpg"))


if __name__ == "__main__":
    unittest.main()
