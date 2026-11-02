"""Phase 3.8-E: thumbnail regeneration — new seeds appended, gallery, progress and errors, project scoping."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import config
from agents.narrative import atomic_write_json, load_json
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_shot_planning import VISUAL_BIBLE
from tests.test_thumbnail_variants import BRIEF, CONCEPTS, TITLE, FakeDetector, FakeGenerator
from video import thumbnails

APPROVED = "agents.visual_bible.approved_reference"


def make_project(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write_json(folder / "visual_bible.json", json.loads(json.dumps(VISUAL_BIBLE)))
    atomic_write_json(folder / "story_brief.json", BRIEF)
    atomic_write_json(folder / "script.json", {"title": TITLE})
    return folder


def more(output, generator, **kwargs):
    with patch(APPROVED, return_value="character_refs/julian_vance.png"), redirect_stdout(io.StringIO()):
        return thumbnails.generate_variants(output, generator, concepts=json.loads(json.dumps(CONCEPTS)),
                                            detector=FakeDetector(), progress=lambda _: None, **kwargs)


class GalleryTests(unittest.TestCase):
    def test_more_options_appends_new_seeds_and_keeps_the_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            output = make_project(Path(temp))
            generator = FakeGenerator()
            first = more(output, generator)
            thumbnails.select(output, 2)
            before = (output / "thumbnails/variant_1.jpg").stat().st_mtime_ns
            index = more(output, generator)
            self.assertEqual((output / "thumbnails/variant_1.jpg").stat().st_mtime_ns, before)   # never overwritten
            self.assertTrue((output / "thumbnails/variant_6.jpg").is_file())
        self.assertEqual([item["number"] for item in first["variants"]], [1, 2, 3])
        self.assertEqual([item["number"] for item in index["variants"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual([item["round"] for item in index["variants"]], [1, 1, 1, 2, 2, 2])
        old, new = generator.jobs[:3], generator.jobs[3:]
        self.assertTrue(all(a.prompt == b.prompt and a.seed != b.seed for a, b in zip(old, new)))
        self.assertEqual(index["selected"], 2)                                                # selection survives
        self.assertEqual(index["variants"][3]["concept_key"], index["variants"][0]["concept_key"])

    def test_regenerate_links_the_parent_and_archive_hides(self):
        with tempfile.TemporaryDirectory() as temp:
            output = make_project(Path(temp))
            generator = FakeGenerator()
            more(output, generator)
            with patch(APPROVED, return_value="x.png"), redirect_stdout(io.StringIO()):
                index = thumbnails.regenerate_variant(output, generator, 3, detector=FakeDetector(),
                                                      progress=lambda _: None)
            child = index["variants"][-1]
            self.assertEqual((child["number"], child["parent"], child["concept"]),
                             (4, 3, index["variants"][2]["concept"]))
            self.assertNotEqual(generator.jobs[-1].seed, generator.jobs[2].seed)
            thumbnails.archive_variant(output, 2)
            with self.assertRaises(ValueError):
                thumbnails.archive_variant(output, 1)                                         # the selected one
            saved = load_json(output / "thumbnails/thumbnails.json")
            self.assertTrue((output / "thumbnails/variant_2.jpg").is_file())                 # files kept
        self.assertEqual([item["number"] for item in saved["variants"] if item["archived"]], [2])

    def test_new_concepts_are_forced(self):
        with tempfile.TemporaryDirectory() as temp:
            output = make_project(Path(temp))
            with patch.object(thumbnails.packaging, "generate_thumbnail_concepts",
                              return_value=json.loads(json.dumps(CONCEPTS))) as concepts, \
                    patch(APPROVED, return_value="x.png"), redirect_stdout(io.StringIO()):
                thumbnails.generate_variants(output, FakeGenerator(), detector=FakeDetector(), progress=lambda _: None,
                                             new_concepts=True)
        self.assertTrue(concepts.call_args.kwargs["force"])


class ComfyGenerator(FakeGenerator):
    client = None                                     # read by the QA, which is mocked here


class Deferred:
    """threading.Thread stand-in: the job runs when the test says so (after switching projects, say)."""
    pending = []

    def __init__(self, target=None, daemon=None, **kwargs):
        self.target = target

    def start(self):
        Deferred.pending.append(self.target)


class GuiTests(unittest.TestCase):
    def setUp(self):
        import gui
        self.gui = gui
        self.client = gui.app.test_client()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        Deferred.pending = []
        self.patches = [patch.object(gui.threading, "Thread", Deferred),
                        patch.dict(gui._state, {"imagegen_busy": False, "running": False, "thumb_error": None,
                                                "thumb_progress": ""}),
                        patch("video.imagegen_comfyui.ImageGenerator", lambda output: ComfyGenerator()),
                        patch("video.qa.layered.LayeredQA", lambda **kwargs: None),
                        patch("video.qa.fixes._detector", return_value=FakeDetector()),
                        patch.object(thumbnails.packaging, "generate_thumbnail_concepts",
                                     side_effect=lambda *a, **k: json.loads(json.dumps(CONCEPTS)))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_jobs(self):
        with redirect_stdout(io.StringIO()):
            while Deferred.pending:
                Deferred.pending.pop(0)()

    def test_variants_land_in_the_project_open_when_the_button_was_pressed(self):
        first, second = make_project(self.root / "a"), make_project(self.root / "b")
        with patch.object(config, "OUTPUT_DIR", str(first)):
            self.assertTrue(self.client.post("/api/thumbnails/generate", json={}).get_json()["ok"])
        with patch.object(config, "OUTPUT_DIR", str(second)), patch(APPROVED, return_value="x.png"):
            self.run_jobs()                                       # the user opened project b meanwhile
            listed = self.client.get("/api/thumbnails").get_json()
        self.assertEqual(len(load_json(first / "thumbnails/thumbnails.json")["variants"]), 3)
        self.assertFalse((second / "thumbnails").exists())
        self.assertEqual((listed["project"], listed["variants"]), ("b", []))

    def test_progress_errors_busy_and_the_legacy_endpoint(self):
        output = make_project(self.root / "a")
        with patch.object(config, "OUTPUT_DIR", str(output)):
            self.client.post("/api/thumbnails/generate", json={"new_concepts": True})
            self.run_jobs()                                       # no approved portrait: ApprovalRequired
            error = self.client.get("/api/thumbnails").get_json()["last_error"]
            self.assertTrue(error["approval"])
            self.assertIn("Approve julian_vance", error["message"])
            self.assertTrue(thumbnails.packaging.generate_thumbnail_concepts.call_args.kwargs["force"])
            self.gui._state["imagegen_busy"] = True
            busy = self.client.post("/api/thumbnails/generate", json={})
            self.assertEqual(busy.status_code, 409)
            self.gui._state["imagegen_busy"] = False
            self.gui._state["script"] = {"title": "Old"}
            legacy = self.client.post("/api/regenerate-thumbnail", json={"title": "New Title"})
            self.assertTrue(legacy.get_json()["ok"])
            with patch(APPROVED, return_value="x.png"):
                self.run_jobs()
            status = self.client.get("/api/thumbnails").get_json()
            self.assertEqual(self.client.post("/api/thumbnails/regenerate", json={"number": 9}).status_code, 404)
        self.assertEqual(len(status["variants"]), 3)                 # the legacy button made variants
        self.assertIsNone(status["last_error"])
        self.assertEqual(self.gui._state["script"]["title"], "New Title")
        self.assertFalse(hasattr(self.gui, "_make_thumbnail"))

    def test_page_polls_and_refreshes_the_gallery(self):
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        action = page[page.index("function thumbAction("):page.index("function thumbButton(")]
        self.assertIn("setTimeout(loadThumbVariants", action)
        self.assertIn("Outra geração em andamento", action)
        gallery = page[page.index("function loadThumbVariants() {"):page.index("function loadTitleScores() {")]
        self.assertIn("setTimeout(loadThumbVariants, 3000)", gallery)
        for text in ("Gerar de novo", "Arquivar", "Usar esta", "d.last_error", "Aprovar referências", "b.number - a.number"):
            self.assertIn(text, gallery)
        self.assertIn("thumbAction('/api/thumbnails/generate', {})\"", page)
        self.assertIn("Mais opções", page)
        self.assertIn("Escolher outra miniatura", page)
        self.assertNotIn("Gerar variantes", page)


if __name__ == "__main__":
    unittest.main()
